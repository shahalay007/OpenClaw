#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from common import http_json_request, http_request


class SupabaseClient:
    def __init__(self, url: str, key: str) -> None:
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        self.base_url = url.rstrip("/")
        self.key = key
        self._headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

    def rpc(self, function_name: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}/rest/v1/rpc/{function_name}"
        return http_json_request(url, method="POST", headers=self._headers, json_body=params)

    def upsert(self, table: str, rows: list[dict[str, Any]], *, on_conflict: str) -> None:
        url = f"{self.base_url}/rest/v1/{table}"
        headers = {
            **self._headers,
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        # Supabase uses the on_conflict query param for upsert
        url += f"?on_conflict={on_conflict}"
        http_request(url, method="POST", headers=headers, json_body=rows)

    def delete(self, table: str, *, filters: dict[str, str]) -> None:
        url = f"{self.base_url}/rest/v1/{table}"
        query_parts = [f"{col}={op}" for col, op in filters.items()]
        if query_parts:
            url += "?" + "&".join(query_parts)
        http_request(url, method="DELETE", headers=self._headers)
