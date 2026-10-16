"""Orchestrates ingestion -> matching -> scoring -> storage -> live push.

Everything runs as asyncio tasks in one process, which is plenty for a single
user. To scale out, the two seams are the scoring queue (swap asyncio.Queue for
Redis/NATS and run scorers as separate workers) and the Hub (swap for Redis
pub/sub so several API processes can fan out to browsers).
"""

import asyncio
import logging
import random
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import httpx
import yaml
from sqlalchemy import select

from ..db import Session
from ..graph import Graph
from ..models import CalendarEvent, EntityImpact, NewsItem, PriceBar, TickerImpact
from ..settings import settings
from ..sources.base import RawItem, now_utc
from ..sources.exchanges import BseClient, NseClient
from ..sources.prices import BarBuilder, PriceFeed
from ..sources.rss import RssClient
from . import signals
from .scorer import ClaudeScorer, LexiconScorer, ScoreResult

log = logging.getLogger(__name__)

MAX_ITEM_AGE = timedelta(hours=72)  # ignore stale backlog on first poll (saves scoring cost)


class Hub:
    """Fan-out of live events to connected browsers."""

    def __init__(self):
        self.clients: set = set()

    async def broadcast(self, event: str, data: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json({"event": event, "data": data})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


class SourceHealth:
    def __init__(self):
        self.status: dict[str, dict] = {}

    def ok(self, name: str, count: int):
        self.status[name] = {"ok": True, "at": now_utc().isoformat(), "items": count, "error": None}

    def fail(self, name: str, err: Exception):
        prev = self.status.get(name, {})
        self.status[name] = {**prev, "ok": False, "error_at": now_utc().isoformat(), "error": str(err)[:200]}


class Engine:
    def __init__(self, graph: Graph, price_feed: PriceFeed):
        self.graph = graph
        self.price_feed = price_feed
        self.hub = Hub()
        self.health = SourceHealth()
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.bars = BarBuilder()
        self.tasks: list[asyncio.Task] = []
        self.universe = yaml.safe_load((settings.config_dir / "universe.yaml").read_text())
        self.lexicon = LexiconScorer(graph)
        self.scorer = (
            ClaudeScorer(graph, settings.anthropic_api_key, settings.scoring_model, settings.scoring_effort)
            if settings.anthropic_api_key
            else self.lexicon
        )

    # ---------- ingestion ----------

    def _remember(self, fp: str) -> bool:
        """True if new. Bounded so memory stays flat over weeks of polling."""
        if fp in self.seen:
            return False
        self.seen[fp] = None
        if len(self.seen) > 50_000:
            self.seen.popitem(last=False)
        return True

    async def ingest(self, items: list[RawItem], max_age: timedelta | None = MAX_ITEM_AGE) -> int:
        added = 0
        cutoff = now_utc() - max_age if max_age else None
        for item in items:
            if not item.title or not self._remember(item.fingerprint):
                continue
            if cutoff and item.published_at < cutoff:
                continue
            matched = self.graph.match(f"{item.title} {item.summary}")
            if item.kind == "filing" or item.source == "demo":  # filings concern the issuer even if unnamed
                matched = list(dict.fromkeys(item.hint_entities + matched))
            if not matched:
                continue
            async with Session() as s:
                exists = await s.scalar(select(NewsItem.id).where(NewsItem.fingerprint == item.fingerprint))
                if exists:
                    continue
                row = NewsItem(
                    fingerprint=item.fingerprint, source=item.source, kind=item.kind, title=item.title,
                    summary=item.summary, url=item.url, published_at=item.published_at,
                    fetched_at=now_utc(), matched_entities=matched,
                )
                s.add(row)
                await s.commit()
                await self.queue.put(row.id)
                added += 1
        return added

    # ---------- scoring ----------

    async def _score_worker(self):
        while True:
            news_id = await self.queue.get()
            try:
                await self._score_one(news_id)
            except Exception:
                log.exception("scoring failed for news %s", news_id)
            finally:
                self.queue.task_done()

    async def _score_one(self, news_id: int):
        async with Session() as s:
            row = await s.get(NewsItem, news_id)
            if row is None or row.status != "pending":
                return
            item = RawItem(row.source, row.kind, row.title, row.url, row.published_at, row.summary)
            matched = [e for e in row.matched_entities if e in self.graph.entities]
            try:
                result: ScoreResult = await self.scorer.score(item, matched)
            except Exception as err:
                log.warning("%s scorer failed (%s); falling back to lexicon", self.scorer.name, err)
                result = await self.lexicon.score(item, matched)
            row.scorer, row.event_type = result.scorer, result.event_type
            row.status = "scored" if result.relevant else "irrelevant"
            for eid, sentiment, conf in result.entity_impacts:
                s.add(EntityImpact(news_id=row.id, entity_id=eid, sentiment=sentiment, confidence=conf,
                                   published_at=row.published_at))
            for t in result.ticker_impacts:
                s.add(TickerImpact(news_id=row.id, ticker=t.ticker, direction=t.direction, magnitude=t.magnitude,
                                   confidence=t.confidence, horizon=t.horizon, path=t.path, reason=t.reason,
                                   published_at=row.published_at))
            await s.commit()
            await s.refresh(row, ["entity_impacts", "ticker_impacts"])
            payload = news_json(row, self.graph)

        if row.status == "scored":
            await self.hub.broadcast("news", payload)
            for ticker in {t.ticker for t in result.ticker_impacts}:
                await self.hub.broadcast("signal", await self.signal(ticker))
            await self._maybe_alert(payload)

    async def requeue_pending(self):
        async with Session() as s:
            for nid in await s.scalars(select(NewsItem.id).where(NewsItem.status == "pending")):
                await self.queue.put(nid)

    # ---------- signals ----------

    async def signal(self, ticker: str) -> dict:
        at = now_utc()
        async with Session() as s:
            rows = list(await s.scalars(
                select(TickerImpact).where(TickerImpact.ticker == ticker,
                                           TickerImpact.published_at >= at - signals.LOOKBACK)
            ))
        score = signals.ticker_score(rows, at)
        drivers = sorted(rows, key=lambda i: abs(signals.decayed(i.impact, i.published_at, i.horizon, at)), reverse=True)
        return {
            "ticker": ticker,
            "score": round(score, 4),
            "label": signals.label(score, settings.green_threshold, settings.red_threshold),
            "at": at.isoformat(),
            "drivers": [
                {"news_id": d.news_id, "title": d.news.title, "path": d.path, "reason": d.reason,
                 "impact": round(d.impact, 3),
                 "current": round(signals.decayed(d.impact, d.published_at, d.horizon, at), 3)}
                for d in drivers[:5]
            ],
        }

    async def _maybe_alert(self, payload: dict):
        if not (settings.telegram_bot_token and settings.telegram_chat_id):
            return
        for t in payload["impacts"]:
            if abs(t["impact"]) < settings.alert_impact:
                continue
            icon = "🟢" if t["direction"] > 0 else "🔴"
            path = " → ".join(self.graph.entities[n].name for n in t["path"] if n in self.graph.entities)
            text = (f"{icon} {t['ticker']} {t['impact']:+.2f} ({t['horizon']})\n{payload['title']}\n"
                    f"Path: {path}\nWhy: {t['reason']}\n{payload['url']}")
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    await c.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                                 json={"chat_id": settings.telegram_chat_id, "text": text,
                                       "disable_web_page_preview": True})
            except Exception as err:
                log.warning("telegram alert failed: %s", err)

    # ---------- prices ----------

    async def _prices(self):
        try:
            bars = await self.price_feed.backfill(days=5)
            async with Session() as s:
                for b in bars:
                    await s.merge(PriceBar(ticker=b.ticker, ts=b.ts, open=b.open, high=b.high, low=b.low,
                                           close=b.close, volume=b.volume))
                await s.commit()
            self.health.ok(f"prices:{self.price_feed.name}", len(bars))
        except Exception as err:
            log.exception("price backfill failed")
            self.health.fail(f"prices:{self.price_feed.name}", err)

        ticks: asyncio.Queue = asyncio.Queue()
        feed_task = asyncio.create_task(self.price_feed.run(ticks))
        self.tasks.append(feed_task)
        last_push: dict[str, float] = {}
        while True:
            tick = await ticks.get()
            bar, finished = self.bars.add(tick)
            if finished:
                async with Session() as s:
                    await s.merge(PriceBar(ticker=finished.ticker, ts=finished.ts, open=finished.open,
                                           high=finished.high, low=finished.low, close=finished.close,
                                           volume=finished.volume))
                    await s.commit()
                self.health.ok(f"prices:{self.price_feed.name}", 1)
            if time.monotonic() - last_push.get(tick.ticker, 0) >= 0.25:
                last_push[tick.ticker] = time.monotonic()
                await self.hub.broadcast("bar", bar_json(bar))

    # ---------- pollers ----------

    async def _every(self, name: str, interval: float, fn):
        backoff = interval
        while True:
            try:
                items = await fn()
                added = await self.ingest(items) if isinstance(items, list) else 0
                self.health.ok(name, added)
                backoff = interval
            except Exception as err:
                log.warning("%s failed: %s", name, err)
                self.health.fail(name, err)
                backoff = min(backoff * 2, 600)
            await asyncio.sleep(backoff * random.uniform(0.85, 1.15))

    async def _google_news(self, rss: RssClient, interval: float):
        entities = list(self.graph.entities.values())
        while True:
            for e in entities:
                name = f"google_news:{e.id}"
                try:
                    added = await self.ingest(await rss.google_news(e.search_query, e.id))
                    self.health.ok(name, added)
                except Exception as err:
                    self.health.fail(name, err)
                await asyncio.sleep(interval / len(entities))

    async def _board_meetings(self, nse: NseClient):
        listed = [e for e in self.graph.entities.values() if e.id in self.graph.tickers and e.nse_symbol]
        for e in listed:
            for date, purpose in await nse.board_meetings(e.nse_symbol):
                fp = f"nse-bm:{e.id}:{date.date()}:{purpose}"[:64]
                async with Session() as s:
                    if not await s.scalar(select(CalendarEvent.id).where(CalendarEvent.fingerprint == fp)):
                        s.add(CalendarEvent(fingerprint=fp, entity_id=e.id, date=date,
                                            title=f"Board meeting: {purpose}", source="nse"))
                        await s.commit()
        return None

    def start_live_sources(self):
        iv = self.universe.get("intervals", {})
        nse, bse, rss = NseClient(), BseClient(), RssClient()
        listed = [e for e in self.graph.entities.values() if e.id in self.graph.tickers]
        for e in listed:
            if e.nse_symbol:
                self._spawn(self._every(f"nse:{e.id}", iv.get("nse_filings", 20),
                                        lambda e=e: nse.announcements(e.nse_symbol, e.id)))
            if e.bse_code:
                self._spawn(self._every(f"bse:{e.id}", iv.get("bse_filings", 20),
                                        lambda e=e: bse.announcements(e.bse_code, e.id, e.ticker or e.name)))
        self._spawn(self._every("nse:board_meetings", iv.get("board_meetings", 3600), lambda: self._board_meetings(nse)))
        for feed in self.universe.get("publisher_feeds", []):
            self._spawn(self._every(f"rss:{feed['name']}", iv.get("publisher_feeds", 90),
                                    lambda f=feed: rss.publisher(f["name"], f["url"])))
        self._spawn(self._google_news(rss, iv.get("google_news", 180)))

    # ---------- lifecycle ----------

    def _spawn(self, coro):
        self.tasks.append(asyncio.create_task(coro))

    async def start(self, live_sources: bool = True):
        await self.requeue_pending()
        for _ in range(settings.scoring_concurrency):
            self._spawn(self._score_worker())
        self._spawn(self._prices())
        if live_sources:
            self.start_live_sources()

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def bar_json(b) -> dict:
    return {"ticker": b.ticker, "time": int(b.ts.replace(tzinfo=b.ts.tzinfo or timezone.utc).timestamp()),
            "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}


def news_json(row: NewsItem, graph: Graph) -> dict:
    published = row.published_at if row.published_at.tzinfo else row.published_at.replace(tzinfo=timezone.utc)
    return {
        "id": row.id,
        "title": row.title,
        "summary": row.summary,
        "url": row.url,
        "source": row.source,
        "kind": row.kind,
        "published_at": published.isoformat(),
        "event_type": row.event_type,
        "scorer": row.scorer,
        "entities": row.matched_entities,
        "entity_impacts": [
            {"entity": e.entity_id, "sentiment": round(e.sentiment, 3), "confidence": round(e.confidence, 3)}
            for e in row.entity_impacts
        ],
        "impacts": [
            {"ticker": t.ticker, "direction": t.direction, "magnitude": t.magnitude, "confidence": t.confidence,
             "impact": round(t.impact, 3), "horizon": t.horizon, "path": t.path, "reason": t.reason}
            for t in row.ticker_impacts
        ],
    }
