# Tech stack and architecture

These documents explain how AutoShorts is built: what each part does, how data flows through it, and why the main design decisions were made.

| Document | What it covers |
|---|---|
| [01 · System overview](01-system-overview.md) | The big picture: processes, components, storage, tech choices |
| [02 · Clip pipeline](02-clip-pipeline.md) | From a pasted link to rendered variants, stage by stage |
| [03 · Editing engine](03-editing-engine.md) | Cut lists, reframing, captions, overlays, audio, master/assembly rendering, live text |
| [04 · Faceless studio](04-faceless-studio.md) | Script writing, fact grounding, formats, voices, images, storyboard rebuilds |
| [05 · AI models and runtimes](05-ai-models.md) | Which models do what, how they run per platform, memory management |
| [06 · Publishing and learning](06-publishing-and-learning.md) | Review, scheduling, platform APIs, analytics, the bandit, comments and DM funnels |
| [07 · Design decisions](07-design-decisions.md) | Trade-offs, problems that came up, and how they were solved |
| [08 · Platform support](08-platform-support.md) | macOS, Windows and Linux: what runs where |

## One-paragraph summary

AutoShorts is a local-first video pipeline. A Python engine (FastAPI + SQLite + a custom job queue) downloads a long video, transcribes it with Whisper, aligns every word with wav2vec2, and asks a local LLM to find complete, self-contained moments. Each moment is rendered once into a high-resolution *master* (speaker-tracked framing, colour grade, cleaned audio), and variants are *assembled* from that master in seconds. Text is never baked in during review: the React dashboard draws captions and titles live over the video, and the text is burned in only when a clip is approved. Approved clips are scheduled and published through the official YouTube and Instagram APIs, metrics flow back, and a Thompson-sampling bandit learns which hooks, caption styles, lengths and edit styles perform best. A second pipeline generates original faceless videos from a topic or script using local voices and an image model.

## Stack at a glance

| Layer | Technology |
|---|---|
| Engine | Python 3.11+, FastAPI, Uvicorn, SQLModel (SQLAlchemy) on SQLite in WAL mode |
| Jobs | Custom SQLite-backed queue with heavy/light lanes, a scheduler thread, retries and cancellation |
| Media | ffmpeg (decode, encode, loudness), OpenCV and NumPy for per-frame compositing |
| Text | CoreText on macOS, Skia + HarfBuzz elsewhere (correct Devanagari shaping and colour emoji) |
| Vision | Apple Vision on macOS, OpenCV YuNet elsewhere (face boxes, landmarks, mouth activity) |
| Speech | Whisper large-v3-turbo (MLX or faster-whisper), wav2vec2 CTC forced alignment |
| LLM | Qwen 3.5 9B 4-bit via MLX, or any local OpenAI-compatible server (Ollama, LM Studio, llama.cpp) |
| Voices | Qwen3-TTS (designed, preset and cloned voices), Kokoro, Chatterbox |
| Images | Z-Image-Turbo (mflux on Apple Silicon, diffusers on CUDA) |
| Dashboard | React 18, TypeScript, Vite, Tailwind CSS, React Router |
| Desktop | Swift AppKit + WKWebView shell for macOS |
| Publishing | YouTube Data API v3 (OAuth), Instagram Graph API, optional Cloudflare tunnel for media hosting |
