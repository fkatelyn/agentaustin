import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship


def _get_database_url() -> tuple[str, bool]:
    """Returns (url, is_sqlite)."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return "sqlite+aiosqlite:///./agent311_local.db", True
    # Railway provides postgres:// but asyncpg needs postgresql+asyncpg://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url, False


engine = None
async_session = None
_is_sqlite = False


def _init_engine():
    global engine, async_session, _is_sqlite
    if engine is None:
        url, _is_sqlite = _get_database_url()
        kwargs = {"echo": False}
        if _is_sqlite:
            kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_async_engine(url, **kwargs)
        async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(255), nullable=False, default="New Chat")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    is_favorite = Column(Boolean, nullable=False, server_default=text("false"), default=False)

    messages = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False, default="")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        default=lambda: datetime.now(timezone.utc),
    )

    session = relationship("Session", back_populates="messages")


async def create_tables():
    _init_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if not _is_sqlite:
            # Add column if missing for pre-existing Postgres DBs (SQLite gets it via create_all)
            try:
                await conn.execute(text(
                    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_favorite BOOLEAN NOT NULL DEFAULT false"
                ))
            except Exception:
                pass


def get_async_session():
    """Return an async session factory (initializing the engine if needed)."""
    _init_engine()
    return async_session


async def get_db():
    _init_engine()
    async with async_session() as session:
        yield session
