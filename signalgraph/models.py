from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NewsItem(Base):
    """One deduplicated article or exchange filing."""

    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(64))  # nse, bse, google_news, ET Markets...
    kind: Mapped[str] = mapped_column(String(16))  # filing | news
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    matched_entities: Mapped[list] = mapped_column(JSON, default=list)
    scorer: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|scored|irrelevant|error

    entity_impacts: Mapped[list["EntityImpact"]] = relationship(back_populates="news", lazy="selectin")
    ticker_impacts: Mapped[list["TickerImpact"]] = relationship(back_populates="news", lazy="selectin")


class EntityImpact(Base):
    """How bad/good an item is for the entity it is about (colours graph nodes)."""

    __tablename__ = "entity_impacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_items.id", ondelete="CASCADE"))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    sentiment: Mapped[float] = mapped_column(Float)  # -1..1
    confidence: Mapped[float] = mapped_column(Float)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    news: Mapped[NewsItem] = relationship(back_populates="entity_impacts", lazy="joined")


class TickerImpact(Base):
    """What an item means for a tracked stock, via a path through the graph."""

    __tablename__ = "ticker_impacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_items.id", ondelete="CASCADE"))
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[float] = mapped_column(Float)  # -1..1
    magnitude: Mapped[float] = mapped_column(Float)  # 0..1
    confidence: Mapped[float] = mapped_column(Float)  # 0..1
    horizon: Mapped[str] = mapped_column(String(16))  # intraday|days|weeks|quarters
    path: Mapped[list] = mapped_column(JSON, default=list)  # entity ids, source -> ticker
    reason: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    news: Mapped[NewsItem] = relationship(back_populates="ticker_impacts", lazy="joined")

    @property
    def impact(self) -> float:
        return self.direction * self.magnitude * self.confidence


Index("ix_ticker_impacts_ticker_time", TickerImpact.ticker, TickerImpact.published_at)
Index("ix_entity_impacts_entity_time", EntityImpact.entity_id, EntityImpact.published_at)


class PriceBar(Base):
    """1-minute OHLCV bars built from ticks (or fetched candles)."""

    __tablename__ = "price_bars"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)


class CalendarEvent(Base):
    """Upcoming board meetings / results dates / project milestones."""

    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    entity_id: Mapped[str] = mapped_column(String(64))
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64))
