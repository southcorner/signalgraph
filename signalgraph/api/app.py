import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .. import demo
from ..db import Session, init_db
from ..graph import Graph
from ..models import CalendarEvent, EntityImpact, NewsItem, PriceBar, TickerImpact
from ..pipeline import signals
from ..pipeline.engine import Engine, bar_json, news_json
from ..settings import settings
from ..sources.base import now_utc
from ..sources.prices import AngelOneFeed, DemoFeed

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def build_engine() -> Engine:
    graph = Graph.load(settings.config_dir)
    angel = [settings.angel_api_key, settings.angel_client_code, settings.angel_pin, settings.angel_totp_secret]
    if settings.mode == "live" and all(angel):
        tokens = {e.id: e.nse_token for e in graph.entities.values() if e.id in graph.tickers and e.nse_token}
        feed = AngelOneFeed(*angel, tokens=tokens)
    else:
        feed = DemoFeed(graph.tickers)
    engine = Engine(graph, feed)
    if settings.mode == "demo":
        engine.scorer = demo.DemoScorer(graph)
    return engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    engine = build_engine()
    app.state.engine = engine
    await engine.start(live_sources=settings.mode == "live")
    if settings.mode == "demo":
        await demo.seed(engine)
        engine._spawn(demo.drip(engine))
    log.info("SignalGraph up: mode=%s prices=%s scorer=%s", settings.mode, engine.price_feed.name, engine.scorer.name)
    yield
    await engine.stop()


app = FastAPI(title="SignalGraph", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _engine() -> Engine:
    return app.state.engine


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
async def meta():
    e = _engine()
    return {
        "mode": settings.mode,
        "price_feed": e.price_feed.name,
        "price_live": e.price_feed.live,
        "scorer": e.scorer.name,
        "tickers": e.graph.tickers,
        "green_threshold": settings.green_threshold,
        "red_threshold": settings.red_threshold,
    }


@app.get("/api/graph/{ticker}")
async def graph(ticker: str):
    e = _engine()
    if ticker not in e.graph.tickers:
        raise HTTPException(404, "unknown ticker")
    at = now_utc()
    # nodes with any route to this ticker, plus the ticker itself
    ids = {ticker} | {eid for eid in e.graph.entities if any(p.nodes[-1] == ticker for p in e.graph.paths_to_tickers(eid))}
    async with Session() as s:
        impacts = list(await s.scalars(
            select(EntityImpact).where(EntityImpact.entity_id.in_(ids),
                                       EntityImpact.published_at >= at - signals.LOOKBACK)))
        counts = dict((await s.execute(
            select(EntityImpact.entity_id, func.count()).where(EntityImpact.entity_id.in_(ids))
            .group_by(EntityImpact.entity_id))).all())
    by_entity: dict[str, list] = {}
    for i in impacts:
        by_entity.setdefault(i.entity_id, []).append(i)
    data = e.graph.to_json()
    ticker_signal = await e.signal(ticker)
    nodes = []
    for n in data["nodes"]:
        if n["id"] not in ids:
            continue
        score = ticker_signal["score"] if n["id"] == ticker else signals.entity_score(by_entity.get(n["id"], []), at)
        best = e.graph.best_path(n["id"], ticker)
        nodes.append({**n, "score": round(score, 4), "news_count": counts.get(n["id"], 0),
                      "exposure": round(best.weight, 3) if best else 1.0})
    edges = [x for x in data["edges"] if x["source"] in ids and x["target"] in ids]
    return {"nodes": nodes, "edges": edges}


@app.get("/api/signal/{ticker}")
async def signal(ticker: str, hours: int = 72):
    e = _engine()
    sig = await e.signal(ticker)
    end = now_utc()
    start = end - timedelta(hours=hours)
    async with Session() as s:
        rows = list(await s.scalars(select(TickerImpact).where(
            TickerImpact.ticker == ticker, TickerImpact.published_at >= start - signals.LOOKBACK)))
    sig["history"] = [{"time": int(t.timestamp()), "value": round(v, 4)}
                      for t, v in signals.history(rows, start, end, timedelta(minutes=30))]
    return sig


@app.get("/api/news")
async def news(ticker: str | None = None, entity: str | None = None, limit: int = 100):
    e = _engine()
    async with Session() as s:
        q = select(NewsItem).where(NewsItem.status == "scored").order_by(NewsItem.published_at.desc()).limit(500)
        rows = list(await s.scalars(q))
    out = []
    for r in rows:
        j = news_json(r, e.graph)
        if ticker and not any(t["ticker"] == ticker for t in j["impacts"]):
            continue
        if entity and entity not in j["entities"] and not any(entity in t["path"] for t in j["impacts"]):
            continue
        out.append(j)
        if len(out) >= limit:
            break
    return out


@app.get("/api/entity/{entity_id}")
async def entity(entity_id: str, ticker: str | None = None):
    """Everything the entity page needs: profile, effect on the stock, history, links, news."""
    e = _engine()
    g = e.graph
    if entity_id not in g.entities:
        raise HTTPException(404, "unknown entity")
    ticker = ticker or g.tickers[0]
    at = now_utc()
    neighbours = {l.dst for l in g.out.get(entity_id, [])} | {l.src for l in g.links if l.dst == entity_id}
    async with Session() as s:
        ent_rows = list(await s.scalars(select(EntityImpact).where(
            EntityImpact.entity_id.in_(neighbours | {entity_id}),
            EntityImpact.published_at >= at - signals.LOOKBACK)))
        tick_rows = list(await s.scalars(select(TickerImpact).where(
            TickerImpact.ticker == ticker, TickerImpact.published_at >= at - signals.LOOKBACK)))
    by_entity: dict[str, list] = {}
    for r in ent_rows:
        by_entity.setdefault(r.entity_id, []).append(r)
    own = by_entity.get(entity_id, [])
    via = [t for t in tick_rows if t.path and t.path[0] == entity_id]
    week = at - timedelta(days=7)
    recent = [t for t in via if t.published_at.replace(tzinfo=t.published_at.tzinfo or at.tzinfo) >= week]

    if entity_id == ticker:
        score = signals.ticker_score(tick_rows, at)
    else:
        score = signals.entity_score(own, at)
    history = []
    t = at - timedelta(days=7)
    while t <= at:
        history.append({"time": int(t.timestamp()), "value": round(signals.entity_score(
            [r for r in own if signals._aware(r.published_at) <= t], t), 4)})
        t += timedelta(hours=3)

    node = next(n for n in g.to_json()["nodes"] if n["id"] == entity_id)
    links = []
    for l in g.links:
        if entity_id not in (l.src, l.dst):
            continue
        other = l.dst if l.src == entity_id else l.src
        links.append({
            "id": other, "name": g.entities[other].name, "type": g.entities[other].type,
            "relation": l.relation, "direction": "out" if l.src == entity_id else "in",
            "weight": l.weight, "sign": l.sign,
            "score": round(signals.ticker_score(tick_rows, at) if other == ticker
                           else signals.entity_score(by_entity.get(other, []), at), 4),
        })
    return {
        "entity": {**node, "score": round(score, 4)},
        "ticker": ticker,
        "effect": {
            "paths": [{"nodes": p.nodes, "relations": p.relations, "weight": round(p.weight, 3)}
                      for p in g.paths_to_tickers(entity_id) if p.nodes[-1] == ticker],
            "contribution": round(sum(signals.decayed(i.impact, i.published_at, i.horizon, at) for i in via), 4),
            "items_7d": len(recent),
            "green_7d": sum(1 for i in recent if i.impact > 0.005),
            "red_7d": sum(1 for i in recent if i.impact < -0.005),
        },
        "history": history,
        "links": sorted(links, key=lambda x: -x["weight"]),
        "news": await news(entity=entity_id, limit=40),
    }


@app.get("/api/bars/{ticker}")
async def bars(ticker: str, days: int = 5):
    async with Session() as s:
        rows = list(await s.scalars(select(PriceBar).where(
            PriceBar.ticker == ticker, PriceBar.ts >= now_utc() - timedelta(days=days)).order_by(PriceBar.ts)))
    out = [bar_json(r) for r in rows]
    live = _engine().bars.current.get(ticker)
    if live and (not out or bar_json(live)["time"] > out[-1]["time"]):
        out.append(bar_json(live))
    return out


@app.get("/api/calendar")
async def calendar():
    async with Session() as s:
        rows = list(await s.scalars(select(CalendarEvent).where(
            CalendarEvent.date >= now_utc() - timedelta(days=1)).order_by(CalendarEvent.date).limit(20)))
    return [{"entity": r.entity_id, "date": r.date.date().isoformat(), "title": r.title, "source": r.source}
            for r in rows]


@app.get("/api/health")
async def health():
    e = _engine()
    return {"queue": e.queue.qsize(), "sources": e.health.status}


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()
    hub = _engine().hub
    hub.clients.add(socket)
    try:
        while True:
            await asyncio.wait_for(socket.receive_text(), timeout=60)
    except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
        pass
    finally:
        hub.clients.discard(socket)
