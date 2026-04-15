#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from common import (
    ELEVENLABS_API_BASE,
    alignment_to_words,
    env_or_raise,
    ffprobe_duration,
    flatten_speaker_turns,
    http_json_request,
    parse_speaker_turns,
    read_text_argument,
    write_json,
)


def list_voices(api_key: str) -> list[dict[str, Any]]:
    payload = http_json_request(
        f"{ELEVENLABS_API_BASE}/voices",
        headers={"xi-api-key": api_key},
        timeout=120,
    )
    return payload.get("voices") or []


def resolve_voice_from_list(
    voices: list[dict[str, Any]],
    voice_id: str | None,
    voice_name: str | None,
    *,
    exclude_voice_ids: set[str] | None = None,
) -> tuple[str, str]:
    excluded = exclude_voice_ids or set()
    if voice_id:
        for voice in voices:
            if voice.get("voice_id") == voice_id and voice_id not in excluded:
                return voice_id, voice.get("name", voice_id)
        if voice_id not in excluded:
            return voice_id, voice_id

    preferred_name = (voice_name or "Rachel").strip().lower()
    for voice in voices:
        if str(voice.get("name", "")).lower() == preferred_name and voice.get("voice_id") not in excluded:
            return voice["voice_id"], voice.get("name", "unknown")

    for voice in voices:
        if voice.get("category") == "premade" and voice.get("voice_id") not in excluded:
            return voice["voice_id"], voice.get("name", "unknown")

    if voices:
        for voice in voices:
            if voice.get("voice_id") not in excluded:
                return voice["voice_id"], voice.get("name", "unknown")

    raise RuntimeError("No ElevenLabs voices available for this account")


def resolve_voice(api_key: str, voice_id: str | None, voice_name: str | None) -> tuple[str, str]:
    return resolve_voice_from_list(list_voices(api_key), voice_id, voice_name)


def synthesize_tts(
    *,
    api_key: str,
    voice_id: str,
    text: str,
    model_id: str,
    output_format: str,
    stability: float,
    similarity_boost: float,
    style: float,
    use_speaker_boost: bool,
) -> dict[str, Any]:
    return http_json_request(
        f"{ELEVENLABS_API_BASE}/text-to-speech/{voice_id}/with-timestamps",
        method="POST",
        headers={"xi-api-key": api_key},
        json_body={
            "text": text,
            "model_id": model_id,
            "output_format": output_format,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity_boost,
                "style": style,
                "use_speaker_boost": use_speaker_boost,
            },
        },
        timeout=180,
    )


def concat_mp3_segments(segment_paths: list[Path], output_path: Path) -> None:
    concat_file = output_path.with_suffix(".concat.txt")
    concat_file.write_text(
        "".join(f"file '{segment.resolve().as_posix()}'\n" for segment in segment_paths),
        encoding="utf-8",
    )
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(output_path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    concat_file.unlink(missing_ok=True)


def build_speaker_voice_map(
    *,
    api_key: str,
    turns: list[dict[str, str]],
    primary_voice_name: str,
    secondary_voice_name: str,
    explicit_voice_id: str | None,
) -> dict[str, tuple[str, str]]:
    voices = list_voices(api_key)
    speakers: list[str] = []
    for turn in turns:
        speaker = turn["speaker"]
        if speaker not in speakers:
            speakers.append(speaker)

    preferred_names = [primary_voice_name, secondary_voice_name]
    mapping: dict[str, tuple[str, str]] = {}
    used_voice_ids: set[str] = set()
    for index, speaker in enumerate(speakers):
        if index == 0 and explicit_voice_id:
            mapping[speaker] = resolve_voice_from_list(
                voices,
                explicit_voice_id,
                None,
                exclude_voice_ids=used_voice_ids,
            )
            used_voice_ids.add(mapping[speaker][0])
            continue
        preferred_name = preferred_names[min(index, len(preferred_names) - 1)]
        mapping[speaker] = resolve_voice_from_list(
            voices,
            None,
            preferred_name,
            exclude_voice_ids=used_voice_ids,
        )
        used_voice_ids.add(mapping[speaker][0])
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate voiceover audio and alignment via ElevenLabs")
    parser.add_argument("--text", help="Voiceover text")
    parser.add_argument("--text-file", help="Path to a text file with voiceover text")
    parser.add_argument("--voice-id", help="Explicit ElevenLabs voice ID")
    parser.add_argument("--voice-name", default="Rachel", help="Resolve a voice by name if --voice-id is omitted")
    parser.add_argument("--secondary-voice-name", default="Adam", help="Second voice name for multi-speaker dialogue")
    parser.add_argument("--model-id", default="eleven_multilingual_v2", help="ElevenLabs model ID")
    parser.add_argument("--output-format", default="mp3_44100_128", help="ElevenLabs output format")
    parser.add_argument("--stability", type=float, default=0.4, help="Voice stability")
    parser.add_argument("--similarity-boost", type=float, default=0.75, help="Voice similarity boost")
    parser.add_argument("--style", type=float, default=0.2, help="Voice style strength")
    parser.add_argument("--use-speaker-boost", action="store_true", help="Enable speaker boost")
    parser.add_argument("--output-audio", required=True, help="Output MP3 path")
    parser.add_argument("--output-alignment", required=True, help="Output alignment JSON path")
    args = parser.parse_args()

    api_key = env_or_raise("ELEVENLABS_API_KEY")
    text = read_text_argument(args.text, args.text_file, "text")
    audio_path = Path(args.output_audio)
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    turns = parse_speaker_turns(text)
    if len(turns) >= 2:
        speaker_voice_map = build_speaker_voice_map(
            api_key=api_key,
            turns=turns,
            primary_voice_name=args.voice_name,
            secondary_voice_name=args.secondary_voice_name,
            explicit_voice_id=args.voice_id,
        )
        words: list[dict[str, Any]] = []
        turn_payloads: list[dict[str, Any]] = []
        current_offset = 0.0

        with tempfile.TemporaryDirectory(prefix="reel-factory-tts-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            segment_paths: list[Path] = []
            for index, turn in enumerate(turns, start=1):
                speaker = turn["speaker"]
                turn_text = turn["text"]
                voice_id, voice_name = speaker_voice_map[speaker]
                payload = synthesize_tts(
                    api_key=api_key,
                    voice_id=voice_id,
                    text=turn_text,
                    model_id=args.model_id,
                    output_format=args.output_format,
                    stability=args.stability,
                    similarity_boost=args.similarity_boost,
                    style=args.style,
                    use_speaker_boost=args.use_speaker_boost,
                )
                audio_base64 = payload.get("audio_base64")
                if not audio_base64:
                    raise RuntimeError(f"ElevenLabs response missing audio_base64: {payload}")

                segment_path = temp_dir / f"segment-{index:02d}.mp3"
                segment_path.write_bytes(base64.b64decode(audio_base64))
                segment_paths.append(segment_path)

                segment_duration = ffprobe_duration(segment_path)
                segment_alignment_payload = {
                    "alignment": payload.get("alignment"),
                    "normalized_alignment": payload.get("normalized_alignment"),
                }
                segment_words = alignment_to_words(segment_alignment_payload)
                shifted_words = [
                    {
                        **word,
                        "speaker": speaker,
                        "voice_name": voice_name,
                        "start": float(word["start"]) + current_offset,
                        "end": float(word["end"]) + current_offset,
                    }
                    for word in segment_words
                ]
                words.extend(shifted_words)
                turn_payloads.append(
                    {
                        "speaker": speaker,
                        "voice_id": voice_id,
                        "voice_name": voice_name,
                        "text": turn_text,
                        "start": current_offset,
                        "end": current_offset + segment_duration,
                    }
                )
                current_offset += segment_duration

            concat_mp3_segments(segment_paths, audio_path)

        alignment_payload = {
            "text": flatten_speaker_turns(turns),
            "dialogue_text": text,
            "dialogue_mode": True,
            "model_id": args.model_id,
            "speaker_voice_map": {
                speaker: {"voice_id": voice_info[0], "voice_name": voice_info[1]}
                for speaker, voice_info in speaker_voice_map.items()
            },
            "turns": turn_payloads,
            "words": words,
        }
    else:
        resolved_voice_id, resolved_voice_name = resolve_voice(api_key, args.voice_id, args.voice_name)
        payload = synthesize_tts(
            api_key=api_key,
            voice_id=resolved_voice_id,
            text=text,
            model_id=args.model_id,
            output_format=args.output_format,
            stability=args.stability,
            similarity_boost=args.similarity_boost,
            style=args.style,
            use_speaker_boost=args.use_speaker_boost,
        )
        audio_base64 = payload.get("audio_base64")
        if not audio_base64:
            raise RuntimeError(f"ElevenLabs response missing audio_base64: {payload}")
        audio_path.write_bytes(base64.b64decode(audio_base64))

        alignment_payload = {
            "text": text,
            "voice_id": resolved_voice_id,
            "voice_name": resolved_voice_name,
            "model_id": args.model_id,
            "alignment": payload.get("alignment"),
            "normalized_alignment": payload.get("normalized_alignment"),
        }
        alignment_payload["words"] = alignment_to_words(alignment_payload)

    alignment_path = Path(args.output_alignment)
    write_json(alignment_path, alignment_payload)

    print(audio_path.resolve())
    print(alignment_path.resolve())


if __name__ == "__main__":
    main()
