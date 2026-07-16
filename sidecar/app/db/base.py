"""Declarative base + timestamp plumbing (database-design §2)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    """The single declarative base for every app-schema table."""


def now_utc() -> datetime:
    """Timezone-aware UTC now — the default for every timestamp column."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store timestamps as naive UTC (SQLite drops tzinfo) but always return
    tz-aware UTC — so comparisons against `now_utc()` never mix naive and
    aware datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:  # type: ignore[override]
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # type: ignore[override]
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
