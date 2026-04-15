---
name: reel-factory
description: Build short narrated vertical videos from an idea using Gemini for scripting and prompt architecture, ElevenLabs for voiceover, Veo for visuals, and FFmpeg for final composition. Use when you need a robust script -> prompts -> TTS -> video -> MP4 workflow with optional captions and talking-scene lip-sync guidance.
---

# Reel Factory

Use this skill when the user wants a short-form narrated video or reel assembled from an idea.

The workflow is:
1. Expand the idea into a script plan.
2. Turn the script into continuity-safe Veo prompts, including on-camera speaking guidance when subjects talk.
3. Generate voiceover plus timestamp alignment with ElevenLabs. Dialogue is split into speaker turns automatically.
4. Generate visuals with the existing `veo3-video-gen` skill.
5. Composite vertical video, with captions optional.

## End-to-end

```bash
python3 {baseDir}/scripts/run_pipeline.py \
  --idea "A cinematic mountain sunset. A calm narrator explains how beautiful the sunset is." \
  --scene-count 1 \
  --duration-seconds 8 \
  --captions off
```

Outputs are written under `data/workspace/reel-runs/<timestamp>-<slug>/` by default.

## Individual steps

### 1. Scriptwriter

```bash
python3 {baseDir}/scripts/scriptwriter.py \
  --idea "Arbitrage between Kalshi and Polymarket" \
  --scene-count 3 \
  --duration-seconds 18 \
  --output data/workspace/script.json
```

### 2. Prompt Architect

```bash
python3 {baseDir}/scripts/prompt_architect.py \
  --script data/workspace/script.json \
  --output data/workspace/prompts.json
```

### 3. ElevenLabs TTS

```bash
python3 {baseDir}/scripts/tts_elevenlabs.py \
  --text-file data/workspace/voiceover.txt \
  --output-audio data/workspace/voiceover.mp3 \
  --output-alignment data/workspace/voiceover.alignment.json
```

For dialogue, speaker-labeled text such as `Bird 1: ... Bird 2: ...` is automatically split into separate turns and synthesized with separate voices.

### 4. Compose the final reel

```bash
python3 {baseDir}/scripts/compose_vertical_reel.py \
  --video data/workspace/visuals.mp4 \
  --audio data/workspace/voiceover.mp3 \
  --captions off \
  --output data/workspace/final.mp4
```

If you want captions:

```bash
python3 {baseDir}/scripts/compose_vertical_reel.py \
  --video data/workspace/visuals.mp4 \
  --audio data/workspace/voiceover.mp3 \
  --alignment data/workspace/voiceover.alignment.json \
  --captions on \
  --output data/workspace/final.mp4
```

## Environment

- `GEMINI_API_KEY` for the scriptwriter and prompt architect
- `ELEVENLABS_API_KEY` for TTS
- `ffmpeg` and `ffprobe` on PATH for composition
- The existing `veo3-video-gen` skill for visual generation

## Notes

- The pipeline defaults to `scene-count 1` for fast, reliable test renders.
- For multi-scene reels, the pipeline sends one Veo prompt per segment and enables last-frame continuity.
- Talking scenes are prompt-conditioned for visible lip-sync or beak-sync behavior.
- Captions default to `off`. Turn them on only when needed.
