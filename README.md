# SignalGraph

A real-time dashboard that turns news about a stock **and everything around it** (CEO, projects, clients, partners, competitors, macro themes) into a red/green signal. It shows signals only and never places trades.

It starts with **TCS**. Adding a stock means adding config, not code.

## What you see

- **Relationship graph.** TCS sits in the centre, surrounded by its people, projects, clients, partners, competitors and macro themes. Each node is coloured by its own recent news (red, grey or green). Links carry a weight for how exposed TCS is to that node, and competitor links are dashed. Click a node to see its news. New items flash along the path they travel to TCS.
- **"Why this colour" panel.** Lists the top items currently driving the score, the path each one took (e.g. *Tejas Networks → BSNL 4G project → TCS*) and a one-line reason.
- **Price chart.** 1-minute candles from Angel One, with ▲/▼ markers where news landed. Hover a marker's bar to read the headline.
- **Signal score chart.** The score over time, from −1 to +1, with the green and red thresholds drawn as lines.
- **Live feed.** News and NSE/BSE filings, each with its impact chip, path and reason.
- **Upcoming events and source health.** Board meetings from NSE, plus whether each data source is currently working.

## How it works

```
NSE filings (20s) ─┐
BSE filings (20s) ─┤                                 ┌─ Claude: impact on each stock via graph paths
Publisher RSS (90s)┼─► dedupe ─► alias match ─► queue┤   (falls back to keyword scorer)
Google News (per   │   (title    (drop items          └─► store ─► decayed score ─► WebSocket push ─► dashboard
 entity, 3 min)   ─┘    hash)     about no entity)                                └─► Telegram alert
Angel One websocket ─► 1-min bars ─────────────────────────────────────────────────► WebSocket push
```

**Score** = `tanh( Σ direction × magnitude × confidence × decay )`. Decay is exponential, and its half-life depends on the impact's horizon: 6 h for intraday, 2 d for days, 7 d for weeks, 30 d for quarters. A headline therefore fades within hours, while a lost contract weighs on the score for weeks. The default thresholds are ±0.25.

**Second-order effects.** Claude receives the item, the entities it mentions, and each entity's path and link weight to TCS. It reasons about the stock rather than the article's tone: a competitor's lost deal can be mildly green, and a weaker rupee is green for IT exporters. The response comes back as JSON that must follow a fixed schema (structured outputs). Items that mention nothing in the graph are dropped before any API call, which keeps the cost low.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 1. Offline demo with invented [Sample] headlines and random-walk prices:
python -m signalgraph --demo            # open http://127.0.0.1:8000

# 2. Live:
cp .env.example .env                    # fill in the Anthropic key and Angel One details
python -m signalgraph
```

The header badges always tell you whether prices are **LIVE** or **DEMO**, and which scorer is running.

- Without Angel One details, news is still pulled live but prices are synthetic.
- Without an Anthropic key, scoring falls back to keyword matching. It works, but it can't reason about context.

**Angel One setup.** Create an app at smartapi.angelbroking.com to get an API key. Enable TOTP and copy the base32 secret into `ANGEL_TOTP_SECRET`. `ANGEL_PIN` is your login PIN. The websocket streams only during market hours (9:15–15:30 IST). On startup the last 5 days of 1-minute candles are backfilled.

**Docker / VPS:** `docker compose up -d` runs the app with Postgres. The port is bound to localhost only.

## Add a stock

1. Write `config/graphs/INFY.yaml` with its entities (aliases, Angel One `nse_token`, `bse_code`) and links. Use `config/graphs/TCS.yaml` as a template. Entities with the same `id` are shared across graphs (e.g. `infosys`).
2. Add `- {symbol: INFY, graph: graphs/INFY.yaml}` under `tickers` in `config/universe.yaml`.

The ticker symbol must equal the company entity's `id`. You can find Angel One symbol tokens in their instrument master file (`OpenAPIScripMaster.json`).

## Keeping the graph honest

Client revenue shares are mostly undisclosed, so link weights are judgement calls (0.6 high, 0.35 medium, 0.15 low). Review `TCS.yaml` every quarter against the annual report, investor deck and earnings call transcripts. The client and project entries reflect what was public as of mid-2026.

## Scaling seams

- **Storage:** SQLite → Postgres by setting `DATABASE_URL`. Bars and impacts are indexed by ticker and time.
- **Scoring:** an in-process `asyncio.Queue` with N concurrent workers (`SCORING_CONCURRENCY`). For many tickers, swap it for Redis/NATS and run scorers as separate processes.
- **Live push:** the `Hub` fans out in-process. Swap it for Redis pub/sub to run several API processes.
- **Sources:** each one is an independent poller with jittered intervals and exponential backoff on errors. Health is shown in the UI.

## Caveats

- NSE and BSE endpoints are the exchanges' unofficial website APIs. They need browser headers and cookies, rate-limit, and can change without notice. Keep intervals at 15 s or more and use them for personal use only.
- Scores are a reading aid, not a trading system. Before trusting thresholds, log signals against next-day and 5-day returns (Phase 3).

## Tests

```bash
pytest -q
```
