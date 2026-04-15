#!/usr/bin/env python3
from __future__ import annotations

import re
import time

from common import gemini_generate_text, slugify
from config import LibrarianSettings
from models import RawInboxItem, SynthesizedContent
from prompts import SYNTHESIZER_PROMPT


class ContentSynthesizer:
    def __init__(self, settings: LibrarianSettings) -> None:
        self.settings = settings

    def synthesize(self, raw: RawInboxItem) -> SynthesizedContent:
        prompt = SYNTHESIZER_PROMPT.format(raw_content=raw.raw_content)
        delays = [2, 4, 8]
        last_error: Exception | None = None

        for attempt in range(len(delays) + 1):
            try:
                markdown = gemini_generate_text(
                    api_key=self.settings.gemini_api_key,
                    model=self.settings.gemini_model,
                    prompt=prompt,
                    temperature=0.3,
                ).strip()
                title = self._extract_title(markdown) or self._fallback_title(raw)
                markdown = self._ensure_h1(markdown, title)
                summary = self._extract_summary(markdown)
                return SynthesizedContent(title=title, markdown_body=markdown, summary=summary)
            except Exception as exc:
                last_error = exc
                if attempt >= len(delays):
                    break
                time.sleep(delays[attempt])

        fallback = self._fallback_markdown(raw, last_error)
        title = self._extract_title(fallback) or self._fallback_title(raw)
        summary = self._extract_summary(fallback)
        return SynthesizedContent(title=title, markdown_body=fallback, summary=summary)

    def _fallback_title(self, raw: RawInboxItem) -> str:
        stem = raw.file_path.stem.replace("-", " ").strip()
        return stem.title() or slugify(raw.file_path.stem, fallback="captured-note").replace("-", " ").title()

    def _fallback_markdown(self, raw: RawInboxItem, error: Exception | None) -> str:
        title = self._fallback_title(raw)
        reason = str(error) if error else "unknown error"
        body = raw.raw_content.strip() or "No content was captured."
        return (
            f"# {title}\n\n"
            "## Summary\n\n"
            f"Source imported with minimal cleanup because Gemini synthesis failed ({reason}).\n\n"
            "## Captured Content\n\n"
            f"{body}\n"
        )

    @staticmethod
    def _extract_title(markdown: str) -> str:
        for line in markdown.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return ""

    @staticmethod
    def _ensure_h1(markdown: str, title: str) -> str:
        if re.search(r"^#\s+", markdown, flags=re.MULTILINE):
            return markdown
        return f"# {title}\n\n{markdown.strip()}"

    @staticmethod
    def _extract_summary(markdown: str) -> str:
        lines = [line.rstrip() for line in markdown.splitlines()]
        summary_lines: list[str] = []
        collecting = False

        for line in lines:
            stripped = line.strip()
            if stripped.lower() in {"## summary", "summary"}:
                collecting = True
                continue
            if collecting:
                if stripped.startswith("#"):
                    break
                if stripped:
                    summary_lines.append(stripped)
                elif summary_lines:
                    break

        if summary_lines:
            return " ".join(summary_lines).strip()

        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                return stripped
        return ""
