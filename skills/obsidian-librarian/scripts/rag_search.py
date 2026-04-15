#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from config import LibrarianSettings
from embedder import embed_single
from supabase_client import SupabaseClient


@dataclass
class SearchResult:
    file_path: str
    chunk_index: int
    content: str
    metadata: dict[str, Any]
    similarity: float


def search(
    settings: LibrarianSettings,
    query: str,
    *,
    category_filter: str | None = None,
    threshold: float = 0.5,
    limit: int = 5,
) -> list[SearchResult]:
    query_embedding = embed_single(
        api_key=settings.gemini_api_key,
        text=query,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )

    client = SupabaseClient(settings.supabase_url, settings.supabase_key)
    params: dict[str, Any] = {
        "query_embedding": json.dumps(query_embedding),
        "match_threshold": threshold,
        "match_count": limit,
    }
    if category_filter:
        params["filter_category"] = category_filter

    rows = client.rpc("match_vault_chunks", params)
    if not isinstance(rows, list):
        return []

    results: list[SearchResult] = []
    for row in rows:
        meta = row.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        results.append(SearchResult(
            file_path=row["file_path"],
            chunk_index=row["chunk_index"],
            content=row["content"],
            metadata=meta,
            similarity=float(row.get("similarity", 0)),
        ))

    return results
