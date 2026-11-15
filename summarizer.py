"""Uses Claude to turn a pile of headlines into a short, readable alert."""
import json
import logging

from anthropic import AsyncAnthropic

log = logging.getLogger(__name__)

PROMPT = """You are a sharp market/news analyst writing a Telegram alert.
Watchlist item: {name} ({kind})

Below are new {section} items (JSON). Some may be irrelevant (different company with
a similar name, generic market roundups, duplicates of the same story).

Return ONLY JSON in this shape:
{{
  "relevant": [{{"i": <index>, "takeaway": "<one plain sentence: what happened / what was said>"}}],
  "why_it_matters": "<one or two sentences tying the relevant items together, or empty string>"
}}

Rules: drop irrelevant items and merge duplicates (keep the best source).
For interviews, the takeaway should be the key thing the person said or signalled.
No hype, no investment advice.

Items:
{items}"""


class Summarizer:
    def __init__(self, api_key: str | None, model: str):
        self.client = AsyncAnthropic(api_key=api_key) if api_key else None
        self.model = model

    async def summarize(self, name: str, kind: str, section: str, items: list[dict]) -> dict:
        """Returns {"relevant": [(item, takeaway)], "why_it_matters": str}."""
        fallback = {"relevant": [(it, "") for it in items], "why_it_matters": ""}
        if not self.client or not items:
            return fallback

        payload = [
            {"i": i, "title": it["title"], "source": it["source"], "snippet": it["snippet"]}
            for i, it in enumerate(items)
        ]
        try:
            msg = await self.client.messages.create(
                model=self.model,
                max_tokens=800,
                messages=[{
                    "role": "user",
                    "content": PROMPT.format(name=name, kind=kind, section=section,
                                             items=json.dumps(payload, ensure_ascii=False)),
                }],
            )
            text = "".join(b.text for b in msg.content if b.type == "text").strip()
            text = text[text.find("{"): text.rfind("}") + 1]
            data = json.loads(text)
            rel = [
                (items[r["i"]], r.get("takeaway", ""))
                for r in data.get("relevant", [])
                if isinstance(r.get("i"), int) and 0 <= r["i"] < len(items)
            ]
            return {"relevant": rel, "why_it_matters": data.get("why_it_matters", "")}
        except Exception as e:  # never let a summary failure block the alert
            log.warning("Claude summary failed for %s: %s", name, e)
            return fallback
