"""Request plumbing shared by the client mixins (httpx only; no server imports)."""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx

THEORY_RE = re.compile(r'(?ms)^[ \t]*theory\s+(?:"([^"\n]+)"|([A-Za-z0-9_\'.-]+))')
BASE_URL = "/api/v1/sessions"
HEAPS_URL = "/api/v1/heaps"
HEAP_GROUPS_URL = "/api/v1/heap_groups"


def extract_theory_name(text: str) -> Optional[str]:
    m = THEORY_RE.search(text)
    if not m:
        return None
    return m.group(1) or m.group(2)


class ClientBase:
    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        # Memory tier the server reported on the LAST response (``X-Memory-State``:
        # ok / warning / critical; "unknown" before any call). MCP servers read
        # it after each call to remind their agent — no polling needed.
        self.last_memory_state: str = "unknown"

    async def __aenter__(self) -> "ClientBase":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        response = await self.client.request(
            method=method,
            url=path,
            json=json_body,
            headers=headers,
            timeout=timeout if timeout is not None else self.timeout,
        )
        state = response.headers.get("X-Memory-State")
        if state:
            self.last_memory_state = state
        return response

    @staticmethod
    def _lease_headers(lease_id: str | None) -> dict[str, str] | None:
        if lease_id:
            return {"X-Lease-Id": lease_id}
        return None

    async def _get_json(self, path: str, *, lease_id: str | None = None) -> dict[str, Any]:
        """GET + raise_for_status + json — the shape of every read-only call."""
        response = await self._request("GET", path, headers=self._lease_headers(lease_id))
        response.raise_for_status()
        return response.json()

    async def health(self) -> dict[str, Any]:
        return await self._get_json("/")

    async def symbols(self) -> dict[str, Any]:
        """Isabelle's symbol table as the server has it (``GET /api/v1/symbols``):
        ``{symbols: {"\\<le>": "≤", ...}, count, available, ascii_output}``.
        Use it to compare theory text the way Isabelle reads it instead of
        carrying a table of your own."""
        return await self._get_json("/api/v1/symbols")

    async def memory_status(self) -> dict[str, Any]:
        """Memory facts (``GET /api/v1/memory``): state, memory numbers, builds
        in flight, evictable / leased rows and every session's expiry + rss."""
        return await self._get_json("/api/v1/memory")
