# 07 · Design decisions

The main trade-offs, and the problems that shaped the current design.

## Local-first, no paid APIs

**Decision:** every model runs on the user's machine.
**Why:** creators who post several clips a day would pay per minute on cloud tools. Running locally also keeps unpublished content private and works offline.
**Cost:** a 16 GB laptop is the floor, and memory has to be managed carefully (see [05](05-ai-models.md)).

## SQLite as database and job queue

**Decision:** one SQLite file in WAL mode holds all state, including the queue.
**Why:** zero setup for non-technical users, jobs survive restarts, and the API and worker coordinate through rows instead of a broker.
**Cost:** single machine only. That's fine for this product; a multi-user server would move to Postgres and a real broker.

## Two processes, two lanes

**Decision:** an API process that never runs models, and a worker with one heavy slot and three light slots.
**Why:** the dashboard stays responsive during renders, heavy jobs can't fight over memory, and publishing and burns don't wait behind a 10-minute faceless build.

## LLM where judgement is needed, code where it isn't

**Decision:** the LLM picks moments, boundaries, hooks and edit ideas. Deterministic code does the timing: where cuts, zooms and captions land.
**Why:** a 9B model is good at "does this make sense alone?" but unreliable at frame-accurate placement. Every LLM output is schema-validated and repaired.

## Master + assembly rendering

**Problem:** every setting change re-ran the whole render (80-100 s).
**Decision:** split rendering into a cached master per moment (the slow analysis and compositing) and a fast assembly per variant.
**Result:** after the first render, changing the edit style or opening takes about 15 s.

## Text is never baked in during review

**Problem:** moving captions or fixing a typo meant re-rendering.
**Decision:** drafts have no text. The browser draws text live from an overlay spec, and text is burned in only on approval.
**Result:** text edits are instant; the final burn takes about 20 s. The preview and burn share layout rules, so what you see is what gets posted.

## Making cuts never split words

**Problem:** Whisper timestamps drift, and Hindi transcripts have overlapping word times, so cuts sometimes clipped syllables.
**Decision:** wav2vec2 forced alignment on the chosen ranges, plus padding that is clamped against neighbouring words, plus a QC check that fails any clip with a mid-word cut.

## Clips that make sense on their own

**Problem:** early clips started mid-thought or ended before the answer.
**Decision:** an editor pass where the LLM sees context around each candidate and chooses start, end and lines to drop, with Hindi-aware sentence snapping and a "makes sense alone" gate.

## Hindi, not Urdu

**Problem:** Whisper labels Hindi speech as Urdu, producing Urdu-script captions.
**Decision:** an allowed-language list (English and Hindi), re-transcription when another language is detected, Devanagari-safe text normalisation, and LLM sentence restoration for unpunctuated Hindi.

## One narrator per video

**Problem:** designed voices are re-invented on every call, so a narrator changed between scenes.
**Decision:** design each voice once into a reference clip, then clone it for every line. Pitch spread across lines dropped from about 28 Hz to 1 Hz.

## Fast, consistent faceless videos

**Problems:** slow pacing (one image per 4-5 s), characters changing between scenes, fake text in images, wrong quiz answers.
**Decisions:** a shot assembler that cuts about every 2 s with varied camera moves; style-first prompts with one seed; prompts that ban text and charts; and evidence-checked quiz questions.

## Incremental faceless rebuilds

**Decision:** cache every voice line and image by the exact input that produced it.
**Result:** editing one line re-voices one scene, a redraw draws one image, and a crashed or restarted build resumes from where it stopped.

## Secure by default, even on localhost

**Problem:** "it only listens on 127.0.0.1" isn't enough. A web page can reach local services through DNS rebinding, and the Instagram tunnel briefly made the whole app public.
**Decision:** a Host-header check, a per-install access token (cookie for the dashboard, header for native clients), and a tunnel that only reaches a media-only server.
**Result:** nothing but your own dashboard and the macOS app can call the API, and the public tunnel can serve exactly one signed file.

## Rights gate

**Decision:** every video must trace to a rights record, credits are kept, and only official platform APIs are used.
**Why:** reposting other people's content gets accounts struck and is penalised as unoriginal; the tool is built for content you own or have permission to use.
