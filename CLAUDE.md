# SignalGraph: notes for Claude

Read `HANDOVER.md` first. It has the current state, what was never live-tested, and the agreed next steps.

## Commands

- Install: `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`
- Tests: `pytest -q` (must stay green; add tests next to `tests/test_pipeline.py`)
- Demo (offline, sample data): `python -m signalgraph --demo` → http://127.0.0.1:8000
- Live: `python -m signalgraph` (needs `.env`; see `.env.example`)
- Static demo page: run the demo, wait ~2 min, then `python tools/build_static_demo.py http://127.0.0.1:8000 out/signalgraph-demo.html`

## Rules for this project

- Signals only. Never add order placement or auto-trading.
- Never commit `.env` or any credentials (Angel One PIN/TOTP secret, API keys, Telegram token).
- Demo data must stay clearly marked: `[Sample]` titles, DEMO badges.
- Graph facts go in `config/graphs/*.yaml`. Mark guesses with "(est.)" and keep facts sourced from filings.
- Claude API code uses the official `anthropic` SDK. Default model `claude-opus-5-5`, structured JSON output, refusal fallbacks enabled (see `pipeline/scorer.py`).
- Front end is plain HTML/CSS/JS with vendored cytoscape 3.30.2 and lightweight-charts 4.2.3 (v4 API: `addCandlestickSeries`, `setMarkers`). There is no build step.
- Colours are tokens in `style.css` (`--good`, `--bad`, `--neutral`). Red/green always comes with ▲/▼ and a label, never colour alone.
- Keep the phone layout working: check at 390px width with no sideways scroll.
