"""Price feeds: Angel One SmartAPI (live) and a synthetic demo feed.

Both push Tick objects onto an asyncio queue; BarBuilder turns ticks into
1-minute bars. Angel One's SDK is synchronous and its websocket blocks, so it
runs in a thread and hands ticks back to the event loop thread-safely.
"""

import asyncio
import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .base import IST

log = logging.getLogger(__name__)


@dataclass
class Tick:
    ticker: str
    ts: datetime
    price: float
    day_volume: float | None = None  # cumulative volume for the day, if the feed has it


@dataclass
class Bar:
    ticker: str
    ts: datetime  # minute start, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class PriceFeed:
    name = "base"
    live = False

    async def backfill(self, days: int) -> list[Bar]:
        return []

    async def run(self, out: asyncio.Queue) -> None:
        raise NotImplementedError


class AngelOneFeed(PriceFeed):
    name = "angelone"
    live = True
    EXCHANGE_TYPE_NSE_CM = 1
    MODE_QUOTE = 2  # LTP + OHLC + cumulative volume

    def __init__(self, api_key: str, client_code: str, pin: str, totp_secret: str, tokens: dict[str, str]):
        self.api_key, self.client_code, self.pin, self.totp_secret = api_key, client_code, pin, totp_secret
        self.tokens = tokens  # ticker -> NSE symbol token
        self.by_token = {v: k for k, v in tokens.items()}
        self._smart = None
        self._session = None

    def _login(self):
        import pyotp
        from SmartApi import SmartConnect

        smart = SmartConnect(api_key=self.api_key)
        session = smart.generateSession(self.client_code, self.pin, pyotp.TOTP(self.totp_secret).now())
        if not session or not session.get("status"):
            raise RuntimeError(f"Angel One login failed: {session.get('message') if session else 'no response'}")
        self._smart, self._session = smart, session["data"]
        log.info("Angel One session started for %s", self.client_code)

    async def backfill(self, days: int) -> list[Bar]:
        await asyncio.to_thread(self._login)
        bars: list[Bar] = []
        end = datetime.now(IST)
        start = end - timedelta(days=days)
        for ticker, token in self.tokens.items():
            resp = await asyncio.to_thread(
                self._smart.getCandleData,
                {
                    "exchange": "NSE",
                    "symboltoken": token,
                    "interval": "ONE_MINUTE",
                    "fromdate": start.strftime("%Y-%m-%d %H:%M"),
                    "todate": end.strftime("%Y-%m-%d %H:%M"),
                },
            )
            for row in (resp or {}).get("data") or []:
                ts = datetime.fromisoformat(row[0]).astimezone(timezone.utc)
                bars.append(Bar(ticker, ts, *map(float, row[1:5]), float(row[5])))
        return bars

    async def run(self, out: asyncio.Queue) -> None:
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2

        loop = asyncio.get_running_loop()
        while True:
            if self._session is None:
                await asyncio.to_thread(self._login)
            sws = SmartWebSocketV2(
                self._session["jwtToken"], self.api_key, self.client_code, self._smart.getfeedToken(),
                max_retry_attempt=5,
            )
            token_list = [{"exchangeType": self.EXCHANGE_TYPE_NSE_CM, "tokens": list(self.tokens.values())}]

            def on_open(_ws):
                sws.subscribe("signalgrph", self.MODE_QUOTE, token_list)

            def on_data(_ws, msg):
                ticker = self.by_token.get(str(msg.get("token")))
                if not ticker or "last_traded_price" not in msg:
                    return
                tick = Tick(
                    ticker,
                    datetime.fromtimestamp(msg["exchange_timestamp"] / 1000, timezone.utc),
                    msg["last_traded_price"] / 100,  # feed is in paise
                    msg.get("volume_trade_for_the_day"),
                )
                loop.call_soon_threadsafe(out.put_nowait, tick)

            def on_error(*args):
                log.warning("Angel One websocket error: %s", args[-1] if args else "")

            sws.on_open, sws.on_data, sws.on_error = on_open, on_data, on_error
            await asyncio.to_thread(sws.connect)  # blocks until the socket closes
            log.warning("Angel One websocket closed; reconnecting in 10s")
            self._session = None  # re-login: tokens may have expired
            await asyncio.sleep(10)


class DemoFeed(PriceFeed):
    """Random-walk prices so the dashboard works offline. Clearly flagged in the UI."""

    name = "demo"

    def __init__(self, tickers: list[str], base: float = 3100.0, seed: int = 7):
        self.tickers = tickers
        self.rng = random.Random(seed)
        self.price = {t: base for t in tickers}

    def _step(self, ticker: str) -> float:
        self.price[ticker] *= math.exp(self.rng.gauss(0, 0.0007))
        return round(self.price[ticker], 2)

    async def backfill(self, days: int) -> list[Bar]:
        bars = []
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        for ticker in self.tickers:
            for i in range(days * 24 * 60 // 4, 0, -1):  # one bar per 4 minutes keeps history light
                ts = now - timedelta(minutes=4 * i)
                o = self.price[ticker]
                path = [self._step(ticker) for _ in range(4)]
                bars.append(Bar(ticker, ts, o, max(o, *path), min(o, *path), path[-1], self.rng.randint(2000, 9000)))
        return bars

    async def run(self, out: asyncio.Queue) -> None:
        volume = {t: 0.0 for t in self.tickers}
        while True:
            for t in self.tickers:
                volume[t] += self.rng.randint(50, 600)
                await out.put(Tick(t, datetime.now(timezone.utc), self._step(t), volume[t]))
            await asyncio.sleep(1)


class BarBuilder:
    """Aggregates ticks into 1-minute bars; returns finished bars as minutes roll."""

    def __init__(self):
        self.current: dict[str, Bar] = {}
        self.last_day_volume: dict[str, float] = {}

    def add(self, tick: Tick) -> tuple[Bar, Bar | None]:
        minute = tick.ts.replace(second=0, microsecond=0)
        vol = 0.0
        if tick.day_volume is not None:
            prev = self.last_day_volume.get(tick.ticker)
            vol = max(tick.day_volume - prev, 0.0) if prev is not None else 0.0
            self.last_day_volume[tick.ticker] = tick.day_volume
        bar = self.current.get(tick.ticker)
        finished = None
        if bar is None or minute > bar.ts:
            finished = bar
            bar = Bar(tick.ticker, minute, tick.price, tick.price, tick.price, tick.price, vol)
            self.current[tick.ticker] = bar
        else:
            bar.high, bar.low = max(bar.high, tick.price), min(bar.low, tick.price)
            bar.close = tick.price
            bar.volume += vol
        return bar, finished
