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

## Status

The macOS path is the most tested. The Windows and Linux paths are implemented and their components are tested individually on macOS by forcing the portable backends (text, faces, LLM server, encoders, Kokoro voices); full end-to-end testing on Windows and NVIDIA hardware is the next step.
