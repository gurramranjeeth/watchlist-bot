"""One-shot mode for GitHub Actions (or any scheduler / cron).

Each run:  1) answers any commands you sent since the last run
           2) checks every watchlist for new news & interviews and sends alerts
           3) exits
"""
import asyncio
from types import SimpleNamespace

import bot


async def main():
    app = bot.build_app()
    async with app:
        offset = int(bot.store.get_meta("update_offset", 0)) or None
        updates = await app.bot.get_updates(offset=offset, timeout=0, allowed_updates=["message"])
        bot.log.info("Processing %d pending command(s)", len(updates))
        for u in updates:
            try:
                await app.process_update(u)
            except Exception as e:
                bot.log.warning("command failed: %s", e)
            bot.store.set_meta("update_offset", u.update_id + 1)

        await bot.check_all(SimpleNamespace(bot=app.bot))
        # confirm the processed updates with Telegram so they aren't re-delivered
        if updates:
            await app.bot.get_updates(offset=updates[-1].update_id + 1, timeout=0)
    await bot.http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
