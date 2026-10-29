from __future__ import annotations

from typing import Any

import requests


# -----------------------------------------------------------------------------
# 2. КЛИЕНТ БЭКЕНДА FASTAPI
# -----------------------------------------------------------------------------

class GimApiClient:
    """Клиент для общения с FastAPI-сервером ГИМ."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: int = 210):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def check_health(self) -> dict | None:
        try:
            resp = requests.get(f"{self.base_url}/api/v1/health", timeout=3)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            pass
        return None

    def get_clarifications(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/clarification."""
        url = f"{self.base_url}/api/v1/clarification"
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def submit_application(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/applications."""
        url = f"{self.base_url}/api/v1/applications"
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

