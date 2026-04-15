#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from common import load_json, run_checked, slugify, timestamp_slug, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the narrated reel pipeline end to end")
    parser.add_argument("--idea", required=True, help="Idea or concept for the reel")
    parser.add_argument("--output-root", default="data/workspace/reel-runs", help="Base output directory")
    parser.add_argument("--scene-count", type=int, default=1, help="Number of scenes/segments")
    parser.add_argument("--duration-seconds", type=int, default=8, help="Target total duration")
    parser.add_argument("--script-model", default="gemini-2.5-flash", help="Gemini model for scriptwriting")
    parser.add_argument("--prompt-model", default="gemini-2.5-flash", help="Gemini model for prompt architecture")
    parser.add_argument("--voice-id", help="ElevenLabs voice ID")
    parser.add_argument("--voice-name", default="Rachel", help="ElevenLabs voice name if no voice ID is provided")
    parser.add_argument("--tts-model", default="eleven_multilingual_v2", help="ElevenLabs TTS model")
    parser.add_argument("--veo-model", default="veo-3.1-generate-preview", help="Veo model")
    parser.add_argument("--aspect-ratio", default="9:16", choices=["16:9", "9:16", "1:1"], help="Video aspect ratio")
    parser.add_argument("--existing-video", help="Use an existing visual MP4 instead of generating a new one")
    parser.add_argument("--captions", choices=["on", "off"], default="off", help="Whether to burn captions into the final video")
    parser.add_argument("--caption-y", choices=["center", "lower-third"], default="center", help="Caption vertical position")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parents[1]
    repo_root = base_dir.parents[1]
    veo_script = repo_root / "skills" / "veo3-video-gen" / "scripts" / "generate_video.py"

    run_dir = repo_root / args.output_root / timestamp_slug(args.idea[:48] or "reel")
    run_dir.mkdir(parents=True, exist_ok=True)

    idea_path = run_dir / "idea.txt"
    idea_path.write_text(args.idea + "\n", encoding="utf-8")

    script_path = run_dir / "script.json"
    prompt_path = run_dir / "prompts.json"
    audio_path = run_dir / "voiceover.mp3"
    alignment_path = run_dir / "voiceover.alignment.json"
    visuals_path = run_dir / "visuals.mp4"
    final_path = run_dir / f"{slugify(args.idea[:48], fallback='reel')}.mp4"

    run_checked(
        [
            "python3",
            str(base_dir / "scripts" / "scriptwriter.py"),
            "--idea",
            args.idea,
            "--scene-count",
            str(args.scene_count),
            "--duration-seconds",
            str(args.duration_seconds),
            "--model",
            args.script_model,
            "--output",
            str(script_path),
        ],
        cwd=repo_root,
    )

    run_checked(
        [
            "python3",
            str(base_dir / "scripts" / "prompt_architect.py"),
            "--script",
            str(script_path),
            "--model",
            args.prompt_model,
            "--output",
            str(prompt_path),
        ],
        cwd=repo_root,
    )

    script_payload = load_json(script_path)
    prompt_payload = load_json(prompt_path)

    voiceover_text = (script_payload.get("voiceover") or "").strip()
    if not voiceover_text:
        voiceover_text = " ".join(scene.get("spoken_text", "") for scene in script_payload.get("scenes") or []).strip()
    if not voiceover_text:
        raise RuntimeError("Scriptwriter did not produce any voiceover text")

    tts_command = [
        "python3",
        str(base_dir / "scripts" / "tts_elevenlabs.py"),
        "--text",
        voiceover_text,
        "--model-id",
        args.tts_model,
        "--output-audio",
        str(audio_path),
        "--output-alignment",
        str(alignment_path),
    ]
    if args.voice_id:
        tts_command.extend(["--voice-id", args.voice_id])
    else:
        tts_command.extend(["--voice-name", args.voice_name])
    run_checked(tts_command, cwd=repo_root)

    if args.existing_video:
        visuals_path = Path(args.existing_video).resolve()
    else:
        scene_prompts = prompt_payload.get("scene_prompts") or []
        if not scene_prompts:
            raise RuntimeError("Prompt architect returned no scene prompts")

        veo_command = [
            "python3",
            str(veo_script),
            "--filename",
            str(visuals_path),
            "--model",
            args.veo_model,
            "--aspect-ratio",
            args.aspect_ratio,
            "--segments",
            str(len(scene_prompts)),
            "--base-style",
            prompt_payload.get("base_style", ""),
            "--retries",
            "2",
        ]
        if len(scene_prompts) > 1:
            veo_command.append("--use-last-frame")
        for scene_prompt in scene_prompts:
            veo_command.extend(["--segment-prompt", scene_prompt["video_prompt"]])
        run_checked(veo_command, cwd=repo_root)

    compose_command = [
        "python3",
        str(base_dir / "scripts" / "compose_vertical_reel.py"),
        "--video",
        str(visuals_path),
        "--audio",
        str(audio_path),
        "--captions",
        args.captions,
        "--caption-y",
        args.caption_y,
        "--output",
        str(final_path),
    ]
    if args.captions == "on":
        compose_command.extend(["--alignment", str(alignment_path)])
    run_checked(compose_command, cwd=repo_root)

    manifest = {
        "idea": args.idea,
        "run_dir": str(run_dir.resolve()),
        "script": str(script_path.resolve()),
        "prompts": str(prompt_path.resolve()),
        "audio": str(audio_path.resolve()),
        "alignment": str(alignment_path.resolve()),
        "visuals": str(visuals_path.resolve()),
        "final_video": str(final_path.resolve()),
        "captions": args.captions,
        "models": {
            "script_model": args.script_model,
            "prompt_model": args.prompt_model,
            "tts_model": args.tts_model,
            "veo_model": args.veo_model,
        },
    }
    manifest_path = run_dir / "manifest.json"
    write_json(manifest_path, manifest)

    print(manifest_path.resolve())
    print(f"MEDIA: {final_path.resolve()}")


if __name__ == "__main__":
    main()
