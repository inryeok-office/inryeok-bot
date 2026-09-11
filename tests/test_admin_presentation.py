from datetime import UTC, datetime

from app.admin.presentation import format_admin_datetime


def test_admin_datetime_is_rendered_as_kst_without_database_precision() -> None:
    value = datetime(2026, 9, 11, 0, 33, 24, 619517, tzinfo=UTC)
    assert format_admin_datetime(value) == "2026. 9. 11. 09:33"


def test_admin_datetime_treats_legacy_naive_values_as_utc() -> None:
    assert format_admin_datetime(datetime(2026, 1, 2, 15, 4)) == "2026. 1. 3. 00:04"
