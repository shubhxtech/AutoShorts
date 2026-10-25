# 08 · Platform support

| Capability | macOS (Apple Silicon) | Windows 10/11 | Linux |
|---|---|---|---|
| Dashboard, queue, publishing | yes | yes | yes |
| Transcription | mlx-whisper | faster-whisper (CUDA or CPU) | faster-whisper (CUDA or CPU) |
| Language model | built in (MLX) | Ollama / LM Studio | Ollama / LM Studio / llama.cpp |
| Face tracking | Apple Vision | YuNet | YuNet |
| Text (Hindi, emoji) | CoreText | Skia + HarfBuzz | Skia + HarfBuzz |
| Voices | mlx-audio | PyTorch (CUDA recommended) | PyTorch (CUDA recommended) |
| Images | mflux | diffusers (NVIDIA GPU recommended) | diffusers (NVIDIA GPU recommended) |
| Hardware encoding | VideoToolbox | NVENC / Quick Sync / AMF, else x264 | NVENC / Quick Sync / AMF, else x264 |
| Desktop app | native shell | browser at localhost:3000 | browser at localhost:3000 |

## Hardware guidance

- **Clips from podcasts** run on any modern machine. Without a GPU, transcription and the LLM are slower but fine.
- **Faceless videos** need image generation. That's practical on Apple Silicon or an NVIDIA GPU with 8 GB+ VRAM; on CPU each image takes several minutes (use Faceless quality: fast).
- **RAM**: 16 GB recommended. Models load one at a time and are freed when idle.

## How the backends are chosen

Each subsystem checks what's installed and picks the best option, so the same code runs everywhere:

```
LLM      mlx-lm importable?        -> MLX, else local server (Ollama default)
Whisper  mlx-whisper importable?   -> MLX, else faster-whisper (CUDA if present)
Faces    macOS + Vision?           -> Vision, else YuNet, else Haar
Text     CoreText available?       -> CoreText, else Skia, else Pillow
Voices   mlx-audio importable?     -> MLX, else PyTorch packages
Images   mflux importable?         -> MLX, else diffusers
Encoder  first working of VideoToolbox, NVENC, QSV, AMF -> else libx264
```

Hardware encoders are verified with a tiny test encode before use, because Windows ffmpeg builds list NVENC even on machines without an NVIDIA GPU.

## Windows setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
scripts\start_windows.bat
```

The script installs Python, Node, ffmpeg and Ollama with winget if they're missing, installs CUDA PyTorch when it finds an NVIDIA GPU, builds the dashboard and pulls the default model.

## Docker

`docker compose up -d` runs two containers: AutoShorts and Ollama. The image is built in two stages (Node builds the dashboard, then a slim Python image with ffmpeg and the skia libraries installs the engine with CPU PyTorch). Environment variables point the engine at the Ollama container (`AUTOSHORTS_LLM_BACKEND=server`, `AUTOSHORTS_LLM_URL`); `settings.get` reads them when the setting hasn't been changed in the dashboard. Everything stateful lives in the `/data` volume, including the Hugging Face model cache, so rebuilding the image doesn't re-download models. The port is published to `127.0.0.1` only. CI builds the image and checks the engine and dashboard answer on every push.

A Mac can't pass Apple silicon (MLX, Metal) into a Linux container, so on macOS the native app stays the way to run it.

## MCP server

`autoshorts mcp` is a stdio [MCP](https://modelcontextprotocol.io) server (`autoshorts/mcp_server.py`) for AI assistants. It's deliberately a thin client of the HTTP API rather than a second way into the database: it sends the same per-install token the dashboard uses, so the rights gate, the review queue and validation apply unchanged, and the server never needs its own copy of the business rules. Errors from the engine come back to the assistant as readable tool errors ("Confirm that you own this video..."). Approving is a separate tool whose description tells the assistant to ask the user first.

## Status

The macOS path is the most tested. The Windows and Linux paths are implemented and their components are tested individually on macOS by forcing the portable backends (text, faces, LLM server, encoders, Kokoro voices); full end-to-end testing on Windows and NVIDIA hardware is the next step.
