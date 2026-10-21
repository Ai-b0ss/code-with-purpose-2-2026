import pytest


@pytest.fixture(autouse=True)
def legacy_api_without_portal_auth(monkeypatch):
    """Older API contract tests run in compatibility mode; portal tests opt in."""
    monkeypatch.setenv("GIM_AUTH_REQUIRED", "0")
    monkeypatch.setenv("GIM_DATABASE_URL", "")
