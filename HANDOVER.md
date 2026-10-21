# Handover: SignalGraph

Written 2026-10-21 at the end of the cloud session that built Phase 1. Read this first, then `README.md`.

## What this is

A single-user, real-time dashboard for **Indian stocks (NSE/BSE)**. It turns news about a stock **and everything connected to it** into a red/green signal. Connected things include the CEO, projects, clients and their news, partners, competitors and macro themes.

- **Signals only, no auto-trading.** This is deliberate: it keeps the tool outside SEBI's retail algo-trading rules.
- **Second-order effects are the point.** Client news, government-budget news and partner delays are where an edge is plausible. Headline speed is not.
- **Broker:** Angel One SmartAPI.
- **Pilot stock:** TCS. The design is meant to scale to many stocks.

## Current state

| Area | Status |
|---|---|
| Relationship graph for TCS (`config/graphs/TCS.yaml`) | Done. 25 entities and 25 links, each entity with a description and key facts. Link weights are judgement calls. |
| NSE and BSE filings pollers | Written. **Never run against the live sites:** the cloud container blocked them. Endpoints and field names come from known public structure, and the parsers are unit-tested with fixtures. Expect small fixes on first live run. |
| Publisher RSS and per-entity Google News | Written, not live-tested (same reason). |
| Angel One live prices (websocket + 1-min candle backfill) | Written against the installed `smartapi-python` 1.5.5 source. **Never logged in with real credentials.** |
| Claude scoring (structured JSON output, `fallbacks="default"`) | Written, unit-tested with a mocked client. Never called the real API. |
| Keyword (lexicon) fallback scorer | Works; used when there's no API key. |
| Signal maths, storage, WebSocket push, Telegram alerts | Work in demo. Telegram is untested live. |
| Mobile-first UI with entity pages (Overview / News / Links tabs) | Done and checked in headless Chromium at phone and desktop sizes, light and dark. |
| Offline demo mode (`--demo`) | Works end to end. Uses invented `[Sample]` headlines. |
| Tests | 9 pass (`pytest -q`). |

A static snapshot of the demo is published at https://claude.ai/artifact/LnfRZAwE9Lt7oujWSjNsoV (private to the owner). Rebuild it with `tools/build_static_demo.py`.

## First things to do locally

1. `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
2. `pytest -q` should report 9 passed.
3. `python -m signalgraph --demo` and open http://127.0.0.1:8000 to confirm the UI.
4. `cp .env.example .env` and fill in `ANTHROPIC_API_KEY` and the four `ANGEL_*` values. Never commit `.env`.
5. `python -m signalgraph` (live). Open the **Events** tab, where the "Data sources" list shows each poller as green or red with its error. Fix whatever is red:
   - **NSE:** cookie warm-up in `sources/exchanges.py` (`NseClient`). NSE changes headers and anti-bot behaviour often.
   - **BSE:** check the `AnnSubCategoryGetData` params and field names (`HEADLINE`, `NEWSSUB`, `DissemDT`, `ATTACHMENTNAME`).
   - **Angel One:** login needs a TOTP generated from the base32 secret. The websocket only streams 9:15–15:30 IST. Prices arrive in **paise** (divided by 100 in `on_data`).
6. Watch a few real items get scored by Claude, and sanity-check direction and magnitude against your own judgement.

## Code map

```
config/universe.yaml        tickers -> graph files, publisher feeds, poll intervals
config/graphs/TCS.yaml      entities (aliases, context words, facts) + weighted links
signalgraph/settings.py     env config (pydantic-settings, reads .env)
signalgraph/graph.py        Graph: load/merge YAML, alias matching, paths to tickers
signalgraph/sources/        exchanges.py (NSE/BSE), rss.py (Google News, publishers), prices.py (Angel One, demo, BarBuilder)
signalgraph/pipeline/       scorer.py (Claude + lexicon), signals.py (decay/tanh score), engine.py (orchestration, Hub, health)
signalgraph/api/app.py      FastAPI routes + /ws; serves static/ (index.html, style.css, app.js, vendored libs)
signalgraph/demo.py         sample items, drip, sample calendar
tools/build_static_demo.py  bakes a running demo into one HTML file
tests/test_pipeline.py      graph, scorers, parsers, engine ingest
```

**Key formulas and decisions**

- **Ticker score** = `tanh(Σ direction × magnitude × confidence × 0.5^(age/half-life))`.
  - Half-lives: intraday 6 h, days 48 h, weeks 7 d, quarters 30 d.
  - GREEN ≥ 0.25, RED ≤ −0.25 (both configurable).
- **Entity node colour** = the same formula over that entity's own sentiment, using the "days" horizon.
- **Before any LLM call:**
  - items are deduped on the normalised title (Google News' "– Publisher" suffix is stripped);
  - items mentioning no graph entity are dropped;
  - items older than 72 h are ignored on first poll.
- **The LLM gets the graph paths and link weights** and judges the price impact on the stock rather than the tone. Its schema enums are limited to the matched entities and tracked tickers, and outputs are clamped in code.
- **Default model:** `claude-opus-5-5` at effort `low`, with server-side refusal fallbacks (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`). Change it with `SCORING_MODEL` and `SCORING_EFFORT`.
- **UI:** hash routes (`#/graph`, `#/e/<entity>/<tab>`) so the phone back button closes entity pages. Below 1100px it uses a bottom tab bar; at 1100px and above, one grid with a side drawer.

## Next work (agreed direction, not started)

The user asked how to scale beyond TCS to all stocks. The proposed plan:

1. **`graphgen <SYMBOL>` (do this first).**
   - Pull the company's filings from NSE/BSE: shareholding pattern, annual report (related-party transactions, subsidiaries), investor presentation, earnings call transcript, order-win filings.
   - Have Claude extract entities and links, each with a cited quote and a confidence level.
   - Write a draft graph YAML. Low-confidence links go to a review queue in the UI for approve or reject.
   - Test on a few Nifty 50 names (e.g. INFY, RELIANCE, HDFCBANK).
2. **Firehose ingestion** to replace per-entity searching:
   - pull the all-stock NSE/BSE announcement feeds plus all RSS and GDELT;
   - match with Aho-Corasick against every alias;
   - add a cheap model (Haiku) check for ambiguous names (Titan, Trent, Bharat…).
   - Per-entity Google News stays for the watchlist only.
3. **One scoring call per article** returning impacts for every affected ticker. Cap the fan-out from hub nodes (RBI, sectors).
4. **Symbol tokens for all stocks** from Angel One's instrument master (`OpenAPIScripMaster.json`). Stream the watchlist and Nifty 500 live, mind the per-connection subscription limits, and cover the rest with candle polling or end-of-day files.
5. **Scale-out:**
   - Redis Streams in place of the in-process `asyncio.Queue` (scoring) and the `Hub` (live push);
   - Postgres + TimescaleDB;
   - separate worker processes.
6. **New UI screens:** market heatmap by sector, a screener of the biggest signal swings, search, watchlists.
7. **Rollout:** Nifty 50 → Nifty 500 → all listed. Before trusting signals:
   - build a labelled set of about 200 headlines to measure scoring;
   - backtest signals against next-day and 5-day returns.

**Still open:** which universe Phase A covers (Nifty 50 or the user's own watchlist).

## Cautions

- NSE and BSE endpoints are unofficial website APIs. Keep poll intervals at 15 s or more, and use them for personal use only.
- Facts and weights in `TCS.yaml` reflect public information as of mid-2026, and "(est.)" marks guesses. Review them each quarter.
- Demo data is invented. Never present `[Sample]` items or demo prices as real.
