#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from common import alignment_to_words, ffprobe_duration, load_json, seconds_to_srt


def escape_filter_path(path: Path) -> str:
    escaped = str(path.resolve())
    replacements = {
        "\\": r"\\\\",
        ":": r"\:",
        "'": r"\'",
        ",": r"\,",
        "[": r"\[",
        "]": r"\]",
    }
    for source, target in replacements.items():
        escaped = escaped.replace(source, target)
    return escaped


def ffmpeg_supports_filter(filter_name: str) -> bool:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=True,
    )
    return f" {filter_name} " in result.stdout


def build_caption_cues(
    words: list[dict[str, Any]],
    *,
    max_words_per_cue: int = 5,
    max_chars_per_cue: int = 32,
    max_duration: float = 1.8,
) -> list[dict[str, Any]]:
    if not words:
        return []

    def make_cue(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "text": " ".join(item["text"] for item in items).strip(),
            "start": max(float(items[0]["start"]), 0.0),
            "end": max(float(items[-1]["end"]) + 0.08, float(items[0]["start"]) + 0.2),
        }

    cues: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    for word in words:
        trial = current + [word]
        trial_text = " ".join(item["text"] for item in trial).strip()
        trial_duration = float(trial[-1]["end"]) - float(trial[0]["start"])

        should_flush = bool(
            current
            and (
                len(trial) > max_words_per_cue
                or len(trial_text) > max_chars_per_cue
                or trial_duration > max_duration
            )
        )
        if should_flush:
            cues.append(make_cue(current))
            current = [word]
        else:
            current = trial

        if current and current[-1]["text"].endswith((".", "!", "?")) and len(current) >= 2:
            cues.append(make_cue(current))
            current = []

    if current:
        cues.append(make_cue(current))

    return cues


def write_srt(path: Path, cues: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{seconds_to_srt(cue['start'])} --> {seconds_to_srt(cue['end'])}")
        lines.append(cue["text"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def seconds_to_ass(seconds: float) -> str:
    total_centiseconds = int(round(max(seconds, 0.0) * 100))
    hours, remainder = divmod(total_centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def write_ass(
    path: Path,
    cues: list[dict[str, Any]],
    *,
    width: int,
    height: int,
    font_name: str,
    font_size: int,
    caption_y: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    margin_v = 120 if caption_y == "center" else 80
    alignment = 5 if caption_y == "center" else 2

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Caption,"
        f"{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,"
        "1,0,0,0,100,100,0,0,3,2,0,"
        f"{alignment},60,60,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for cue in cues:
        lines.append(
            "Dialogue: 0,"
            f"{seconds_to_ass(cue['start'])},"
            f"{seconds_to_ass(cue['end'])},"
            f"Caption,,0,0,0,,{escape_ass_text(cue['text'])}"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_filter_graph(
    subtitle_path: Path | None,
    *,
    width: int,
    height: int,
    subtitle_filter: str | None,
) -> str:
    filters = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
        "setsar=1",
        "format=yuv420p",
    ]
    if subtitle_path and subtitle_filter:
        filters.append(f"{subtitle_filter}='{escape_filter_path(subtitle_path)}'")

    return "[0:v]" + ",".join(filters) + "[vout]"


def main() -> None:
    parser = argparse.ArgumentParser(description="Composite a vertical reel with audio and optional captions")
    parser.add_argument("--video", required=True, help="Input video path")
    parser.add_argument("--audio", required=True, help="Input audio path")
    parser.add_argument("--alignment", help="Alignment JSON path")
    parser.add_argument("--output", required=True, help="Output MP4 path")
    parser.add_argument("--width", type=int, default=1080, help="Output width")
    parser.add_argument("--height", type=int, default=1920, help="Output height")
    parser.add_argument("--font-size", type=int, default=58, help="Caption font size")
    parser.add_argument("--captions", choices=["on", "off"], default="off", help="Whether to burn captions into the video")
    parser.add_argument("--caption-y", choices=["center", "lower-third"], default="center", help="Caption vertical position")
    parser.add_argument("--font-name", default="Liberation Sans", help="ASS subtitle font name")
    args = parser.parse_args()

    video_path = Path(args.video)
    audio_path = Path(args.audio)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    subtitle_filter: str | None = None
    subtitle_path: Path | None = None
    if args.captions == "on":
        if not args.alignment:
            raise RuntimeError("--alignment is required when --captions=on")
        alignment_path = Path(args.alignment)
        alignment_payload = load_json(alignment_path)
        words = alignment_to_words(alignment_payload)
        cues = build_caption_cues(words)

        srt_path = output_path.with_suffix(".srt")
        write_srt(srt_path, cues)

        ass_path = output_path.with_suffix(".ass")
        write_ass(
            ass_path,
            cues,
            width=args.width,
            height=args.height,
            font_name=args.font_name,
            font_size=args.font_size,
            caption_y=args.caption_y,
        )

        if ffmpeg_supports_filter("subtitles"):
            subtitle_filter = "subtitles"
            subtitle_path = ass_path
        elif ffmpeg_supports_filter("ass"):
            subtitle_filter = "ass"
            subtitle_path = ass_path
        else:
            raise RuntimeError(
                "FFmpeg build is missing subtitle rendering filters. "
                "Use the repo's Dockerized OpenClaw image, which includes libass."
            )

    audio_duration = ffprobe_duration(audio_path)
    filter_graph = build_filter_graph(
        subtitle_path,
        width=args.width,
        height=args.height,
        subtitle_filter=subtitle_filter,
    )

    filter_script_path = output_path.with_suffix(".ffmpeg-filter.txt")
    filter_script_path.write_text(filter_graph, encoding="utf-8")

    command = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-filter_complex_script",
        str(filter_script_path),
        "-map",
        "[vout]",
        "-map",
        "1:a:0",
        "-t",
        f"{audio_duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(command, check=True)
    print(output_path.resolve())


if __name__ == "__main__":
    main()
