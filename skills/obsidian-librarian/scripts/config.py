#!/usr/bin/env python3
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CATEGORIES = [
    "AI_Research",
    "Industry_News",
    "Technical_Deep_Dives",
    "Tools_and_Frameworks",
    "Opinion_and_Commentary",
    "Uncategorized",
]


@dataclass
class LibrarianSettings:
    obsidian_vault_path: str
    inbox_folder: str = "_Inbox"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    debounce_seconds: float = 3.0
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))

    @classmethod
    def from_env(cls) -> "LibrarianSettings":
        return cls(
            obsidian_vault_path=os.environ.get("OBSIDIAN_VAULT_PATH", "~/Desktop/Vault"),
            inbox_folder=os.environ.get("OBSIDIAN_INBOX_FOLDER", "_Inbox"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            gemini_model=os.environ.get("OBSIDIAN_GEMINI_MODEL", os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")),
            debounce_seconds=float(os.environ.get("OBSIDIAN_DEBOUNCE_SECONDS", "3.0")),
            categories=list(DEFAULT_CATEGORIES),
        )

    @property
    def vault_path(self) -> Path:
        return Path(self.obsidian_vault_path).expanduser().resolve()

    @property
    def inbox_path(self) -> Path:
        return self.vault_path / self.inbox_folder
