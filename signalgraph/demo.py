"""Offline demo: invented sample headlines with pre-set scores, plus a slow drip of
new ones so the live push path can be seen working. Every title starts with
[Sample]; none of these are real news."""

import asyncio
import random
from datetime import timedelta

from .pipeline.scorer import ScoreResult, TickerImpactOut
from .sources.base import RawItem, now_utc

# (hours ago, source, kind, title, entity impacts {id: sentiment}, ticker impact (via, dir, mag, conf, horizon, reason))
SAMPLES = [
    (70, "demo", "filing", "[Sample] TCS: Outcome of board meeting - quarterly results and interim dividend",
     {"TCS": 0.4}, ("TCS", 1, 0.45, 0.8, "weeks", "Results ahead of estimates with a dividend; direct positive.")),
    (64, "demo", "news", "[Sample] Infosys wins large multi-year deal from a European bank",
     {"infosys": 0.6}, ("infosys", -1, 0.12, 0.6, "weeks", "Competitor win in BFSI; slight share-of-wallet pressure on TCS.")),
    (58, "demo", "news", "[Sample] Jaguar Land Rover production disruption extends into another week",
     {"jlr": -0.7}, ("jlr", -1, 0.3, 0.7, "weeks", "Key Tata-group client under stress; risk to discretionary IT spend.")),
    (50, "demo", "news", "[Sample] US proposes stricter H-1B wage rules for IT outsourcers",
     {"h1b": -0.6}, ("h1b", -1, 0.35, 0.7, "quarters", "Higher onsite costs for Indian IT; margin headwind.")),
    (41, "demo", "news", "[Sample] BSNL expands indigenous 4G rollout to additional circles",
     {"bsnl": 0.5, "bsnl_4g": 0.6}, ("bsnl_4g", 1, 0.25, 0.65, "quarters", "Larger scope on the BSNL 4G project TCS leads.")),
    (33, "demo", "news", "[Sample] Rupee weakens past key level against dollar; IT exporters gain",
     {"usdinr": -0.4}, ("usdinr", 1, 0.2, 0.7, "days", "Weaker rupee lifts INR-reported revenue for IT exporters.")),
    (26, "demo", "news", "[Sample] Accenture trims full-year outlook citing slower discretionary spend",
     {"accenture": -0.6}, ("accenture", -1, 0.35, 0.7, "weeks", "Global bellwether cut is a negative read-through for Indian IT.")),
    (18, "demo", "news", "[Sample] TCS HyperVault signs anchor tenant for first AI data centre campus",
     {"hypervault": 0.7}, ("hypervault", 1, 0.3, 0.6, "quarters", "De-risks the data-centre capex with committed demand.")),
    (9, "demo", "news", "[Sample] Krithivasan says deal pipeline remains strong despite macro caution",
     {"krithivasan": 0.3}, ("krithivasan", 1, 0.1, 0.5, "days", "Management commentary; mildly reassuring, low information.")),
    (55, "demo", "news", "[Sample] MEA reports record passport issuance under Passport Seva 2.0",
     {"passport_seva": 0.4}, ("passport_seva", 1, 0.04, 0.5, "weeks", "Programme running smoothly; negligible financial effect.")),
    (46, "demo", "news", "[Sample] BSNL crosses 1 lakh 4G sites, plans 5G upgrade on the same stack",
     {"bsnl": 0.6, "bsnl_4g": 0.5}, ("bsnl", 1, 0.2, 0.65, "quarters", "Upgrade path extends TCS's scope on the BSNL network.")),
    (36, "demo", "news", "[Sample] Phoenix Group extends policy administration migration programme",
     {"phoenix": 0.5}, ("phoenix", 1, 0.08, 0.6, "quarters", "Small scope extension at a UK insurance client.")),
    (22, "demo", "news", "[Sample] JLR restarts UK assembly lines after weeks-long shutdown",
     {"jlr": 0.5}, ("jlr", 1, 0.15, 0.6, "weeks", "Client recovery eases risk to TCS's manufacturing revenue.")),
    (14, "demo", "news", "[Sample] Seksaria says margins should improve in H2 on pricing and utilisation",
     {"seksaria": 0.4, "TCS": 0.3}, ("seksaria", 1, 0.12, 0.55, "weeks", "CFO margin commentary; modestly positive.")),
    (4, "demo", "news", "[Sample] Marks & Spencer reviews IT vendor arrangements after cyber incident probe",
     {"mands": -0.5}, ("mands", -1, 0.12, 0.6, "weeks", "Small client; possible scope loss for TCS.")),
]

DRIP = [
    ("[Sample] Nifty IT index rises as US bank tech budgets surprise to the upside",
     {"it_sector": 0.5, "us_bfsi": 0.5}, ("us_bfsi", 1, 0.3, 0.7, "days", "Largest vertical's spend outlook improving.")),
    ("[Sample] HCLTech flags project ramp-downs at a large telecom client",
     {"hcltech": -0.5}, ("hcltech", 1, 0.08, 0.5, "days", "Peer weakness; mostly company-specific, mild sector read-through.")),
    ("[Sample] Tata Sons board reviews group capital allocation plan",
     {"tata_sons": 0.0}, ("tata_sons", 1, 0.03, 0.4, "days", "Routine group governance item; negligible for TCS.")),
    ("[Sample] TCS bags multi-year cloud transformation deal from UK insurer",
     {"TCS": 0.6}, ("TCS", 1, 0.4, 0.75, "weeks", "Direct order win; adds to deal TCV.")),
    ("[Sample] Tejas Networks reports delay in radio equipment deliveries for BSNL",
     {"tejas": -0.6, "bsnl_4g": -0.4}, ("bsnl_4g", -1, 0.2, 0.6, "weeks", "Partner delay could slow BSNL 4G milestones TCS bills against.")),
]


class DemoScorer:
    """Returns the pre-set score attached to a demo item by title."""

    name = "demo"

    def __init__(self, graph):
        self.graph = graph
        self.table = {s[3]: (s[4], s[5]) for s in SAMPLES} | {d[0]: (d[1], d[2]) for d in DRIP}

    async def score(self, item: RawItem, matched: list[str]) -> ScoreResult:
        ents, (via, direction, mag, conf, horizon, reason) = self.table[item.title]
        path = self.graph.best_path(via, "TCS")
        return ScoreResult(
            relevant=True,
            event_type="other",
            entity_impacts=[(e, s, 0.7) for e, s in ents.items()],
            ticker_impacts=[TickerImpactOut("TCS", via, float(direction), mag, conf, horizon, reason,
                                            path.nodes if path else [via, "TCS"])],
            scorer="demo",
        )


def _item(title: str, hours_ago: float, kind: str = "news", entities=None) -> RawItem:
    return RawItem("demo", kind, title, "", now_utc() - timedelta(hours=hours_ago),
                   summary="Invented sample for offline demo.", hint_entities=list(entities or []))


CALENDAR = [
    (6, "bsnl_4g", "[Sample] BSNL 5G pilot milestone review"),
    (21, "accenture", "[Sample] Accenture quarterly results (bellwether read-through)"),
    (84, "TCS", "[Sample] Board meeting: quarterly results and dividend"),
]


async def seed(engine) -> None:
    from sqlalchemy import select

    from .db import Session
    from .models import CalendarEvent

    await engine.ingest([_item(s[3], s[0], s[2], s[4].keys()) for s in SAMPLES], max_age=None)
    async with Session() as s:
        for days, entity, title in CALENDAR:
            fp = f"demo:{entity}:{days}"
            if not await s.scalar(select(CalendarEvent.id).where(CalendarEvent.fingerprint == fp)):
                s.add(CalendarEvent(fingerprint=fp, entity_id=entity, date=now_utc() + timedelta(days=days),
                                    title=title, source="demo"))
        await s.commit()


async def drip(engine, every: float = 25.0) -> None:
    """Emit one sample item at a time so the live push path is visible."""
    pending = DRIP[:]
    random.shuffle(pending)
    while pending:
        await asyncio.sleep(every)
        title, ents, _ = pending.pop()
        await engine.ingest([_item(title, 0, "news", ents.keys())], max_age=None)
