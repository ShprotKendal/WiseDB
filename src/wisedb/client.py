"""HTTP-клиент сервера WiseDB (MCP, скрипты)."""

from __future__ import annotations

from typing import Any, Optional

import httpx


class WiseDBClient:
    """Тонкая обёртка над эндпоинтами ``/v1``."""

    def __init__(
        self,
        base_url: str,
        *,
        database: str = "default",
        password: str = "",
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._owns_client = http_client is None
        headers: dict[str, str] = {
            "X-WiseDB-Database": database,
            "X-WiseDB-Password": password,
        }
        if api_key:
            headers["X-API-Key"] = api_key
        if http_client is not None:
            self._client = http_client
            return
        self._client = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> WiseDBClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def create_database(
        self,
        db_id: str,
        password: str,
        *,
        name: Optional[str] = None,
        admin_api_key: Optional[str] = None,
    ) -> dict[str, Any]:
        headers = {}
        if admin_api_key:
            headers["X-API-Key"] = admin_api_key
        r = self._client.post(
            "/v1/databases",
            json={"id": db_id, "password": password, "name": name or db_id},
            headers=headers,
        )
        r.raise_for_status()
        return r.json()

    def list_databases(self, *, admin_api_key: Optional[str] = None) -> list[dict[str, Any]]:
        headers = {}
        if admin_api_key:
            headers["X-API-Key"] = admin_api_key
        r = self._client.get("/v1/databases", headers=headers)
        r.raise_for_status()
        return r.json()

    def upsert_chunks(self, *, tenant_id: str = "default", chunks: list[dict[str, Any]]) -> dict[str, Any]:
        r = self._client.post("/v1/chunks/upsert", json={"tenant_id": tenant_id, "chunks": chunks})
        r.raise_for_status()
        return r.json()

    def upsert_edges(self, *, tenant_id: str = "default", edges: list[dict[str, Any]]) -> dict[str, Any]:
        r = self._client.post("/v1/edges/upsert", json={"tenant_id": tenant_id, "edges": edges})
        r.raise_for_status()
        return r.json()

    def retrieve(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post("/v1/retrieve", json=payload)
        r.raise_for_status()
        return r.json()

    def list_chunks(self, *, tenant_id: str = "default", limit: int = 200) -> list[dict[str, Any]]:
        r = self._client.get("/v1/chunks", params={"tenant_id": tenant_id, "limit": limit})
        r.raise_for_status()
        return r.json()

    def lookup_chunk(self, external_key: str, *, tenant_id: str = "default") -> dict[str, Any]:
        r = self._client.get(
            "/v1/chunks/lookup",
            params={"tenant_id": tenant_id, "external_key": external_key},
        )
        r.raise_for_status()
        return r.json()

    def get_chunk(self, chunk_id: str, *, tenant_id: str = "default") -> dict[str, Any]:
        r = self._client.get(f"/v1/chunks/{chunk_id}", params={"tenant_id": tenant_id})
        r.raise_for_status()
        return r.json()

    def delete_chunk(self, chunk_id: str, *, tenant_id: str = "default") -> dict[str, Any]:
        r = self._client.delete(f"/v1/chunks/{chunk_id}", params={"tenant_id": tenant_id})
        r.raise_for_status()
        return r.json()

    def healthz(self) -> dict[str, Any]:
        r = self._client.get("/healthz")
        r.raise_for_status()
        return r.json()

    def readyz(self) -> dict[str, Any]:
        r = self._client.get("/readyz")
        r.raise_for_status()
        return r.json()
