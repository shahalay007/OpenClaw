#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import fetch_url_source, slugify, timestamp_slug, write_text
from config import LibrarianSettings
from pipeline import LibrarianPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Save text or a URL into an Obsidian vault through the librarian pipeline")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--text", help="Raw text to ingest")
    source_group.add_argument("--text-file", help="Path to a text/markdown file to ingest")
    source_group.add_argument("--url", help="URL to fetch and ingest")
    source_group.add_argument("--inbox-file", help="Existing file already staged inside the vault inbox")

    parser.add_argument("--vault-path", help="Override OBSIDIAN_VAULT_PATH for this run")
    parser.add_argument("--title", help="Optional title override for the final note")
    parser.add_argument("--keep-inbox", action="store_true", help="Keep the staged inbox file after successful processing")
    parser.add_argument("--print-json", action="store_true", help="Print a JSON manifest instead of plain text paths")
    return parser.parse_args()


def stage_manual_text(settings: LibrarianSettings, text: str, label: str) -> Path:
    settings.inbox_path.mkdir(parents=True, exist_ok=True)
    inbox_path = settings.inbox_path / f"{timestamp_slug(label)}.md"
    write_text(inbox_path, text.strip() + "\n")
    return inbox_path


def main() -> None:
    args = parse_args()
    settings = LibrarianSettings.from_env()
    if args.vault_path:
        settings.obsidian_vault_path = args.vault_path

    settings.vault_path.mkdir(parents=True, exist_ok=True)
    settings.inbox_path.mkdir(parents=True, exist_ok=True)

    if not settings.gemini_api_key:
        raise RuntimeError("Missing GEMINI_API_KEY in environment")

    if args.inbox_file:
        inbox_path = Path(args.inbox_file).expanduser().resolve()
    elif args.url:
        try:
            fetched_title, fetched_body = fetch_url_source(args.url)
        except Exception as exc:
            raise RuntimeError(f"OpenClaw librarian URL ingest failed for {args.url}: {exc}") from exc
        raw_text = (
            f"Source URL: {args.url}\n"
            f"Source Title: {fetched_title}\n\n"
            f"{fetched_body.strip()}\n"
        )
        inbox_path = stage_manual_text(settings, raw_text, fetched_title or "web-source")
    elif args.text_file:
        source_path = Path(args.text_file).expanduser().resolve()
        raw_text = source_path.read_text(encoding="utf-8")
        inbox_path = stage_manual_text(settings, raw_text, source_path.stem)
    else:
        label = args.title or "manual-paste"
        inbox_path = stage_manual_text(settings, args.text or "", label)

    pipeline = LibrarianPipeline(settings)
    processed = pipeline.process_file(inbox_path, keep_inbox=args.keep_inbox, title_override=args.title)

    manifest = {
        "vault_path": str(settings.vault_path),
        "inbox_file": str(processed.raw.file_path),
        "final_path": str(processed.final_path),
        "title": processed.synthesized.title,
        "category": processed.architected.category,
        "needs_review": bool(processed.architected.frontmatter.get("needs_review")),
        "source": processed.architected.frontmatter.get("source"),
        "tags": processed.architected.frontmatter.get("tags") or [],
    }

    if args.print_json:
        print(json.dumps(manifest, indent=2))
    else:
        print(processed.final_path.resolve())
        print(f"MEDIA: {processed.final_path.resolve()}")


if __name__ == "__main__":
    main()
