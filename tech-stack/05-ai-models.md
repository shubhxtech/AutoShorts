# 05 · AI models and runtimes

Every model runs on the user's machine. Nothing is sent to a paid API.

| Job | Model | Apple Silicon | Windows / Linux |
|---|---|---|---|
| Transcription | Whisper large-v3-turbo | mlx-whisper | faster-whisper (CUDA float16, or CPU int8) |
| Word alignment | wav2vec2-base-960h (CTC) | PyTorch on MPS | PyTorch on CUDA or CPU |
| Language model | Qwen 3.5 9B, 4-bit | mlx-lm, in process | any local OpenAI-compatible server: Ollama, LM Studio, llama.cpp, vLLM |
| Voices | Qwen3-TTS 1.7B (preset, designed, cloned), Kokoro 82M, Chatterbox Turbo | mlx-audio | qwen-tts, kokoro, chatterbox-tts (PyTorch) |
| Images | Z-Image-Turbo | mflux, 4-bit | diffusers, CUDA (CPU offload under 16 GB VRAM) |
| Faces | — | Apple Vision | OpenCV YuNet (230 KB ONNX) |

Each subsystem picks its backend once with an `auto` rule and can be forced with a setting or environment variable for testing (`llm_backend`, `AUTOSHORTS_TEXT`, `AUTOSHORTS_FACES`, `AUTOSHORTS_VOICES`, `AUTOSHORTS_IMAGES`).

## Why these models

- **Whisper large-v3-turbo**: near large-v3 accuracy at several times the speed, and it handles Hindi and code-switched Hinglish.
- **Qwen 3.5 9B (4-bit)**: the strongest model that fits comfortably next to everything else in 16 GB, good at Hindi, and reliable at following a JSON schema. A 4B option exists for 8 GB machines.
- **Qwen3-TTS**: the most natural local voices tested, with voice design from a text description and voice cloning from a short reference.
- **Kokoro**: tiny and fast, a good fallback and the Hindi option.
- **Z-Image-Turbo**: high quality in 8 steps, which keeps a faceless video to about a minute per picture on a laptop.

## Talking to the LLM

All LLM work goes through `llm.structured(schema, system, user)`:

1. The Pydantic schema is rendered as JSON Schema and put in the system prompt (and JSON mode is requested from servers).
2. The reply is parsed tolerantly: `<think>` blocks and code fences are stripped, trailing commas fixed, and the first complete JSON object extracted.
3. It is validated against the schema. On failure, the error is fed back to the model for one corrected retry at a lower temperature.
4. Schemas use lenient validators (null becomes an empty list, "#3" becomes 3), because small models are inconsistent.

## Memory management

A 16 GB laptop can't hold Whisper, a 9B LLM, a TTS model and an image model at once, so:

- **One heavy job at a time.** The heavy lane has a single slot.
- **Load, use, release.** Each stage unloads the previous model before loading its own (the LLM is unloaded before transcription, the image model is released after each batch).
- **MLX cache limit.** MLX keeps freed GPU buffers for reuse by default. Capping that cache and clearing it after each image took faceless image generation from 12 GB with heavy swapping (4-5 minutes per image) to 5.5 GB (about 1 minute per image).
- **Idle unload.** Models stay cached between jobs so the next job starts fast, but after `unload_idle_minutes` (default 3) of no work, the worker frees them all.
- **Manual free.** The sidebar shows the engine's RAM and loaded models, with a **Free** button. If a job is running, the request waits until it finishes, so a model is never pulled out from under a job.
