"""Google News (per-entity queries) and publisher RSS feeds."""

import calendar
import html
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

from .base import BROWSER_HEADERS, RawItem, now_utc

GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _published(entry) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc) if parsed else now_utc()


class RssClient:
    def __init__(self):
        self._client = httpx.AsyncClient(headers=BROWSER_HEADERS, timeout=20, follow_redirects=True)

    async def _fetch(self, url: str):
        resp = await self._client.get(url)
        resp.raise_for_status()
        return feedparser.parse(resp.content)

    async def google_news(self, query: str, entity_id: str) -> list[RawItem]:
        # when:2d keeps the result set to fresh stories
        feed = await self._fetch(GOOGLE_NEWS.format(q=quote_plus(f"{query} when:2d")))
        return [
            RawItem(
                source="google_news",
                kind="news",
                title=_clean(e.get("title", "")),
                summary=_clean(e.get("summary", ""))[:600],
                url=e.get("link", ""),
                published_at=_published(e),
                hint_entities=[entity_id],
            )
            for e in feed.entries[:30]
        ]

    async def publisher(self, name: str, url: str) -> list[RawItem]:
        feed = await self._fetch(url)
        return [
            RawItem(
                source=name,
                kind="news",
                title=_clean(e.get("title", "")),
                summary=_clean(e.get("summary", ""))[:600],
                url=e.get("link", ""),
                published_at=_published(e),
            )
            for e in feed.entries[:60]
        ]

    async def close(self):
        await self._client.aclose()
