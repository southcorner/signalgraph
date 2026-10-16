"""Impact scoring: what a news item means for each tracked stock.

ClaudeScorer reasons about second-order effects (a client's trouble, a competitor
losing a deal, a visa rule) using the relationship paths we give it.
LexiconScorer is a crude offline fallback so the pipeline runs without an API key.
"""

import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

from ..graph import Graph
from ..sources.base import RawItem

log = logging.getLogger(__name__)

EVENT_TYPES = [
    "order_win", "order_loss", "results_beat", "results_miss", "guidance_change",
    "management_change", "promoter_activity", "pledge_change", "rating_action",
    "regulatory_legal", "governance_red_flag", "cyber_incident", "layoffs_hiring",
    "client_distress", "client_growth", "project_milestone", "project_delay",
    "partnership", "capex_investment", "dividend_buyback", "macro_policy",
    "sector_read_through", "board_meeting", "routine_filing", "other",
]
HORIZONS = ["intraday", "days", "weeks", "quarters"]


@dataclass
class TickerImpactOut:
    ticker: str
    via_entity: str
    direction: float
    magnitude: float
    confidence: float
    horizon: str
    reason: str
    path: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    relevant: bool
    event_type: str
    entity_impacts: list[tuple[str, float, float]]  # (entity_id, sentiment, confidence)
    ticker_impacts: list[TickerImpactOut]
    scorer: str


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


SYSTEM_PROMPT = """You are an equity analyst for Indian listed stocks (NSE/BSE). You read one news item or exchange filing at a time and judge what it means for the share price of each tracked stock, including second-order effects through clients, partners, competitors, parent companies, people and macro themes.

The output feeds a red/green signal dashboard for one trader. It produces signals only; nobody auto-trades on it. Be calibrated: most items are noise, and saying so is valuable.

How to judge:
- Judge price impact on the tracked stock, not the tone of the article. A competitor losing a large deal can be mildly positive. A weaker rupee is positive for Indian IT exporters. Bad news about a client can hurt the vendor.
- Use the relationship path and link weight you are given. A low-weight link should rarely produce a large magnitude.
- Magnitude scale: 0.05 = trivial or routine, 0.2 = notable, 0.5 = clearly price-moving, 0.8+ = major (big results surprise, fraud, CEO exit, a large order win or loss).
- India-specific: auditor resignation, rising promoter pledge, SEBI/ED action and qualified audit opinions are strongly negative. Routine filings (trading-window closures, investor-meet intimations, duplicate share certificates, ESOP allotments) are near zero.
- Old news recycled into a new article deserves low magnitude, because it is already priced in.
- If the item does not really concern any listed entity (a name collision, a passing mention), set relevant=false and return empty lists.
- entity_impacts: for each entity the item is actually about, how good or bad it is for that entity itself (-1 to 1).
- ticker_impacts: one entry per tracked stock affected. direction -1 to 1, magnitude 0 to 1, confidence 0 to 1. "via_entity" is the entity through which the effect flows (the stock itself for direct news). The reason must be one short sentence a trader can scan."""


class ClaudeScorer:
    name = "claude"

    def __init__(self, graph: Graph, api_key: str, model: str, effort: str):
        self.graph = graph
        self.model = model
        self.effort = effort
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=3)

    def _schema(self, entity_ids: list[str]) -> dict:
        return {
            "type": "object",
            "properties": {
                "relevant": {"type": "boolean"},
                "event_type": {"type": "string", "enum": EVENT_TYPES},
                "entity_impacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity_id": {"type": "string", "enum": entity_ids},
                            "sentiment": {"type": "number"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["entity_id", "sentiment", "confidence"],
                        "additionalProperties": False,
                    },
                },
                "ticker_impacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string", "enum": self.graph.tickers},
                            "via_entity": {"type": "string", "enum": entity_ids},
                            "direction": {"type": "number"},
                            "magnitude": {"type": "number"},
                            "confidence": {"type": "number"},
                            "horizon": {"type": "string", "enum": HORIZONS},
                            "reason": {"type": "string"},
                        },
                        "required": ["ticker", "via_entity", "direction", "magnitude", "confidence", "horizon", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["relevant", "event_type", "entity_impacts", "ticker_impacts"],
            "additionalProperties": False,
        }

    def _context(self, item: RawItem, matched: list[str]) -> str:
        lines = []
        for eid in matched:
            e = self.graph.entities[eid]
            lines.append(f"- {eid}: {e.name} [{e.type}]" + (f" - {e.role}" if e.role else ""))
            for p in self.graph.paths_to_tickers(eid):
                lines.append(f"    path to {p.nodes[-1]}: {p.describe(self.graph)} (link weight {p.weight:.2f})")
        return (
            f"Tracked stocks: {', '.join(self.graph.tickers)}\n\n"
            f"Entities mentioned and how they connect to tracked stocks:\n" + "\n".join(lines) + "\n\n"
            f"<item>\nsource: {item.source} ({item.kind})\npublished: {item.published_at.isoformat()}\n"
            f"title: {item.title}\nsummary: {item.summary or '(none)'}\n</item>"
        )

    async def score(self, item: RawItem, matched: list[str]) -> ScoreResult:
        response = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=8000,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": self._context(item, matched)}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": self._schema(matched)}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"scoring refused: {getattr(response.stop_details, 'category', None)}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("scoring hit max_tokens")
        data = json.loads(next(b.text for b in response.content if b.type == "text"))

        impacts = []
        for t in data["ticker_impacts"]:
            path = self.graph.best_path(t["via_entity"], t["ticker"])
            impacts.append(
                TickerImpactOut(
                    ticker=t["ticker"],
                    via_entity=t["via_entity"],
                    direction=_clamp(t["direction"], -1, 1),
                    magnitude=_clamp(t["magnitude"], 0, 1),
                    confidence=_clamp(t["confidence"], 0, 1),
                    horizon=t["horizon"],
                    reason=t["reason"],
                    path=path.nodes if path else [t["via_entity"], t["ticker"]],
                )
            )
        return ScoreResult(
            relevant=bool(data["relevant"]),
            event_type=data["event_type"],
            entity_impacts=[
                (e["entity_id"], _clamp(e["sentiment"], -1, 1), _clamp(e["confidence"], 0, 1))
                for e in data["entity_impacts"]
            ],
            ticker_impacts=impacts if data["relevant"] else [],
            scorer=f"claude:{response.model}",
        )


POSITIVE = {
    "wins": 0.5, "bags": 0.5, "secures": 0.5, "order": 0.2, "deal": 0.25, "contract": 0.2, "beats": 0.5,
    "upgrade": 0.4, "record": 0.3, "surges": 0.4, "jumps": 0.35, "rallies": 0.35, "expands": 0.25,
    "partnership": 0.25, "buyback": 0.4, "dividend": 0.2, "growth": 0.2, "profit rises": 0.4,
    "raises guidance": 0.5, "launches": 0.15, "milestone": 0.25, "approval": 0.25,
}
NEGATIVE = {
    "loses": 0.5, "cuts": 0.35, "layoffs": 0.35, "probe": 0.5, "penalty": 0.4, "downgrade": 0.45,
    "misses": 0.5, "resigns": 0.5, "fraud": 0.8, "breach": 0.5, "cyberattack": 0.5, "cyber attack": 0.5,
    "lawsuit": 0.4, "delay": 0.3, "terminates": 0.5, "slumps": 0.4, "falls": 0.25, "plunges": 0.5,
    "warning": 0.3, "pledge": 0.3, "sebi": 0.3, "default": 0.6, "outage": 0.35, "fee hike": 0.35,
    "restrictions": 0.3, "slowdown": 0.3, "weak": 0.25,
}


class LexiconScorer:
    """Keyword scorer: no understanding of context, but free and offline."""

    name = "lexicon"

    def __init__(self, graph: Graph):
        self.graph = graph

    @staticmethod
    def sentiment(text: str) -> float:
        t = text.lower()
        score = sum(w for k, w in POSITIVE.items() if re.search(rf"\b{re.escape(k)}", t))
        score -= sum(w for k, w in NEGATIVE.items() if re.search(rf"\b{re.escape(k)}", t))
        return _clamp(score, -1, 1)

    async def score(self, item: RawItem, matched: list[str]) -> ScoreResult:
        s = self.sentiment(f"{item.title}. {item.summary}")
        impacts: dict[str, TickerImpactOut] = {}
        for eid in matched:
            for p in self.graph.paths_to_tickers(eid):
                direction = s * p.sign
                mag = abs(s) * p.weight
                ticker = p.nodes[-1]
                if mag > 0 and (ticker not in impacts or mag > impacts[ticker].magnitude):
                    impacts[ticker] = TickerImpactOut(
                        ticker, eid, 1.0 if direction > 0 else -1.0, mag, 0.3, "days",
                        f"Keyword score {s:+.2f} via {p.describe(self.graph)}", p.nodes,
                    )
        return ScoreResult(
            relevant=bool(matched),
            event_type="other",
            entity_impacts=[(eid, s, 0.3) for eid in matched],
            ticker_impacts=list(impacts.values()),
            scorer="lexicon",
        )
