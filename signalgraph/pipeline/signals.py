"""Turn scored impacts into red/green signals.

signal(t) = tanh( sum_i direction_i * magnitude_i * confidence_i * decay(t - t_i) )

Decay is exponential with a half-life set by the impact's horizon, so a
headline fades in hours while a lost contract weighs on the score for weeks.
tanh keeps the score in [-1, 1] and stops a pile of small items from looking
like one huge one.
"""

import math
from datetime import datetime, timedelta, timezone

HALF_LIFE_HOURS = {"intraday": 6, "days": 48, "weeks": 24 * 7, "quarters": 24 * 30}
LOOKBACK = timedelta(days=90)


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def decay(age_hours: float, horizon: str) -> float:
    if age_hours < 0:
        return 0.0
    return 0.5 ** (age_hours / HALF_LIFE_HOURS.get(horizon, 48))


def decayed(value: float, published: datetime, horizon: str, at: datetime) -> float:
    return value * decay((at - _aware(published)).total_seconds() / 3600, horizon)


def ticker_score(impacts, at: datetime) -> float:
    """impacts: iterable of TickerImpact rows."""
    return math.tanh(sum(decayed(i.impact, i.published_at, i.horizon, at) for i in impacts))


def entity_score(impacts, at: datetime) -> float:
    """impacts: iterable of EntityImpact rows. Entity sentiment uses a 'days' horizon."""
    return math.tanh(sum(decayed(i.sentiment * i.confidence, i.published_at, "days", at) for i in impacts))


def label(score: float, green: float, red: float) -> str:
    return "GREEN" if score >= green else "RED" if score <= red else "NEUTRAL"


def history(impacts, start: datetime, end: datetime, step: timedelta) -> list[tuple[datetime, float]]:
    impacts = list(impacts)
    out, t = [], start
    while t <= end:
        out.append((t, ticker_score([i for i in impacts if _aware(i.published_at) <= t], t)))
        t += step
    return out
