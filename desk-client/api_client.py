from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any


class ApiError(RuntimeError):
    """Readable error returned by the Service Desk API client."""


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.url(path), data=body, method=method.upper())
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"Сервер отклонил запрос ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise ApiError(f"API недоступно: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ApiError("Истекло время ожидания ответа API") from exc

    def health(self) -> dict[str, str]:
        return self.request("GET", "/health")

    def tickets(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.request("GET", f"/api/v1/tickets?limit={limit}&offset={offset}")

    def ticket(self, ticket_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/tickets/{ticket_id}")

    def change_status(self, ticket_id: int, status: str, comment: str = "") -> dict[str, str]:
        return self.request("POST", f"/api/v1/tickets/{ticket_id}/status", {"new_status": status, "comment": comment})

    def engineer_load(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/v1/reports/engineer-load")

    def asset(self, asset_id: int) -> dict[str, Any]:
        return self.request("GET", f"/api/v1/assets/{asset_id}")

    def users(self) -> list[dict[str, Any]]:
        return self.request("GET", "/api/v1/admin/users")
