#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"


def env_or_raise(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    raise RuntimeError(f"Missing required environment variable: {name}")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def read_text_argument(text: str | None, text_file: str | None, label: str) -> str:
    if text and text.strip():
        return text.strip()
    if text_file:
        return Path(text_file).read_text(encoding="utf-8").strip()
    raise RuntimeError(f"Provide --{label} or --{label}-file")


def slugify(value: str, *, fallback: str = "run") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or fallback


def timestamp_slug(label: str) -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(label)}"


def extract_json_text(response_payload: dict[str, Any]) -> str:
    candidates = response_payload.get("candidates") or []
    for candidate in candidates:
        parts = candidate.get("content", {}).get("parts", [])
        for part in parts:
            text = part.get("text")
            if text:
                return text
    raise RuntimeError(f"No text candidate found in Gemini response: {response_payload}")


def parse_json_text(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    return json.loads(cleaned)


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    timeout: int = 120,
) -> bytes:
    request_headers = dict(headers or {})
    data: bytes | None = None
    if json_body is not None:
        request_headers.setdefault("Content-Type", "application/json")
        data = json.dumps(json_body).encode("utf-8")

    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return curl_request(
                url,
                method=method,
                headers=request_headers,
                json_body=json_body,
                timeout=timeout,
            )
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def curl_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    json_body: Any | None,
    timeout: int,
) -> bytes:
    command = [
        "curl",
        "-fsSL",
        "--max-time",
        str(timeout),
        "-X",
        method,
        url,
    ]
    for key, value in headers.items():
        command.extend(["-H", f"{key}: {value}"])

    body_bytes: bytes | None = None
    if json_body is not None:
        body_bytes = json.dumps(json_body).encode("utf-8")
        command.extend(["--data-binary", "@-"])

    result = subprocess.run(
        command,
        input=body_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"curl failed for {url}: {stderr}")
    return result.stdout


def http_json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    timeout: int = 120,
) -> Any:
    raw = http_request(url, method=method, headers=headers, json_body=json_body, timeout=timeout)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def is_retryable_error(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "resource_exhausted",
        "unavailable",
        "deadline",
        "timeout",
        "temporarily",
        "connection reset",
    )
    return any(marker in lowered for marker in markers)


def gemini_generate_json(
    *,
    model: str,
    prompt: str,
    schema: dict[str, Any],
    system_instruction: str | None = None,
    temperature: float = 0.7,
    retries: int = 2,
) -> Any:
    api_key = env_or_raise("GEMINI_API_KEY")
    clean_model = model.removeprefix("models/")
    url = f"{GEMINI_API_BASE}/models/{urllib.parse.quote(clean_model)}:generateContent?key={urllib.parse.quote(api_key)}"

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    attempt = 0
    while True:
        attempt += 1
        try:
            response_payload = http_json_request(url, method="POST", json_body=payload, timeout=180)
            return parse_json_text(extract_json_text(response_payload))
        except Exception as exc:
            if attempt > retries + 1 or not is_retryable_error(str(exc)):
                raise
            time.sleep(min(20, attempt * 4))


def run_checked(command: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def alignment_to_words(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("words"):
        return payload["words"]

    alignment = payload.get("normalized_alignment") or payload.get("alignment") or payload
    words = alignment.get("words")
    if isinstance(words, list) and words:
        return words

    characters = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    result: list[dict[str, Any]] = []
    current_chars: list[str] = []
    current_start: float | None = None
    current_end: float | None = None

    def flush() -> None:
        nonlocal current_chars, current_start, current_end
        if not current_chars or current_start is None or current_end is None:
            current_chars = []
            current_start = None
            current_end = None
            return
        text = "".join(current_chars).strip()
        if text:
            result.append({"text": text, "start": current_start, "end": current_end})
        current_chars = []
        current_start = None
        current_end = None

    for char, start, end in zip(characters, starts, ends):
        if char.isspace():
            flush()
            continue
        if current_start is None:
            current_start = float(start)
        current_chars.append(char)
        current_end = float(end)

    flush()
    return result


def parse_speaker_turns(text: str) -> list[dict[str, str]]:
    matches = list(re.finditer(r"(?<!\w)([A-Za-z][A-Za-z0-9 _-]{0,30}):\s*", text))
    if not matches:
        return []

    turns: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        utterance = text[start:end].strip().strip('"').strip("'").strip()
        if not utterance:
            continue
        speaker = re.sub(r"\s+", " ", match.group(1).strip())
        turns.append({"speaker": speaker, "text": utterance})
    return turns


def flatten_speaker_turns(turns: list[dict[str, str]]) -> str:
    return " ".join(turn["text"].strip() for turn in turns if turn.get("text")).strip()


def seconds_to_srt(seconds: float) -> str:
    total_ms = int(round(max(seconds, 0) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
