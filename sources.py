"""Where the news and interviews come from.

- News:       Google News RSS search (free, no key).
- Interviews: YouTube Data API search if YOUTUBE_API_KEY is set,
              otherwise Google News filtered for interview/podcast stories.
"""
import asyncio
import html
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

UA = {"User-Agent": "Mozilla/5.0 (watchlist-bot)"}
_TAG_RE = re.compile(r"<[^>]+>")


def looks_like_ticker(name: str) -> bool:
    """TCS, RELIANCE.NS, NVDA, $AAPL -> stock. 'Jensen Huang', 'AI chips' -> topic."""
    n = name.lstrip("$")
    return bool(re.fullmatch(r"[A-Z0-9&\-]{1,12}(\.[A-Z]{1,3})?", n))


def _query_for(name: str, kind: str) -> str:
    if kind == "stock":
        base = name.lstrip("$").split(".")[0]          # RELIANCE.NS -> RELIANCE
        return f'"{base}" (stock OR shares OR results OR company)'
    return f'"{name}"'


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", text or "")).strip()


async def _google_news(client: httpx.AsyncClient, query: str, region: str, limit: int) -> list[dict]:
    hl, gl = region.split("-") if "-" in region else ("en", region)
    url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(query + " when:2d")
        + f"&hl={hl}-{gl}&gl={gl}&ceid={gl}:{hl}"
    )
    r = await client.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    feed = feedparser.parse(r.text)
    out = []
    for e in feed.entries[:limit]:
        source = getattr(e, "source", {}).get("title", "") if hasattr(e, "source") else ""
        published = None
        if getattr(e, "published_parsed", None):
            published = datetime.fromtimestamp(time.mktime(e.published_parsed), tz=timezone.utc)
        out.append(
            {
                "title": _clean(e.title),
                "url": e.link,
                "source": source,
                "snippet": _clean(getattr(e, "summary", ""))[:300],
                "published": published,
            }
        )
    return out


async def _youtube(client: httpx.AsyncClient, query: str, api_key: str, limit: int) -> list[dict]:
    after = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = await client.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "date",
            "publishedAfter": after,
            "maxResults": limit,
            "key": api_key,
        },
        timeout=20,
    )
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        sn = it["snippet"]
        out.append(
            {
                "title": _clean(sn["title"]),
                "url": f"https://www.youtube.com/watch?v={it['id']['videoId']}",
                "source": sn.get("channelTitle", "YouTube"),
                "snippet": _clean(sn.get("description", ""))[:300],
                "published": datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
            }
        )
    return out


async def fetch_news(client, name: str, kind: str, region: str, limit: int = 8) -> list[dict]:
    return await _google_news(client, _query_for(name, kind), region, limit)


async def fetch_interviews(client, name: str, kind: str, region: str, youtube_key: str | None,
                           limit: int = 5) -> list[dict]:
    if kind == "stock":
        base = name.lstrip("$").split(".")[0]
        yt_q = f"{base} CEO OR management interview"
        gn_q = f'"{base}" (CEO OR MD OR management OR chairman) (interview OR podcast OR "in conversation" OR "speaks to")'
    else:
        yt_q = f'"{name}" interview OR podcast'
        gn_q = f'"{name}" (interview OR podcast OR "in conversation" OR "speaks to" OR keynote)'

    results: list[dict] = []
    tasks = [_google_news(client, gn_q, region, limit)]
    if youtube_key:
        tasks.append(_youtube(client, yt_q, youtube_key, limit))
    for res in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(res, list):
            results.extend(res)
    return results
