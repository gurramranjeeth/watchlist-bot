# Watchlist Telegram Bot

Tell it what to watch (stocks, people or topics). It checks every 20 minutes for
new **news** and **interviews**, has Claude filter out noise and write a one-line
takeaway for each item, and sends you an alert in Telegram.

```
/add TCS, RELIANCE.NS, NVDA, Jensen Huang, AI chips
/list            /remove NVDA
/news            /news TCS
/interviews      /interviews Jensen Huang
/pause           /resume
```

All-caps names (`TCS`, `NVDA`, `RELIANCE.NS`, `$AAPL`) are treated as **stocks**;
anything else (`Jensen Huang`, `AI chips`) as a **person/topic**.

## 1. Get your keys (10 min)

| Key | Where | Cost |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Message **@BotFather** in Telegram, then `/newbot` | Free |
| `ANTHROPIC_API_KEY` | console.anthropic.com, then API Keys | Pay per use. With Haiku, about 20 items and a 20-min interval usually cost a few dollars a month |
| `YOUTUBE_API_KEY` *(optional)* | console.cloud.google.com: enable "YouTube Data API v3", then Credentials | Free (10k units/day) |

The YouTube key is optional, but each search costs 100 units. With it on, keep
to about 15 items with a 20-min interval, or raise `CHECK_INTERVAL_MIN`.
Without it, interviews come from news coverage only.

## 2. Run locally first

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your keys
set -a; source .env; set +a
python bot.py
```
Open your bot in Telegram and send `/start`. It replies with your **chat ID**.
Put that in `ALLOWED_CHAT_IDS` so nobody else can use the bot (and your API credit).

## 3. Deploy 24/7

The bot uses *polling*, so it needs an always-on **worker**. It doesn't need a web
server or a public URL. Hosts that put free web services to sleep won't work.

**Option 0: GitHub Actions (free, no card).** `.github/workflows/bot.yml` runs
`run_once.py` every 30 min: it answers pending commands, sends alerts, then exits.
Add repo secrets `TELEGRAM_BOT_TOKEN` (required), plus optionally `ALLOWED_CHAT_IDS`,
`ANTHROPIC_API_KEY` and `YOUTUBE_API_KEY`. Command replies can take up to 30 min.
The watchlist is stored in the Actions cache, not in the repo.

**Option A: Railway** (easiest; low-cost, with trial credit)
1. Push this folder to a GitHub repo.
2. railway.app: New Project, then Deploy from GitHub. It finds the Dockerfile.
3. Variables: add the keys from `.env.example`.
4. Add a **Volume** mounted at `/data` so your watchlist survives redeploys.

**Option B: a truly free VM** (Oracle Cloud Always Free, or Google Cloud e2-micro)
```bash
sudo apt install -y docker.io git
git clone <your repo> && cd watchlist-bot
sudo docker build -t wlbot .
sudo docker run -d --restart=always --env-file .env -v wlbot-data:/data --name wlbot wlbot
sudo docker logs -f wlbot
```

## How it works

```
bot.py         commands + a job that runs every CHECK_INTERVAL_MIN
sources.py     Google News RSS (news) + YouTube API / news search (interviews)
summarizer.py  Claude: drops irrelevant items and duplicates, writes takeaways
storage.py     SQLite: watchlists, plus what you've already been sent (no repeats)
```
When you `/add` something, the bot marks what's already published as seen, so you
only get alerts for things that come out afterwards. Use `/news` to see the
current state right away.

## Tweaks
- **Fewer and better alerts:** raise `CHECK_INTERVAL_MIN` to 60.
- **Smarter summaries:** `CLAUDE_MODEL=claude-sonnet-5-5` (more expensive).
- **US news:** `NEWS_REGION=en-US`.
