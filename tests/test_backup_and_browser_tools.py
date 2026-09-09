from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_backup_restore_script_is_fail_closed_and_does_not_prune_volumes() -> None:
    script = (ROOT / "scripts" / "verify_backup_restore.sh").read_text(encoding="utf-8")
    assert "docker volume prune" not in script
    assert "docker compose down -v" not in script
    assert "POSTGRES_IMAGE" in script
    assert "docker image inspect" in script
    assert "ON_ERROR_STOP=1" in script
    assert "alembic_version" in script
    assert "review_jobs" in script
    assert "admin_audit_logs" in script
    assert "volume rm \"$volume\"" in script


def test_admin_playwright_smoke_never_contains_credentials_or_write_actions() -> None:
    script = (ROOT / "scripts" / "admin_playwright_smoke.py").read_text(encoding="utf-8")
    assert "POST /" not in script
    assert "password" not in script.lower()
    assert "PLAYWRIGHT_STORAGE_STATE" in script
    assert "/health/live" in script
    assert "/admin/operations" in script
