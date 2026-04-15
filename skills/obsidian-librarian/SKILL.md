---
name: obsidian-librarian
description: Save pasted text, research notes, and blog/article URLs into an Obsidian vault through a two-pass Gemini pipeline. Use when the user asks to save something to Obsidian, file a note in the vault, convert a blog/article into a structured note, organize knowledge into interlinked markdown, or says short phrases like "save this", "save it", or "save this url" when the same message contains a URL or the content to store.
---

# Obsidian Librarian

Use this skill when the user wants OpenClaw to store text or a URL in the Obsidian vault as a cleaned, categorized markdown note.

Trigger shortcuts:
- Treat `save this`, `save it`, `save this url`, and `save this link` as Obsidian-librarian requests when the same message contains a URL, pasted text, or quoted content to preserve.
- Treat short follow-ups like `save it` as Obsidian-librarian requests when the immediately preceding user message provided the text or URL to store.
- If the message only says `save this` or `save it` with no actual content or URL available in context, do not guess; ask what should be saved.
- If the intent is ambiguous between saving to the local filesystem versus saving to the knowledge vault, prefer the Obsidian vault when the content looks like a note, article, research snippet, or social post.

The vault is mounted in the container at `/data/.openclaw/obsidian-vault`. Raw inputs are staged in `/data/.openclaw/obsidian-vault/_Inbox`, then processed into category folders.

URL handling policy:
- Always use Apify to read the URL first.
- For `x.com` / `twitter.com` post URLs, use the dedicated Apify tweet actor.
- If direct URL reading fails, run a web-search fallback and stage the search-result snapshot instead.
- If both stages fail, surface the full error back to OpenClaw instead of silently swallowing it.

## Supported Inputs

- Pasted text
- A local text/markdown file
- A blog/article URL
- An existing file already sitting in `_Inbox`

## Workflow

1. Stage the raw source in `_Inbox/`.
2. Run Gemini pass 1 to clean and structure it into markdown.
3. Run Gemini pass 2 to choose category, tags, source attribution, and candidate wikilinks.
4. Scan existing vault notes for titles and aliases to resolve `[[wikilinks]]`.
5. Write the final note with YAML frontmatter into the chosen category folder.
6. Delete the `_Inbox` file only after the final note is written successfully.

## Main Command

```bash
python3 {baseDir}/scripts/run_pipeline.py --text-file /data/.openclaw/workspace/input.txt
```

## URL Example

```bash
python3 {baseDir}/scripts/run_pipeline.py --url "https://example.com/article"
```

## Existing Inbox File

```bash
python3 {baseDir}/scripts/run_pipeline.py --inbox-file /data/.openclaw/obsidian-vault/_Inbox/some-file.md
```

## Notes

- For long pasted text, prefer writing it to a temp file under `/data/.openclaw/workspace/` and using `--text-file`.
- Use `--title "Custom Title"` if the user wants an explicit note title override.
- Use `--keep-inbox` only when debugging. Normal behavior is to clean up the staged source after success.
- The pipeline does forward-linking only in v1. Existing notes are not modified.
- URL ingestion requires `APIFY_API_KEY` in the container environment.
