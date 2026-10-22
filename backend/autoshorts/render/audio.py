"""Clip audio: cut the voice to the EDL, clean it up, add ducked music and SFX, normalise loudness."""

from __future__ import annotations

import json
import random
import re
import subprocess
from pathlib import Path

from .. import logs
from ..config import paths
from ..media import ffmpeg, ffmpeg_bin
from . import sfx as SFX
from .edl import EDL
from .plan import Sfx

log = logs.get("audio")

MUSIC_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}


def pick_music(mood: str, seed: int) -> Path | None:
    """A licensed track from the music folder. Tag files by mood in the name or a subfolder: hype_*, chill/, ..."""
    if not mood or mood == "none":
        return None
    root = paths().music
    files = [p for p in root.rglob("*") if p.suffix.lower() in MUSIC_EXTS]
    tagged = [p for p in files if (mood in p.stem.lower() or mood in p.parent.name.lower()) and "_generated_" not in p.stem]
    pool = tagged
    if not pool:
        # No licensed track for this mood: fall back to a bed synthesised on this Mac.
        from .music import MOODS, ensure_library

        if mood not in MOODS:
            return None
        pool = [p for p in ensure_library(root / "generated") if p.stem.startswith(mood + "_")]
    if not pool:
        return None
    return random.Random(seed).choice(sorted(pool))


def tts(text: str, out_wav: Path, voice: str = "") -> float:
    """Narrated intro in one of the local voices (every OS), resampled for the mix."""
    from .. import voices

    raw = out_wav.with_suffix(".raw.wav")
    # Old installs stored a macOS voice name here ("Samantha"); those map to the default local voice.
    voices.speak_to_file(text, voice if ":" in (voice or "") else "design:documentary", raw)
    ffmpeg("-i", str(raw), "-ar", "48000", "-ac", "2", str(out_wav))
    raw.unlink(missing_ok=True)
    from ..media import probe

    return probe(out_wav).duration


VOICE_CHAIN = "highpass=f=75,afftdn=nr=8:nf=-38,acompressor=threshold=0.08:ratio=3:attack=8:release=140:makeup=1.6"


def master_voice(src: str, a: float, b: float, out_wav: Path, has_audio: bool = True) -> Path:
    """The cleaned voice for source seconds [a, b], computed once per master."""
    if has_audio:
        ffmpeg("-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src, "-af",
               f"aresample=48000,aformat=channel_layouts=stereo,{VOICE_CHAIN}",
               "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out_wav))
    else:
        ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{b - a:.3f}",
               "-c:a", "pcm_s16le", str(out_wav))
    return out_wav


def build(src: str, edl: EDL, out_path: Path, *, sfx_events: list[Sfx], music: Path | None,
          music_db: float = -20, narration: Path | None = None, target_lufs: float = -14.0,
          has_audio: bool = True, denoise: bool = True, src_offset: float = 0.0,
          voice_chain: bool = True) -> Path:
    """`src` is either the source video (src_offset=0) or a master voice track starting at
    src_offset seconds of the source, already cleaned (voice_chain=False)."""
    timed = [sg for sg in edl.segments if sg.kind != "still"]
    seek = max(0.0, min((sg.src_start for sg in timed), default=0.0) - 0.5 - src_offset)
    span0 = src_offset + seek
    span1 = max((sg.src_end for sg in timed), default=0.0) + 0.5
    # Seek straight to the clip instead of decoding a two-hour episode from the top.
    inputs: list[str] = ["-ss", f"{seek:.3f}", "-t", f"{span1 - span0:.3f}", "-i", src]
    parts: list[str] = []
    labels: list[str] = []
    narr_idx = None
    if narration is not None:
        inputs += ["-i", str(narration)]
        narr_idx = 1
    # Every segment after the first starts XF earlier and is crossfaded into the previous one,
    # so jump cuts are seamless (no clicks, no dips) and the total length still matches the video.
    XF = 0.03
    for k, seg in enumerate(edl.segments):
        d = seg.duration
        lead = XF if k > 0 else 0.0
        if seg.kind == "still":
            if narr_idx is not None:
                parts.append(f"[{narr_idx}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{d + lead:.4f},"
                             f"asetpts=PTS-STARTPTS[s{k}]")
            else:
                parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d + lead:.4f}[s{k}]")
        elif has_audio:
            a0 = max(0.0, seg.src_start - span0 - lead)
            parts.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,atrim=start={a0:.4f}:"
                         f"end={seg.src_end - span0:.4f},asetpts=PTS-STARTPTS[s{k}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d + lead:.4f}[s{k}]")
        labels.append(f"[s{k}]")
    if len(labels) == 1:
        parts.append(f"{labels[0]}anull[cat]")
    else:
        prev = labels[0]
        for k in range(1, len(labels)):
            out = "[cat]" if k == len(labels) - 1 else f"[x{k}]"
            parts.append(f"{prev}{labels[k]}acrossfade=d={XF}:c1=qsin:c2=qsin{out}")
            prev = out
    chain = VOICE_CHAIN if denoise else VOICE_CHAIN.replace(",afftdn=nr=8:nf=-38", "")
    if not voice_chain:
        chain = "anull"
    parts.append(f"[cat]{chain}[voice]")

    mix = ["[voice]"]
    n_in = 1 + (1 if narration is not None else 0)
    if music is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        mi = n_in
        n_in += 1
        dur = edl.duration
        parts[-1] = f"[cat]{chain},asplit=2[voice][sc]"
        parts.append(f"[{mi}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{dur:.3f},"
                     f"volume={music_db}dB,afade=t=in:d=0.6,afade=t=out:st={max(0, dur - 1.2):.3f}:d=1.2[mus]")
        # Duck ~12 dB under speech with a slow release so it doesn't pump.
        parts.append("[mus][sc]sidechaincompress=threshold=0.015:ratio=6:attack=20:release=600:knee=4[ducked]")
        mix.append("[ducked]")
    for j, ev in enumerate(sfx_events):
        inputs += ["-i", str(SFX.path_for(ev.kind))]
        ms = max(0, int(ev.at * 1000))
        parts.append(f"[{n_in}:a]volume={ev.gain_db}dB,adelay={ms}|{ms}[fx{j}]")
        mix.append(f"[fx{j}]")
        n_in += 1
    if len(mix) > 1:
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first:dropout_transition=0[mixed]")
        out_label = "[mixed]"
    else:
        out_label = "[voice]"

    raw = out_path.with_suffix(".raw.wav")
    ffmpeg(*inputs, "-filter_complex", ";".join(parts), "-map", out_label, "-t", f"{edl.duration:.4f}",
           "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(raw))

    # Two-pass EBU R128 normalisation to the platform target.
    probe_cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(raw), "-af",
                 f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    proc = subprocess.run(probe_cmd, capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "", re.S)
    if m:
        st = json.loads(m.group(0))
        ln = (f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:measured_I={st['input_i']}:measured_TP={st['input_tp']}:"
              f"measured_LRA={st['input_lra']}:measured_thresh={st['input_thresh']}:offset={st['target_offset']}:"
              "linear=true")
    else:
        ln = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    ffmpeg("-i", str(raw), "-af", f"{ln},aresample=48000", "-c:a", "aac", "-b:a", "192k", str(out_path))
    raw.unlink(missing_ok=True)
    return out_path
