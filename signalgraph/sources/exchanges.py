"""NSE and BSE corporate filings.

These are the exchanges' unofficial website JSON endpoints: they need browser-like
headers and (for NSE) session cookies from the home page, and they rate-limit.
Keep poll intervals at 15s or more and use this for personal use only.
"""

import logging
from datetime import datetime, timedelta

import httpx

from .base import BROWSER_HEADERS, IST, RawItem, now_utc, parse_ist

log = logging.getLogger(__name__)


class NseClient:
    BASE = "https://www.nseindia.com"

    def __init__(self):
        self._client = httpx.AsyncClient(headers=BROWSER_HEADERS, timeout=15, follow_redirects=True)
        self._warm = False

    async def _get_json(self, path: str, params: dict, referer: str):
        for attempt in range(2):
            if not self._warm:
                await self._client.get(self.BASE, headers={"Accept": "text/html"})
                self._warm = True
            resp = await self._client.get(self.BASE + path, params=params, headers={"Referer": self.BASE + referer})
            if resp.status_code in (401, 403) and attempt == 0:
                self._warm = False  # cookies expired; refresh once
                continue
            resp.raise_for_status()
            return resp.json()

    async def announcements(self, symbol: str, entity_id: str) -> list[RawItem]:
        data = await self._get_json(
            "/api/corporate-announcements",
            {"index": "equities", "symbol": symbol},
            f"/get-quotes/equity?symbol={symbol}",
        )
        rows = data if isinstance(data, list) else data.get("data", [])
        items = []
        for row in rows[:50]:
            published = parse_ist(row.get("an_dt") or row.get("sort_date") or "", ["%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"])
            subject = (row.get("desc") or "Announcement").strip()
            text = (row.get("attchmntText") or "").strip()
            items.append(
                RawItem(
                    source="nse",
                    kind="filing",
                    title=f"{symbol}: {subject}" + (f" - {text[:160]}" if text else ""),
                    summary=text,
                    url=row.get("attchmntFile") or f"{self.BASE}/get-quotes/equity?symbol={symbol}",
                    published_at=published or now_utc(),
                    hint_entities=[entity_id],
                )
            )
        return items

    async def board_meetings(self, symbol: str) -> list[tuple[datetime, str]]:
        data = await self._get_json(
            "/api/corporate-board-meetings",
            {"index": "equities", "symbol": symbol},
            f"/get-quotes/equity?symbol={symbol}",
        )
        rows = data if isinstance(data, list) else data.get("data", [])
        out = []
        for row in rows:
            date = parse_ist(row.get("bm_date") or "", ["%d-%b-%Y"])
            if date:
                out.append((date, (row.get("bm_purpose") or row.get("bm_desc") or "Board meeting").strip()))
        return out

    async def close(self):
        await self._client.aclose()


class BseClient:
    API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

    def __init__(self):
        self._client = httpx.AsyncClient(
            headers={**BROWSER_HEADERS, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"},
            timeout=15,
        )

    async def announcements(self, scrip_code: str, entity_id: str, label: str) -> list[RawItem]:
        today = datetime.now(IST)
        params = {
            "pageno": 1,
            "strCat": -1,
            "strPrevDate": (today - timedelta(days=7)).strftime("%Y%m%d"),
            "strScrip": scrip_code,
            "strSearch": "P",
            "strToDate": today.strftime("%Y%m%d"),
            "strType": "C",
            "subcategory": -1,
        }
        resp = await self._client.get(self.API, params=params)
        resp.raise_for_status()
        items = []
        for row in resp.json().get("Table", [])[:50]:
            stamp = (row.get("DissemDT") or row.get("NEWS_DT") or "").split(".")[0]
            published = parse_ist(stamp, ["%Y-%m-%dT%H:%M:%S"])
            headline = (row.get("HEADLINE") or row.get("NEWSSUB") or "").strip()
            attachment = row.get("ATTACHMENTNAME")
            items.append(
                RawItem(
                    source="bse",
                    kind="filing",
                    title=f"{label}: {headline[:220]}",
                    summary=(row.get("NEWSSUB") or "").strip(),
                    url=self.ATTACH + attachment if attachment else "https://www.bseindia.com/corporates/ann.html",
                    published_at=published or now_utc(),
                    hint_entities=[entity_id],
                )
            )
        return items

    async def close(self):
        await self._client.aclose()
