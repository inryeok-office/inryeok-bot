"""Presentation helpers for the administrator console.

These helpers keep display formatting out of templates while leaving the
stored timestamps and canonical policy values untouched.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


def format_admin_datetime(value: datetime | None) -> str:
    """Render a stored UTC timestamp as a compact, human-readable KST value."""

    if value is None:
        return "기록 없음"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    local = aware.astimezone(_KST)
    return f"{local.year}. {local.month}. {local.day}. {local:%H:%M}"
