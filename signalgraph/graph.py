"""Relationship graph: entities, links, alias matching and paths to tracked stocks."""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Entity:
    id: str
    name: str
    type: str
    aliases: list[str]
    context: list[str] = field(default_factory=list)
    query: str | None = None
    role: str | None = None
    ticker: str | None = None
    nse_symbol: str | None = None
    nse_token: str | None = None
    bse_code: str | None = None

    @property
    def search_query(self) -> str:
        return self.query or f'"{self.aliases[0]}"'


@dataclass
class Link:
    src: str
    dst: str
    relation: str
    weight: float
    sign: int


@dataclass
class Path_:
    """A route from a news entity to a tracked stock."""

    nodes: list[str]  # [source, ..., ticker entity]
    relations: list[str]
    weight: float  # product of link weights
    sign: int  # product of link signs

    def describe(self, graph: "Graph") -> str:
        parts = [graph.entities[self.nodes[0]].name]
        for rel, node in zip(self.relations, self.nodes[1:]):
            parts.append(f"--{rel}--> {graph.entities[node].name}")
        return " ".join(parts)


def _word_pattern(alias: str) -> re.Pattern:
    # \b fails next to symbols like "&", so use look-arounds on alphanumerics.
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE)


class Graph:
    MAX_DEPTH = 3

    def __init__(self, entities: dict[str, Entity], links: list[Link], tickers: list[str]):
        self.entities = entities
        self.links = links
        self.tickers = tickers  # entity ids that are tracked stocks
        self.out: dict[str, list[Link]] = defaultdict(list)
        for link in links:
            self.out[link.src].append(link)
        self._patterns = {
            e.id: ([_word_pattern(a) for a in e.aliases], [_word_pattern(c) for c in e.context])
            for e in entities.values()
        }
        self._paths = {e: self._find_paths(e) for e in entities}

    @classmethod
    def load(cls, config_dir: Path) -> "Graph":
        universe = yaml.safe_load((config_dir / "universe.yaml").read_text())
        entities: dict[str, Entity] = {}
        links: dict[tuple[str, str], Link] = {}
        tickers = []
        for t in universe["tickers"]:
            data = yaml.safe_load((config_dir / t["graph"]).read_text())
            for raw in data["entities"]:
                ent = Entity(**raw)
                if ent.id in entities:  # shared node across graphs: merge aliases
                    entities[ent.id].aliases = sorted(set(entities[ent.id].aliases) | set(ent.aliases))
                else:
                    entities[ent.id] = ent
            for raw in data.get("links", []):
                link = Link(raw["from"], raw["to"], raw["relation"], float(raw["weight"]), int(raw.get("sign", 1)))
                links[(link.src, link.dst)] = link
            tickers.append(t["symbol"])
        for link in links.values():
            missing = {link.src, link.dst} - entities.keys()
            if missing:
                raise ValueError(f"link {link.src}->{link.dst} references unknown entities {missing}")
        return cls(entities, list(links.values()), tickers)

    def match(self, text: str) -> list[str]:
        """Entity ids mentioned in text (respecting each entity's context words)."""
        hits = []
        for eid, (aliases, context) in self._patterns.items():
            if any(p.search(text) for p in aliases):
                if context and not any(p.search(text) for p in context):
                    continue
                hits.append(eid)
        return hits

    def _find_paths(self, start: str) -> list[Path_]:
        found: list[Path_] = []
        if start in self.tickers:
            found.append(Path_([start], [], 1.0, 1))

        def walk(node: str, nodes: list[str], rels: list[str], weight: float, sign: int):
            if len(rels) >= self.MAX_DEPTH:
                return
            for link in self.out.get(node, []):
                if link.dst in nodes:
                    continue
                p = Path_(nodes + [link.dst], rels + [link.relation], weight * link.weight, sign * link.sign)
                if link.dst in self.tickers:
                    found.append(p)
                walk(link.dst, p.nodes, p.relations, p.weight, p.sign)

        walk(start, [start], [], 1.0, 1)
        return found

    def paths_to_tickers(self, entity_id: str) -> list[Path_]:
        return self._paths.get(entity_id, [])

    def best_path(self, entity_id: str, ticker: str) -> Path_ | None:
        candidates = [p for p in self.paths_to_tickers(entity_id) if p.nodes[-1] == ticker]
        return max(candidates, key=lambda p: p.weight, default=None)

    def to_json(self) -> dict:
        return {
            "tickers": self.tickers,
            "nodes": [
                {"id": e.id, "name": e.name, "type": e.type, "role": e.role, "ticker": e.ticker}
                for e in self.entities.values()
            ],
            "edges": [
                {"source": l.src, "target": l.dst, "relation": l.relation, "weight": l.weight, "sign": l.sign}
                for l in self.links
            ],
        }
