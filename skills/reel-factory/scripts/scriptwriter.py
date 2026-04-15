#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import gemini_generate_json, write_json


SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "voiceover": {"type": "string"},
        "visual_identity": {
            "type": "object",
            "properties": {
                "subject_anchor": {"type": "string"},
                "style": {"type": "string"},
                "camera_language": {"type": "string"},
                "lighting": {"type": "string"},
                "negative_constraints": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["subject_anchor", "style", "camera_language", "lighting", "negative_constraints"],
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "spoken_text": {"type": "string"},
                    "visual_description": {"type": "string"},
                },
                "required": ["scene_id", "duration_seconds", "spoken_text", "visual_description"],
            },
        },
    },
    "required": ["title", "hook", "voiceover", "visual_identity", "scenes"],
}


SYSTEM_INSTRUCTION = """You write short-form narrated reel scripts.
Return JSON only and keep it production-ready.
Narration should sound natural when spoken aloud.
The reel should be visually coherent, concise, and suitable for short-form vertical video.
Do not mention captions, on-screen UI, or text overlays inside the visual descriptions."""


def normalize_plan(plan: dict, scene_count: int, duration_seconds: int) -> dict:
    scenes = plan.get("scenes") or []
    if not scenes:
        raise RuntimeError("Gemini returned no scenes")

    if len(scenes) > scene_count:
        scenes = scenes[:scene_count]

    if len(scenes) < scene_count:
        while len(scenes) < scene_count:
            scenes.append(
                {
                    "scene_id": f"scene-{len(scenes) + 1:02d}",
                    "duration_seconds": max(duration_seconds / scene_count, 1),
                    "spoken_text": plan.get("voiceover", ""),
                    "visual_description": plan.get("visual_identity", {}).get("subject_anchor", ""),
                }
            )

    total_assigned = sum(max(float(scene.get("duration_seconds", 0)), 0.5) for scene in scenes)
    scale = duration_seconds / total_assigned if total_assigned else 1.0

    normalized_scenes = []
    spoken_lines = []
    for index, scene in enumerate(scenes, start=1):
        spoken_text = (scene.get("spoken_text") or "").strip()
        visual_description = (scene.get("visual_description") or "").strip()
        duration = round(max(float(scene.get("duration_seconds", 1)) * scale, 1), 2)
        normalized_scenes.append(
            {
                "scene_id": scene.get("scene_id") or f"scene-{index:02d}",
                "duration_seconds": duration,
                "spoken_text": spoken_text,
                "visual_description": visual_description,
            }
        )
        if spoken_text:
            spoken_lines.append(spoken_text)

    plan["scenes"] = normalized_scenes
    plan["voiceover"] = (plan.get("voiceover") or " ".join(spoken_lines).strip()).strip()
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand an idea into a reel script plan")
    parser.add_argument("--idea", required=True, help="Raw idea or topic")
    parser.add_argument("--scene-count", type=int, default=1, help="Number of scenes to plan")
    parser.add_argument("--duration-seconds", type=int, default=8, help="Approximate total duration")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini text model")
    parser.add_argument("--temperature", type=float, default=0.7, help="Sampling temperature")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    prompt = f"""Create a short-form narrated reel plan.

Idea:
{args.idea}

Constraints:
- total duration about {args.duration_seconds} seconds
- exactly {args.scene_count} scene(s)
- keep the narration concise enough for spoken delivery
- produce a scenic, cinematic result
- keep visual identity stable across all scenes
- no logos, no watermarks, no on-screen text
"""

    plan = gemini_generate_json(
        model=args.model,
        prompt=prompt,
        schema=SCRIPT_SCHEMA,
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=args.temperature,
    )

    normalized = normalize_plan(plan, args.scene_count, args.duration_seconds)
    output_path = Path(args.output)
    write_json(output_path, normalized)
    print(output_path.resolve())


if __name__ == "__main__":
    main()
