# AutoShorts

An open-source studio that turns long videos into ready-to-post Shorts and Reels, and makes original faceless videos, entirely on your own machine.

Paste a link. AutoShorts finds the moments worth clipping, edits them in a modern short-form style, checks them, and schedules them to your YouTube and Instagram accounts. Every model runs locally: no paid APIs, no cloud processing, no per-minute fees.

> **Status:** early and actively developed. It currently runs on Apple Silicon (M1 or newer). Linux and Windows support is planned; see [Roadmap](#roadmap).

---

## Features

**Clips from long videos**
- Word-level transcription with Whisper, plus forced alignment so cuts never land inside a word
- A local LLM reads the whole episode like an editor and picks complete arcs (a hook question and its full answer), not random fragments
- Each candidate is scored on hook, standalone sense, emotion, surprise, value and payoff, then refined with audio signals

**Editing**
- 9:16 reframing that follows the active speaker, with split-screen, group, screencast and fit layouts
- Animated captions in several styles, with stressed words highlighted; English and Hindi (Devanagari)
- Jump cuts that remove dead air, fillers and stutters, with crossfaded audio and loudness normalised to -14 LUFS
- Hook title on the first frame, punch-ins on emphasis, motion-graphic cards, optional AI B-roll and a progress bar
- Live text editing: captions, hook, titles and position update instantly in the preview and are burned in only on approval
- Master and assembly rendering: the slow analysis runs once per clip, so changing the edit style or opening takes seconds

**Faceless studio**
- Seven formats: story, top-N countdown, rapid facts, quiz, would-you-rather, text story and motivational quote
- Script from a topic (with Wikipedia grounding and fact-checking), from your own script file, or remixed from a reference video's idea
- Locally generated voices and images with a consistent narrator and character design
- Storyboard editor: review the script before anything is drawn, edit lines, redraw single shots, and rebuild only what changed
- Royalty-free background music and sound design generated in code

**Publishing and growth**
- Multiple YouTube channels and Instagram professional accounts through their official APIs
- Per-account best-time scheduling, A/B variants, and a bandit that learns which hooks, styles and lengths perform
- Analytics, comment triage, and comment-to-DM funnels for affiliate links
- A rights registry: every source must trace to content you own or have permission to use

---

## How it works

```
link or file
  -> download -> transcribe (Whisper) -> align words -> find moments (local LLM)
  -> build master per moment (framing, grade, clean audio)
  -> assemble variants (cuts, zooms, cards, music) -> quality checks
  -> review in the dashboard (live text edits) -> burn text on approve -> schedule -> publish
  -> collect metrics -> update the optimizer
```

The engine is a Python service (FastAPI, SQLite, a local job queue) with a React dashboard. Heavy work runs in a background worker, one large model in memory at a time, so a 16 GB machine is enough.

---

## Requirements

- macOS on Apple Silicon, 16 GB RAM recommended (8 GB works with the smaller LLM)
- Python 3.11+, Node 18+, ffmpeg
- About 20 GB of free disk space for models, downloaded on first use

## Quick start

```bash
git clone https://github.com/shubhxtech/AutoShorts.git
cd AutoShorts
make setup      # Python environment and dashboard build
make up         # starts the dashboard, worker and scheduler
```

Open http://localhost:3000, paste a video link you have the rights to, and the first clips appear in **Review**.

To try it without any footage, run `make demo`. To build the desktop app, run `make mac` (outputs to `dist/`).

## Connecting accounts

- **YouTube:** create a Google Cloud OAuth client (web application), add the redirect URI shown in Settings, upload the `client_secret.json`, then use **Pages -> Connect YouTube**. New API projects upload as private until Google's audit is approved.
- **Instagram:** use a professional account and a long-lived token from a Meta app, pasted under **Pages -> Connect Instagram**. Comment-to-DM needs the `instagram_business_manage_messages` permission.

Tokens are stored locally in the data folder and never leave your machine except to call those APIs.

## Models

| Task | Default model |
|---|---|
| Transcription | `whisper-large-v3-turbo` (MLX) |
| Language model | `Qwen3.5-9B` 4-bit (a 4B option for 8 GB machines) |
| Word alignment | `wav2vec2-base-960h` |
| Voices | Qwen3-TTS (designed and cloned voices), Kokoro, Chatterbox |
| Images | Z-Image-Turbo 4-bit |

Models can be swapped in **Settings**. Check each model's license before commercial use.

---

## Project layout

```
backend/clipforge/   engine: ingest, understand, mining, render, quality, publish, analytics, faceless, api
frontend/            React + TypeScript dashboard
macos/               desktop app shell, installer and build script
```

The Python package is currently named `clipforge`. App data lives outside the repository (`~/Library/Application Support/ClipForge` by default; override with `CLIPFORGE_HOME`).

## Development

```bash
make dev        # engine on :3000, hot-reloading dashboard on :5173
make test       # backend tests
```

## Roadmap

- Linux and Windows support (CUDA and CPU model backends, cross-platform face tracking and text rendering)
- Docker image for one-command setup
- More faceless formats and caption styles
- TikTok publishing

## Responsible use

AutoShorts is built for content you own or are licensed to use. The rights registry, credits and official platform APIs are there to keep your accounts safe. Don't use it to repost other creators' work without permission.
