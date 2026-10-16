import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
}


@dataclass
class RawItem:
    source: str
    kind: str  # filing | news
    title: str
    url: str
    published_at: datetime
    summary: str = ""
    hint_entities: list[str] = field(default_factory=list)  # e.g. the entity a query was for

    @property
    def fingerprint(self) -> str:
        """Same story from many outlets collapses to one item: key on the normalised
        title (Google News appends ' - Publisher'), not the URL."""
        title = re.sub(r"\s+-\s+[^-]{2,60}$", "", self.title) if self.source == "google_news" else self.title
        norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        return hashlib.sha256(norm.encode()).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ist(value: str, formats: list[str]) -> datetime | None:
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=IST).astimezone(timezone.utc)
        except (ValueError, AttributeError):
            continue
    return None
