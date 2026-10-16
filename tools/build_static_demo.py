"""Bake a running demo server into one self-contained HTML page (for sharing a preview).

    python -m signalgraph --demo --port 8077   # wait ~2 min so the sample drip finishes
    python tools/build_static_demo.py http://127.0.0.1:8077 out/signalgraph-demo.html
"""

import json
import re
import sys
import time
from pathlib import Path

import httpx

STATIC = Path(__file__).resolve().parent.parent / "signalgraph" / "api" / "static"
CDN = [
    "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js",
    "https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js",
]
# Hosted pages sit under the host's top bar: pin the sticky header below the safe area.
OVERRIDES = """
.appbar { top: env(safe-area-inset-top, 0px); padding-top: 8px; }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 25; background: var(--surface); }
.demo-note { font-size: 12px; color: var(--ink-2); background: var(--sunken); border: 1px dashed var(--border); border-radius: 10px; padding: 8px 12px; }
"""


def main(base: str, out: str) -> None:
    c = httpx.Client(base_url=base, timeout=30)
    get = lambda p: c.get(p).raise_for_status().json()  # noqa: E731
    meta = get("/api/meta")
    t = meta["tickers"][0]
    graph = get(f"/api/graph/{t}")
    news = get(f"/api/news?ticker={t}&limit=300")
    snap = {
        "taken_at": int(time.time()),
        "meta": meta,
        "graph": graph,
        "bars": get(f"/api/bars/{t}?days=5"),
        "signal": get(f"/api/signal/{t}?hours=120"),
        "news": news[2:],
        "replay": list(reversed(news[:2])),  # re-announced a few seconds after load to show live push
        "calendar": get("/api/calendar"),
        "health": get("/api/health"),
        "entities": {n["id"]: get(f"/api/entity/{n['id']}?ticker={t}") for n in graph["nodes"]},
    }
    html = (STATIC / "index.html").read_text()
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S).group(1)
    body = re.sub(r"<script[^>]*></script>", "", body)
    body = body.replace('<main id="main">', '<main id="main">\n<p class="demo-note">Demo: invented [Sample] headlines, synthetic prices. Tap any graph node, client or chip.</p>', 1)
    page = "\n".join([
        "<title>SignalGraph</title>",
        "<style>", (STATIC / "style.css").read_text(), OVERRIDES, "</style>",
        *[f'<script src="{u}"></script>' for u in CDN],
        "<script>window.SG_SNAPSHOT = " + json.dumps(snap, ensure_ascii=False).replace("</", "<\\/") + ";</script>",
        body,
        "<script>", (STATIC / "app.js").read_text(), "</script>",
    ])
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(page)
    print(f"wrote {out} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main(*sys.argv[1:3])
