#!/usr/bin/env python3
from __future__ import annotations

import time

from common import (
    build_gemini_model_url,
    build_vertex_model_url,
    http_json_request,
    is_retryable_error,
)


def embed_texts(
    *,
    api_key: str,
    texts: list[str],
    model: str = "gemini-embedding-001",
    dimensions: int = 384,
    vertex_project: str | None = None,
    vertex_location: str | None = None,
    vertex_credentials: str | None = None,
) -> list[list[float]]:
    if not texts:
        return []

    clean_model = model.removeprefix("models/")
    use_vertex = bool(vertex_project and vertex_credentials)
    if use_vertex:
        url = build_vertex_model_url(
            project_id=vertex_project,
            location=vertex_location or "us-central1",
            model=clean_model,
            action="predict",
        )
        from vertex_auth import get_vertex_access_token
        headers = {"Authorization": f"Bearer {get_vertex_access_token(vertex_credentials)}"}
    else:
        url = build_gemini_model_url(api_key=api_key, model=clean_model, action="embedContent")
        headers = None

    embeddings: list[list[float]] = []
    for text in texts:
        if use_vertex:
            payload = {
                "instances": [{"content": text}],
                "parameters": {"outputDimensionality": dimensions},
            }
        else:
            payload = {
                "model": f"models/{clean_model}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": dimensions,
            }
        attempt = 0
        while True:
            attempt += 1
            try:
                response = http_json_request(
                    url, method="POST", headers=headers, json_body=payload, timeout=120
                )
                if use_vertex:
                    predictions = response.get("predictions") or []
                    embedding = (
                        predictions[0].get("embeddings", {}).get("values")
                        if predictions
                        else None
                    ) or []
                else:
                    embedding = response.get("embedding", {}).get("values") or []
                if not embedding:
                    raise RuntimeError(f"No embedding returned for model {clean_model}")
                embeddings.append(embedding)
                break
            except Exception as exc:
                if attempt > 3 or not is_retryable_error(str(exc)):
                    raise
                time.sleep(min(20, attempt * 2))

    return embeddings


def embed_single(
    *,
    api_key: str,
    text: str,
    model: str = "gemini-embedding-001",
    dimensions: int = 384,
    vertex_project: str | None = None,
    vertex_location: str | None = None,
    vertex_credentials: str | None = None,
) -> list[float]:
    results = embed_texts(
        api_key=api_key,
        texts=[text],
        model=model,
        dimensions=dimensions,
        vertex_project=vertex_project,
        vertex_location=vertex_location,
        vertex_credentials=vertex_credentials,
    )
    return results[0]
