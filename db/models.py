from __future__ import annotations
import json
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, Boolean, Date, DateTime, Text,
    UniqueConstraint, select
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL
import os

os.makedirs("data", exist_ok=True)

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("ticker", "date"),)

    id      = Column(Integer, primary_key=True, autoincrement=True)
    ticker  = Column(String(10), nullable=False, index=True)
    date    = Column(Date, nullable=False, index=True)
    open    = Column(Float)
    high    = Column(Float)
    low     = Column(Float)
    close   = Column(Float, nullable=False)
    volume  = Column(Float)


class MacroData(Base):
    __tablename__ = "macro_data"
    __table_args__ = (UniqueConstraint("date"),)

    id            = Column(Integer, primary_key=True, autoincrement=True)
    date          = Column(Date, nullable=False, index=True)
    brent         = Column(Float)
    usd_rub       = Column(Float)
    cbr_rate      = Column(Float)
    imoex         = Column(Float)
    imoex_ma50    = Column(Float)
    market_regime = Column(String(10), default="neutral")


class Dividend(Base):
    __tablename__ = "dividends"
    __table_args__ = (UniqueConstraint("ticker", "ex_date"),)

    id       = Column(Integer, primary_key=True, autoincrement=True)
    ticker   = Column(String(10), nullable=False, index=True)
    ex_date  = Column(Date, nullable=False)
    amount   = Column(Float)
    currency = Column(String(5), default="RUB")


class User(Base):
    __tablename__ = "users"

    user_id        = Column(Integer, primary_key=True)
    chat_id        = Column(Integer, nullable=False)
    settings_json  = Column(Text, default="{}")
    created_at     = Column(DateTime, default=datetime.utcnow)

    def get_settings(self) -> dict:
        return json.loads(self.settings_json or "{}")

    def set_settings(self, d: dict):
        self.settings_json = json.dumps(d, ensure_ascii=False)

    @property
    def alerts_enabled(self) -> bool:
        return self.get_settings().get("alerts", True)

    @property
    def brief_enabled(self) -> bool:
        return self.get_settings().get("brief", True)

    @property
    def rsi_alerts(self) -> bool:
        return self.get_settings().get("rsi_alerts", True)

    @property
    def news_alerts(self) -> bool:
        return self.get_settings().get("news_alerts", True)

    @property
    def bank_size(self) -> float | None:
        return self.get_settings().get("bank_size")


class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("user_id", "ticker"),)

    id      = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    ticker  = Column(String(10), nullable=False)


class SignalHistory(Base):
    __tablename__ = "signal_history"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    ticker      = Column(String(10), nullable=False, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow, index=True)
    signal      = Column(String(10))   # BUY / HOLD / SELL / WAIT
    confidence  = Column(String(10))   # HIGH / MEDIUM / LOW
    score_tech  = Column(Float)
    score_fund  = Column(Float)
    roi         = Column(Float)
    price       = Column(Float)


class NewsCache(Base):
    __tablename__ = "news_cache"
    __table_args__ = (UniqueConstraint("ticker", "url"),)

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ticker       = Column(String(10), nullable=False, index=True)
    title        = Column(Text, nullable=False)
    source       = Column(String(100), default="")
    url          = Column(Text, default="")
    published_at = Column(DateTime)
    fetched_at   = Column(DateTime, default=datetime.utcnow)


class AICache(Base):
    __tablename__ = "ai_cache"
    __table_args__ = (UniqueConstraint("ticker"),)

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ticker       = Column(String(10), nullable=False, index=True)
    analysis     = Column(Text, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow)


async def init_db():
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Миграции — добавляем новые столбцы если их ещё нет
        for sql in [
            "ALTER TABLE news_cache ADD COLUMN source VARCHAR(100) DEFAULT ''",
            "ALTER TABLE macro_data ADD COLUMN imoex_ma50 REAL",
            "ALTER TABLE macro_data ADD COLUMN market_regime VARCHAR(10) DEFAULT 'neutral'",
            "CREATE TABLE IF NOT EXISTS ai_cache (id INTEGER PRIMARY KEY, ticker VARCHAR(10) UNIQUE NOT NULL, analysis TEXT NOT NULL, created_at DATETIME)",
        ]:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # столбец уже существует


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
