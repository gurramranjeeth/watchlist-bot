"""Watchlist Telegram bot: news + interviews for your stocks, people and topics.

Commands
  /add TCS, NVDA, Jensen Huang, AI chips   add one or more items (comma-separated)
  /remove NVDA                             remove an item
  /list                                    show your watchlist
  /news [item]                             latest developments now (all items, or one)
  /interviews [item]                       latest interviews now (all items, or one)
  /pause, /resume                          stop / restart automatic alerts
  /help
"""
import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from sources import fetch_interviews, fetch_news, looks_like_ticker
from storage import Store
from summarizer import Summarizer

# ---------------------------------------------------------------- config ----
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY") or None
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
INTERVAL_MIN = int(os.getenv("CHECK_INTERVAL_MIN", "20"))
REGION = os.getenv("NEWS_REGION", "en-IN")          # e.g. en-US, en-GB
DB_PATH = os.getenv("DB_PATH", "watchlist.db")
MAX_ITEMS = int(os.getenv("MAX_WATCHLIST_ITEMS", "25"))
# Comma-separated Telegram chat IDs allowed to use the bot. Empty = anyone.
# Set this! Otherwise strangers can run up your Claude bill.
ALLOWED = {int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x}

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

store = Store(DB_PATH)
summarizer = Summarizer(ANTHROPIC_KEY, MODEL)
http = httpx.AsyncClient(follow_redirects=True)
fetch_sem = asyncio.Semaphore(4)                   # be polite to news sources


# --------------------------------------------------------------- helpers ----
def allowed(update: Update) -> bool:
    return not ALLOWED or update.effective_chat.id in ALLOWED


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def fresh(items: list[dict], days: int = 2) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    keep = [it for it in items if not it["published"] or it["published"] >= cutoff]
    keep.sort(key=lambda it: it["published"] or cutoff, reverse=True)
    return keep


async def gather_item(name: str, kind: str) -> tuple[list[dict], list[dict]]:
    async with fetch_sem:
        news, intv = await asyncio.gather(
            fetch_news(http, name, kind, REGION),
            fetch_interviews(http, name, kind, REGION, YOUTUBE_KEY),
            return_exceptions=True,
        )
    news = fresh(news) if isinstance(news, list) else []
    intv = fresh(intv, days=3) if isinstance(intv, list) else []
    # an interview story shouldn't also show up as plain news
    intv_urls = {i["url"] for i in intv}
    news = [n for n in news if n["url"] not in intv_urls]
    return news, intv


async def render_section(name: str, kind: str, section: str, items: list[dict], icon: str) -> str:
    if not items:
        return ""
    s = await summarizer.summarize(name, kind, section, items[:6])
    if not s["relevant"]:
        return ""
    lines = [f"{icon} <b>{esc(section.capitalize())}</b>"]
    for it, takeaway in s["relevant"]:
        src = f" — <i>{esc(it['source'])}</i>" if it["source"] else ""
        lines.append(f'• <a href="{esc(it["url"])}">{esc(it["title"])}</a>{src}')
        if takeaway:
            lines.append(f"   ↳ {esc(takeaway)}")
    if s["why_it_matters"]:
        lines.append(f"💡 {esc(s['why_it_matters'])}")
    return "\n".join(lines)


async def build_report(name: str, kind: str, news: list[dict], intv: list[dict],
                       want=("news", "interviews")) -> str:
    parts = []
    if "news" in want:
        parts.append(await render_section(name, kind, "latest developments", news, "📰"))
    if "interviews" in want:
        parts.append(await render_section(name, kind, "interviews", intv, "🎙"))
    parts = [p for p in parts if p]
    if not parts:
        return ""
    tag = "📈" if kind == "stock" else "🔎"
    return f"{tag} <b>{esc(name)}</b>\n" + "\n\n".join(parts)


async def send_long(bot, chat_id: int, text: str):
    """Telegram caps messages at 4096 chars; split on blank lines."""
    chunk = ""
    for block in text.split("\n\n"):
        if len(chunk) + len(block) + 2 > 3900 and chunk:
            await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True)
            chunk = ""
        chunk += ("\n\n" if chunk else "") + block
    if chunk:
        await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML,
                               disable_web_page_preview=True)


def pick_items(chat_id: int, arg: str) -> list[tuple[str, str]]:
    items = store.items(chat_id)
    if arg:
        items = [i for i in items if i[0].lower() == arg.lower()] or [
            (arg, "stock" if looks_like_ticker(arg) else "topic")
        ]
    return items


# -------------------------------------------------------------- commands ----
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text(
            f"Sorry, this bot is private. (Your chat ID is {update.effective_chat.id})")
        return
    store.ensure_chat(update.effective_chat.id)
    await update.message.reply_text(
        "👋 I watch stocks, people and topics for you and alert you about new developments "
        f"and interviews (checking every {INTERVAL_MIN} min).\n\n"
        "Try: /add TCS, NVDA, Jensen Huang, AI chips\n\n" + __doc__.split("Commands")[1]
    )


async def cmd_help(update: Update, ctx):
    await update.message.reply_text("Commands" + __doc__.split("Commands")[1])


async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    chat_id = update.effective_chat.id
    raw = " ".join(ctx.args)
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        await update.message.reply_text("Usage: /add TCS, NVDA, Jensen Huang, AI chips")
        return
    added = []
    for name in names:
        if len(store.items(chat_id)) >= MAX_ITEMS:
            await update.message.reply_text(f"Watchlist is full ({MAX_ITEMS} items).")
            break
        kind = "stock" if looks_like_ticker(name) else "topic"
        if store.add(chat_id, name.lstrip("$"), kind):
            added.append((name.lstrip("$"), kind))
    if not added:
        await update.message.reply_text("Already on your watchlist.")
        return
    await update.message.reply_text(
        "✅ Added: " + ", ".join(f"{n} ({'stock' if k == 'stock' else 'person/topic'})" for n, k in added)
        + "\n\nI'll alert you about anything new from now on. Use /news or /interviews to see the latest right away."
    )
    # Mark what's already out there as seen, so the first check doesn't flood you.
    for name, kind in added:
        try:
            news, intv = await gather_item(name, kind)
            store.mark_seen(chat_id, news + intv)
        except Exception as e:
            log.warning("seed failed for %s: %s", name, e)


async def cmd_remove(update: Update, ctx):
    if not allowed(update):
        return
    name = " ".join(ctx.args).strip().lstrip("$")
    ok = name and store.remove(update.effective_chat.id, name)
    await update.message.reply_text(f"🗑 Removed {name}." if ok else "Not on your watchlist. See /list")


async def cmd_list(update: Update, ctx):
    if not allowed(update):
        return
    items = store.items(update.effective_chat.id)
    if not items:
        await update.message.reply_text("Your watchlist is empty. Try /add TCS, Jensen Huang")
        return
    lines = [("📈 " if k == "stock" else "🔎 ") + n for n, k in items]
    await update.message.reply_text("Your watchlist:\n" + "\n".join(lines))


async def _on_demand(update: Update, ctx, want: tuple[str, ...]):
    if not allowed(update):
        return
    chat_id = update.effective_chat.id
    items = pick_items(chat_id, " ".join(ctx.args).strip().lstrip("$"))
    if not items:
        await update.message.reply_text("Your watchlist is empty. Try /add TCS, Jensen Huang")
        return
    await ctx.bot.send_chat_action(chat_id, "typing")
    found = False
    for name, kind in items:
        news, intv = await gather_item(name, kind)
        report = await build_report(name, kind, news, intv, want)
        if report:
            found = True
            await send_long(ctx.bot, chat_id, report)
            store.mark_seen(chat_id, news + intv)
    if not found:
        await update.message.reply_text("Nothing relevant in the last couple of days.")


async def cmd_news(update, ctx):
    await _on_demand(update, ctx, ("news",))


async def cmd_interviews(update, ctx):
    await _on_demand(update, ctx, ("interviews",))


async def cmd_pause(update, ctx):
    if allowed(update):
        store.set_paused(update.effective_chat.id, True)
        await update.message.reply_text("⏸ Alerts paused. /resume to turn them back on.")


async def cmd_resume(update, ctx):
    if allowed(update):
        store.set_paused(update.effective_chat.id, False)
        await update.message.reply_text("▶️ Alerts on.")


# ------------------------------------------------------------ background ----
async def check_all(ctx: ContextTypes.DEFAULT_TYPE):
    """Runs every INTERVAL_MIN minutes: push anything new for every active chat."""
    for chat_id in store.active_chats():
        for name, kind in store.items(chat_id):
            try:
                news, intv = await gather_item(name, kind)
                news = store.filter_unseen(chat_id, news)
                intv = store.filter_unseen(chat_id, intv)
                if not news and not intv:
                    continue
                store.mark_seen(chat_id, news + intv)   # even irrelevant ones, so we don't re-ask Claude
                report = await build_report(name, kind, news, intv)
                if report:
                    await send_long(ctx.bot, chat_id, "🔔 New\n" + report)
            except Exception as e:
                log.warning("check failed for %s/%s: %s", chat_id, name, e)


def main():
    app = Application.builder().token(TOKEN).build()
    for cmd, fn in [
        ("start", cmd_start), ("help", cmd_help), ("add", cmd_add), ("remove", cmd_remove),
        ("list", cmd_list), ("news", cmd_news), ("interviews", cmd_interviews),
        ("pause", cmd_pause), ("resume", cmd_resume),
    ]:
        app.add_handler(CommandHandler(cmd, fn))
    app.job_queue.run_repeating(check_all, interval=INTERVAL_MIN * 60, first=60)
    log.info("Bot running. Model=%s interval=%smin youtube=%s", MODEL, INTERVAL_MIN, bool(YOUTUBE_KEY))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
