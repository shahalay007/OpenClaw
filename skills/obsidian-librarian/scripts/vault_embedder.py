#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from config import LibrarianSettings
from embedder import embed_texts
from supabase_client import SupabaseClient
from vault_reader import VaultChunk, read_all_vault_files, read_vault_file

BATCH_SIZE = 50


def _chunk_to_row(chunk: VaultChunk, embedding: list[float]) -> dict:
    return {
        "file_path": chunk.file_path,
        "chunk_index": chunk.chunk_index,
        "content": chunk.content,
        "embedding": json.dumps(embedding),
        "metadata": json.dumps(chunk.metadata),
    }


def embed_and_upsert(
    settings: LibrarianSettings,
    chunks: list[VaultChunk],
    client: SupabaseClient,
) -> int:
    if not chunks:
        return 0

    upserted = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [c.content for c in batch]

        embeddings = embed_texts(
            api_key=settings.gemini_api_key,
            texts=texts,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )

        rows = [
            _chunk_to_row(chunk, emb)
            for chunk, emb in zip(batch, embeddings)
        ]
        client.upsert("vault_chunks", rows, on_conflict="file_path,chunk_index")
        upserted += len(rows)
        print(f"  upserted {upserted}/{len(chunks)} chunks", file=sys.stderr)

    return upserted


def reindex_full_vault(settings: LibrarianSettings) -> int:
    client = SupabaseClient(settings.supabase_url, settings.supabase_key)
    chunks = read_all_vault_files(settings.vault_path, settings.inbox_folder)
    print(f"Read {len(chunks)} chunks from vault", file=sys.stderr)
    upserted = embed_and_upsert(settings, chunks, client)

    # Clean up stale chunks from files that no longer exist in the vault
    vault_paths = {c.file_path for c in chunks}
    _delete_stale_chunks(client, vault_paths)

    return upserted


def _delete_stale_chunks(client: SupabaseClient, current_paths: set[str]) -> None:
    try:
        rows = client.select("vault_chunks", columns="file_path")
        if not isinstance(rows, list):
            return
        db_paths = {row["file_path"] for row in rows if "file_path" in row}
        stale = db_paths - current_paths
        for path in stale:
            client.delete("vault_chunks", filters={"file_path": f"eq.{path}"})
            print(f"  removed stale chunks for {path}", file=sys.stderr)
    except Exception as exc:
        print(f"  stale cleanup failed: {exc}", file=sys.stderr)


def reindex_file(settings: LibrarianSettings, file_path: Path) -> int:
    client = SupabaseClient(settings.supabase_url, settings.supabase_key)
    relative = str(file_path.relative_to(settings.vault_path))

    chunks = read_vault_file(file_path, settings.vault_path)
    if not chunks:
        # File is empty or unreadable — remove any existing chunks
        client.delete("vault_chunks", filters={"file_path": f"eq.{relative}"})
        return 0

    # Upsert new chunks first, then delete stale leftovers (safe ordering)
    upserted = embed_and_upsert(settings, chunks, client)
    new_max_index = len(chunks)
    client.delete("vault_chunks", filters={
        "file_path": f"eq.{relative}",
        "chunk_index": f"gte.{new_max_index}",
    })
    return upserted


if __name__ == "__main__":
    settings = LibrarianSettings.from_env()
    if not settings.gemini_api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")
    total = reindex_full_vault(settings)
    print(f"Done. Indexed {total} chunks.", file=sys.stderr)
