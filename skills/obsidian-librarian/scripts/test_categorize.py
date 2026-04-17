#!/usr/bin/env python3
"""Quick smoke test for the categorization fix.

Usage:
    source .env && python3 skills/obsidian-librarian/scripts/test_categorize.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the scripts directory is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import LibrarianSettings, DEFAULT_CATEGORIES
from models import SynthesizedContent
from architect import MetadataArchitect
from vault_index import VaultIndex

TEST_CASES = [
    {
        "title": "GPT-5 Architecture Deep Dive",
        "summary": "Analysis of GPT-5 transformer architecture and training methodology.",
        "body": (
            "# GPT-5 Architecture Deep Dive\n\n"
            "This paper presents the GPT-5 architecture, a 1.8 trillion parameter "
            "transformer model trained on 15 trillion tokens. Key innovations include "
            "mixture-of-experts routing, improved positional embeddings, and a novel "
            "attention mechanism that reduces compute by 40%. Benchmark results show "
            "state-of-the-art performance on MMLU, HumanEval, and MATH."
        ),
        "expected": "AI_Models_and_Research",
    },
    {
        "title": "Polymarket Election Contracts Analysis",
        "summary": "Breakdown of Polymarket prediction market contracts for 2026 elections.",
        "body": (
            "# Polymarket Election Contracts Analysis\n\n"
            "Polymarket's 2026 midterm election contracts are seeing record volume. "
            "The Senate control contract has $45M in open interest. Key observations: "
            "the market is pricing a 62% chance of Republican Senate control, "
            "while individual state contracts show tight races in Arizona and Nevada. "
            "Kalshi's competing contracts show similar pricing but lower liquidity."
        ),
        "expected": "Prediction_Markets",
    },
    {
        "title": "Building RAG Pipelines with LangChain",
        "summary": "Tutorial on building production RAG systems using LangChain and ChromaDB.",
        "body": (
            "# Building RAG Pipelines with LangChain\n\n"
            "This tutorial walks through building a production-ready RAG pipeline. "
            "We use LangChain for orchestration, ChromaDB for vector storage, and "
            "OpenAI embeddings for document chunking. Topics covered: document loading, "
            "text splitting strategies, embedding generation, retrieval with reranking, "
            "and prompt engineering for grounded responses. Includes deployment with FastAPI."
        ),
        "expected": "AI_Engineering",
    },
]

VALID_CATEGORIES = set(DEFAULT_CATEGORIES.keys())


def main() -> int:
    settings = LibrarianSettings.from_env()
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set. Source your .env first.", file=sys.stderr)
        return 1

    # Use a temporary vault path with an empty index (no wikilinks needed for this test)
    vault_index = VaultIndex(Path("/tmp/test-vault-empty"))

    passed = 0
    failed = 0

    for i, tc in enumerate(TEST_CASES, 1):
        content = SynthesizedContent(
            title=tc["title"],
            markdown_body=tc["body"],
            summary=tc["summary"],
        )
        architect = MetadataArchitect(settings, vault_index)
        result = architect.architect(content)

        is_valid = result.category in VALID_CATEGORIES
        is_expected = result.category == tc["expected"]
        is_not_uncategorized = result.category != "Uncategorized"

        status = "PASS" if (is_valid and is_expected and is_not_uncategorized) else "FAIL"
        if status == "PASS":
            passed += 1
        else:
            failed += 1

        print(f"[{status}] Test {i}: \"{tc['title']}\"")
        print(f"       Expected: {tc['expected']}")
        print(f"       Got:      {result.category}")
        if not is_valid:
            print(f"       ERROR: '{result.category}' is not a valid category")
        print()

    print(f"Results: {passed} passed, {failed} failed out of {len(TEST_CASES)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
