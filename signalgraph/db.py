from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base
from .settings import settings

if settings.database_url.startswith("sqlite"):
    Path(settings.database_url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
