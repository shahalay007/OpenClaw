#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import flatten_speaker_turns, gemini_generate_json, load_json, parse_speaker_turns, write_json


PROMPT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject_anchor": {"type": "string"},
        "base_style": {"type": "string"},
        "negative_prompt": {"type": "string"},
        "scene_prompts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "video_prompt": {"type": "string"},
                },
                "required": ["scene_id", "duration_seconds", "video_prompt"],
            },
        },
    },
    "required": ["subject_anchor", "base_style", "negative_prompt", "scene_prompts"],
}


SYSTEM_INSTRUCTION = """You are a prompt architect for short-form video generation.
Return JSON only.
Translate loose scene descriptions into precise video prompts that preserve identity and continuity.
Every prompt must be safe for Veo-style generation and must explicitly avoid on-screen text, watermarks, logos, and extra unintended subjects."""


def normalize_payload(payload: dict, source_scene_count: int) -> dict:
    prompts = payload.get("scene_prompts") or []
    if not prompts:
        raise RuntimeError("Prompt architect returned no scene prompts")
    if len(prompts) > source_scene_count:
        payload["scene_prompts"] = prompts[:source_scene_count]
    return payload


def build_speech_guidance(scenes: list[dict]) -> str:
    guidance_lines: list[str] = []
    for scene in scenes:
        spoken_text = (scene.get("spoken_text") or "").strip()
        if not spoken_text:
            continue

        turns = parse_speaker_turns(spoken_text)
        if turns:
            turn_summary = " | ".join(f"{turn['speaker']}: {turn['text']}" for turn in turns)
            guidance_lines.append(
                "- This scene contains on-camera dialogue. Make the active speaker visibly perform each line."
            )
            guidance_lines.append(
                "- Only the currently speaking subject should move its mouth, beak, or face in sync with the line cadence while the other subject listens and reacts."
            )
            guidance_lines.append(
                "- Keep the speakers framed clearly enough that speech performance is visible."
            )
            guidance_lines.append(f"- Dialogue turn order to honor: {turn_summary}")
        else:
            guidance_lines.append(
                "- This scene contains spoken performance. The visible subject should clearly appear to speak on camera with mouth, beak, or facial timing matching the line cadence."
            )
            guidance_lines.append(f"- Spoken line to honor: {spoken_text}")

    return "\n".join(guidance_lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn a script plan into continuity-safe video prompts")
    parser.add_argument("--script", required=True, help="Path to the scriptwriter JSON output")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini text model")
    parser.add_argument("--temperature", type=float, default=0.4, help="Sampling temperature")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    script_path = Path(args.script)
    script_payload = load_json(script_path)
    scenes = script_payload.get("scenes") or []
    visual_identity = script_payload.get("visual_identity") or {}
    speech_guidance = build_speech_guidance(scenes)
    clean_voiceover = flatten_speaker_turns(
        parse_speaker_turns(script_payload.get("voiceover", ""))
    ) or script_payload.get("voiceover", "")

    prompt = f"""Convert this reel plan into Veo-ready prompts.

Script plan JSON:
{script_path.read_text(encoding="utf-8")}

Requirements:
- output one prompt per scene
- keep subject identity and environment stable across all prompts
- each prompt should be standalone and concrete
- assume a 9:16 cinematic vertical reel
- include composition, motion, lighting, lens feeling, and scene texture
- avoid text overlays, subtitles, lower thirds, captions, logos, UI, or watermarks
- preserve these anchors:
  - subject anchor: {visual_identity.get("subject_anchor", "")}
  - style: {visual_identity.get("style", "")}
  - camera language: {visual_identity.get("camera_language", "")}
  - lighting: {visual_identity.get("lighting", "")}
- if the scene contains talking, explicitly direct visible lip-sync or beak-sync behavior
- preserve the intended spoken performance without showing written words on screen
- overall spoken content reference: {clean_voiceover}
{speech_guidance}
"""

    payload = gemini_generate_json(
        model=args.model,
        prompt=prompt,
        schema=PROMPT_SCHEMA,
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=args.temperature,
    )

    normalized = normalize_payload(payload, len(scenes))
    output_path = Path(args.output)
    write_json(output_path, normalized)
    print(output_path.resolve())


if __name__ == "__main__":
    main()
