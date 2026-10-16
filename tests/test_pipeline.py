import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/test.db"
os.environ.pop("ANTHROPIC_API_KEY", None)

import httpx  # noqa: E402
import pytest  # noqa: E402

from signalgraph.graph import Graph  # noqa: E402
from signalgraph.pipeline import signals  # noqa: E402
from signalgraph.pipeline.scorer import ClaudeScorer, LexiconScorer  # noqa: E402
from signalgraph.sources.base import RawItem  # noqa: E402
from signalgraph.sources.exchanges import BseClient, NseClient  # noqa: E402

CONFIG = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture(scope="module")
def graph():
    return Graph.load(CONFIG)


def test_alias_matching_respects_word_boundaries_and_context(graph):
    assert "mands" in graph.match("M&S ends service desk deal")
    assert "jlr" in graph.match("JLR restarts production")
    assert "tcs" not in [m.lower() for m in graph.match("Stocks to watch: Batcsx")]
    # 'rupee' only counts with IT context
    assert "usdinr" not in graph.match("Rupee falls against dollar as oil rises")
    assert "usdinr" in graph.match("Rupee falls; IT exporters gain")


def test_paths_reach_ticker_through_projects(graph):
    paths = graph.paths_to_tickers("tejas")
    assert paths, "Tejas should reach TCS via the BSNL 4G project"
    best = graph.best_path("tejas", "TCS")
    assert best.nodes == ["tejas", "bsnl_4g", "TCS"]
    assert best.weight == pytest.approx(0.5 * 0.35)
    assert graph.best_path("infosys", "TCS").sign == -1


@pytest.mark.asyncio
async def test_lexicon_scorer_flips_sign_for_competitors(graph):
    s = LexiconScorer(graph)
    item = RawItem("x", "news", "Infosys wins large deal", "", datetime.now(timezone.utc))
    r = await s.score(item, ["infosys"])
    assert r.ticker_impacts[0].direction < 0


def test_signal_decay_and_bounds():
    now = datetime.now(timezone.utc)
    imp = SimpleNamespace(impact=0.8, published_at=now - timedelta(hours=48), horizon="days")
    assert signals.decayed(0.8, imp.published_at, "days", now) == pytest.approx(0.4)
    many = [SimpleNamespace(impact=0.9, published_at=now, horizon="weeks")] * 20
    assert -1 < signals.ticker_score(many, now) <= 1
    assert signals.label(0.3, 0.25, -0.25) == "GREEN"
    assert signals.label(-0.3, 0.25, -0.25) == "RED"


def test_fingerprint_collapses_publisher_suffix():
    t = datetime.now(timezone.utc)
    a = RawItem("google_news", "news", "TCS wins deal from UK insurer - Economic Times", "", t)
    b = RawItem("google_news", "news", "TCS wins deal from UK insurer - Moneycontrol", "", t)
    assert a.fingerprint == b.fingerprint


@pytest.mark.asyncio
async def test_nse_parser():
    payload = [{"symbol": "TCS", "desc": "Press Release", "an_dt": "16-Oct-2026 18:30:12",
                "attchmntText": "TCS has informed the Exchange about a large deal win",
                "attchmntFile": "https://nsearchives.nseindia.com/x.pdf"}]

    def handler(req: httpx.Request):
        if req.url.path == "/":
            return httpx.Response(200, text="ok")
        return httpx.Response(200, json=payload)

    nse = NseClient()
    nse._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    items = await nse.announcements("TCS", "TCS")
    assert items[0].kind == "filing"
    assert items[0].published_at == datetime(2026, 10, 16, 13, 0, 12, tzinfo=timezone.utc)
    assert "deal win" in items[0].title


@pytest.mark.asyncio
async def test_bse_parser():
    payload = {"Table": [{"HEADLINE": "Outcome of Board Meeting", "NEWSSUB": "TCS - Results",
                          "DissemDT": "2026-10-16T18:31:00.123", "ATTACHMENTNAME": "abc.pdf"}]}
    bse = BseClient()
    bse._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    items = await bse.announcements("532540", "TCS", "TCS")
    assert items[0].url.endswith("abc.pdf")
    assert items[0].title == "TCS: Outcome of Board Meeting"


@pytest.mark.asyncio
async def test_claude_scorer_parses_structured_output(graph):
    scorer = ClaudeScorer(graph, "test-key", "claude-opus-5-5", "low")
    body = {
        "relevant": True, "event_type": "client_distress",
        "entity_impacts": [{"entity_id": "jlr", "sentiment": -0.8, "confidence": 0.9}],
        "ticker_impacts": [{"ticker": "TCS", "via_entity": "jlr", "direction": -1.5, "magnitude": 0.3,
                            "confidence": 0.7, "horizon": "weeks", "reason": "Client production halt."}],
    }
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", stop_details=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(body))])

    scorer.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=fake_create)))
    item = RawItem("x", "news", "JLR halts production", "", datetime.now(timezone.utc))
    r = await scorer.score(item, ["jlr"])
    t = r.ticker_impacts[0]
    assert t.direction == -1.0  # clamped
    assert t.path == ["jlr", "TCS"]
    schema = captured["output_config"]["format"]["schema"]
    assert schema["properties"]["entity_impacts"]["items"]["properties"]["entity_id"]["enum"] == ["jlr"]
    assert captured["fallbacks"] == "default"


@pytest.mark.asyncio
async def test_engine_ingest_dedupes_filters_and_scores(graph):
    from signalgraph.db import init_db
    from signalgraph.pipeline.engine import Engine
    from signalgraph.sources.prices import DemoFeed

    await init_db()
    engine = Engine(graph, DemoFeed(["TCS"]))
    now = datetime.now(timezone.utc)
    items = [
        RawItem("ET", "news", "BSNL expands indigenous 4G rollout, order wins for vendors", "u1", now),
        RawItem("Mint", "news", "BSNL expands indigenous 4G rollout, order wins for vendors", "u2", now),  # dup
        RawItem("ET", "news", "Monsoon arrives early in Kerala", "u3", now),  # no entity
        RawItem("ET", "news", "TCS wins deal", "u4", now - timedelta(days=10)),  # stale
    ]
    assert await engine.ingest(items) == 1
    await engine._score_one(await engine.queue.get())
    sig = await engine.signal("TCS")
    assert sig["score"] > 0
    assert sig["drivers"][0]["path"][-1] == "TCS"
