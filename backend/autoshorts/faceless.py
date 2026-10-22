"""Faceless AI videos, fully on-device.

topic (or a reference video to remix) -> local LLM writes an original script split into scenes
-> Kokoro reads it -> Z-Image-Turbo draws each scene in one consistent style -> scenes get a slow
Ken Burns move timed to the narration -> the result goes through the normal editor (captions, hook,
quality checks, review queue, publishing).

Unlike stock-footage generators, every visual is drawn for its exact line of the script.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field, field_validator

from . import llm, logs, settings
from .config import paths
from .db import save, session
from .jobs.queue import JobContext, enqueue, task
from .media import ffmpeg, probe
from .models import Moment, Source, Video, utcnow

log = logs.get("faceless")

STYLES = {
    "doodle": "simple hand-drawn doodle illustration, thick black outlines, flat pastel colors, white paper background, cute minimal characters",
    "cinematic": "cinematic film still, dramatic lighting, shallow depth of field, rich color grade, 35mm",
    "anime": "anime illustration, vibrant colors, clean line art, studio quality, detailed background",
    "3d": "3d render, pixar style, soft global illumination, vibrant colors, cute characters",
    "realistic": "photorealistic photo, natural light, high detail, documentary style",
    "comic": "bold comic book panel, halftone shading, strong ink lines, dynamic composition",
    "watercolor": "soft watercolor painting, gentle textures, muted palette, storybook illustration",
    "clay": "claymation stop-motion style, handmade plasticine characters, soft studio lighting, tactile textures",
    "noir": "black and white film noir photograph, hard shadows, high contrast, moody fog, 1940s",
    "papercut": "layered paper cut-out illustration, depth and soft shadows between layers, bold flat colors",
    "vintage": "vintage 1970s photograph, film grain, warm faded colors, authentic period detail",
    "flat": "clean flat vector illustration, bold simple shapes, limited color palette, modern explainer style",
    "dark_fantasy": "dark fantasy digital painting, dramatic rim light, epic scale, volumetric fog, painterly",
}
VOICES = {  # Kokoro voices: first letter = language (a US, b UK, h Hindi, e Spanish, ...), second = gender
    "af_heart": "US female (warm)", "af_bella": "US female (energetic)", "am_michael": "US male (deep)",
    "am_adam": "US male", "bf_emma": "UK female", "bm_george": "UK male",
    "hf_alpha": "Hindi female", "hm_omega": "Hindi male",
}
KOKORO = "mlx-community/Kokoro-82M-bf16"


class Scene(BaseModel):
    narration: str = Field(description="what the narrator says in this scene: 1-2 short sentences")
    visual: str = Field(description="what the image shows, concrete and specific: subject, action, setting (no text in the image)")
    visual2: str = Field("", description="optional second shot of this scene (another angle, a close-up detail, or option B)")
    kind: str = Field("beat", description="beat | intro | item | question | answer | choice | outro")
    heading: str = Field("", description="short on-screen text for this scene when the format uses one")
    number: int | None = Field(None, description="list number for 'item' scenes")
    options: list[str] = Field(default_factory=list, description="answer options (question) or the two choices (choice)")
    answer: int | None = Field(None, description="index of the correct option for 'question' scenes")
    evidence: str = Field("", description="question scenes only: the exact sentence from SOURCE FACTS that proves the answer")

    @field_validator("visual2", "kind", "heading", "evidence", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v)

    @field_validator("options", mode="before")
    @classmethod
    def _opts(cls, v):
        if v is None:
            return []
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]

    @field_validator("number", "answer", mode="before")
    @classmethod
    def _int(cls, v):
        try:
            return None if v in (None, "") else int(str(v).strip().lstrip("#"))
        except ValueError:
            return None


class Script(BaseModel):
    title: str = Field(description="video title, max 8 words")
    hook: str = Field(description="on-screen hook for the first 2 seconds, max 8 words")
    character: str = Field("", description="the main recurring character, described the same way every time: age, look, clothing (e.g. 'a stone age man with a brown beard in fur clothes'); empty if none")
    setting: str = Field("", description="the world all scenes share: era, place, mood (e.g. 'prehistoric cave camp, 31,000 years ago')")
    mood: str = Field("inspiring", description="background music mood: chill | hype | emotional | suspense | inspiring | funny")
    scenes: list[Scene] = Field(description="the scenes in order; the first scene's narration is the spoken hook")
    youtube_title: str
    instagram_caption: str
    hashtags: list[str]
    format: str = "story"
    language: str = "English"


SYSTEM = """You write scripts for viral faceless short videos (YouTube Shorts / Reels).
Open with a spoken hook in the first sentence that creates curiosity. Fast escalating beats, no filler, no "in this video", no "let's dive in". Short spoken sentences.
Every scene needs a concrete visual that matches its exact line. Visuals must fit the setting's era and place (never modern clothes, tools or buildings in a historical setting) and show the same main character, described the same way, wherever they appear. Never put words or numbers in visuals, and never ask for things that need writing to make sense (charts, graphs, labelled maps, calendars, signs, documents, screens, scoreboards): show people, places, objects and actions instead.
Be accurate: if the topic is factual, only state well-established facts. If the topic is a future, predicted or hypothetical event, frame it clearly as a prediction or a "what if" (never as something that already happened), and never invent quotes from real people."""


WIKI_HEADERS = {"User-Agent": "AutoShorts/0.2 (local short-video studio; https://www.mediawiki.org/wiki/API:Etiquette) python-httpx"}


class Queries(BaseModel):
    queries: list[str] = Field(description="2-3 short Wikipedia search queries, most specific first (names, places, dates)")


def wikipedia_facts(topic: str, max_chars: int = 5000) -> tuple[str, str]:
    """Grounding text for factual topics: the best-matching Wikipedia paragraphs (free, no key)."""
    import httpx

    queries = [topic]
    try:
        q = llm.structured(Queries, "You turn a video topic into Wikipedia search queries.", f"Topic: {topic}",
                           max_tokens=200, temperature=0.1)
        queries = [x for x in q.queries if x.strip()][:3] + [topic]
    except Exception:
        log.info("couldn't write Wikipedia queries; searching the topic as typed", exc_info=True)
    try:
        hits, seen = [], set()
        for query in queries:
            r = httpx.get("https://en.wikipedia.org/w/api.php", timeout=15, headers=WIKI_HEADERS,
                          params={"action": "query", "list": "search", "srsearch": query, "srlimit": 2, "format": "json"})
            for h in r.json().get("query", {}).get("search", []):
                if h["title"] not in seen:
                    seen.add(h["title"])
                    hits.append(h)
        topic = " ".join(queries)
        texts = []
        for h in hits[:5]:
            e = httpx.get("https://en.wikipedia.org/w/api.php", timeout=15, headers=WIKI_HEADERS,
                          params={"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "plain",
                                  "titles": h["title"], "format": "json"}).json()
            page = next(iter(e.get("query", {}).get("pages", {}).values()), {})
            if page.get("extract"):
                texts.append((h["title"], page["extract"]))
        # Keep only the paragraphs that are actually about the topic.
        stop = {"the", "a", "an", "of", "and", "who", "that", "was", "is", "in", "on", "to", "for", "with"}
        keys = {w for w in re.findall(r"[a-z]+", topic.lower()) if w not in stop and len(w) > 2}
        scored = []
        for title, text in texts:
            for para in text.split("\n"):
                toks = set(re.findall(r"[a-z]+", para.lower()))
                hit = len(keys & {t[:6] for t in toks} | keys & toks)
                if len(para) > 120 and hit:
                    scored.append((hit, title, para))
        scored.sort(key=lambda x: -x[0])
        out, used, n = [], [], 0
        for _hit, title, para in scored:
            if n + len(para) > max_chars:
                break
            out.append(para)
            n += len(para)
            if title not in used:
                used.append(title)
        return "\n\n".join(out), ", ".join(used)
    except Exception as exc:
        log.info("wikipedia lookup failed: %s", exc)
        return "", ""


class FactCheck(BaseModel):
    unsupported: list[str] = Field(description="claims in the script that the source text does not support")
    narrations: list[str] = Field(description="every scene's narration in the same order and count, rewritten only where needed so each claim is supported")


def fact_check(script: Script, source: str) -> Script:
    """Fix unsupported claims line by line; scene structure (numbers, options, visuals) is left alone."""
    listing = "\n".join(f"{i + 1}. {s.narration}" for i, s in enumerate(script.scenes))
    try:
        res = llm.structured(FactCheck, "You are a strict fact-checker for short educational videos. Keep the "
                             "drama, length and pacing of each line, but every factual claim must be supported by the "
                             "source text. Rewrite only lines that need it; return all lines.",
                             f"Source text:\n{source[:6000]}\n\nScript lines:\n{listing}\n\nCheck and fix.",
                             max_tokens=2500, temperature=0.2)
    except Exception as exc:
        log.warning("fact check failed: %s", exc)
        return script
    if len(res.narrations) == len(script.scenes):
        for sc, line in zip(script.scenes, res.narrations):
            line = re.sub(r"^\s*\d+[.)]\s*", "", line).strip()
            if line:
                sc.narration = line
        if res.unsupported:
            log.info("fact check fixed %d claims: %s", len(res.unsupported), res.unsupported[:3])
    return script


def write_script(topic: str, reference: str, language: str, seconds: int, niche: str, cta: str,
                 facts: str = "", fmt: str = "story") -> Script:
    from .faceless_formats import FORMATS

    f = FORMATS.get(fmt, FORMATS["story"])
    words = int(seconds * 2.5)
    ref = f"\n\nReference transcript to remix into an ORIGINAL script (new wording, new angle, don't copy sentences):\n{reference[:6000]}" if reference else ""
    if facts:
        ref += ("\n\nSOURCE FACTS (use only facts stated here; if a detail isn't here, leave it out):\n" + facts[:6000])
    cta_line = f'\nThe last scene ends with: "Comment {cta.upper()} and I\'ll send you the link."' if cta else ""
    lang = ""
    if language.lower().startswith("hi"):
        lang = ("\nWrite narration, headings and options in natural spoken Hindi in Devanagari script (common English "
                "words may stay in English). Visual descriptions stay in English.")
    prompt = (f"FORMAT: {f.name}. {f.prompt}\n\nTopic: {topic or 'from the reference'}\nNiche: {niche or 'general'}\n"
              f"Language: {language}{lang}\nLength: about {words} spoken words total ({seconds} seconds).{cta_line}{ref}")
    script = llm.structured(Script, SYSTEM, prompt, max_tokens=3500, temperature=0.7)
    script.format, script.language = f.id, language
    return tidy(script)


def _supported(evidence: str, answer: str, facts: str) -> bool:
    """Is the quoted evidence really in the source, and does it contain the answer?"""
    def toks(t: str) -> set[str]:
        return {w for w in re.findall(r"\w+", t.lower()) if len(w) > 2}
    ev, src = toks(evidence), toks(facts)
    if len(ev) < 4 or not src:
        return False
    in_source = len(ev & src) / len(ev) >= 0.8
    ans = toks(answer) or {answer.lower().strip()}
    return in_source and bool(ans & ev)


def verify_quiz(script: Script, facts: str) -> Script:
    """Drop question/answer pairs whose answer isn't backed by the source text (a wrong quiz kills trust)."""
    if script.format != "quiz" or not facts:
        return script
    out, skip_answer, kept, dropped = [], False, 0, []
    for sc in script.scenes:
        if sc.kind == "question":
            ok = sc.answer is not None and 0 <= sc.answer < len(sc.options) and \
                _supported(sc.evidence, sc.options[sc.answer], facts)
            if not ok:
                dropped.append(sc.heading or sc.narration)
                skip_answer = True
                continue
            kept += 1
            skip_answer = False
        elif sc.kind == "answer" and skip_answer:
            skip_answer = False
            continue
        out.append(sc)
    if kept >= 2:
        script.scenes = out
        if dropped:
            log.info("quiz: dropped %d unsupported questions: %s", len(dropped), dropped[:3])
    else:
        log.warning("quiz: only %d questions are backed by the source; keeping the script as written", kept)
    return script


def tidy(script: Script) -> Script:
    """Repair what small models get wrong per format, so the graphics never break."""
    fmt = script.format
    out: list[Scene] = []
    for sc in script.scenes:
        sc.kind = (sc.kind or "beat").lower().strip()
        sc.narration = clean_markup(sc.narration)
        if fmt == "quiz" and sc.kind == "question":
            if not sc.heading or re.fullmatch(r"(question|q)\s*#?\d*[:.]?", sc.heading.strip(), re.I):
                # Small models often write "Question 1" as the heading: show the real question instead.
                sc.heading = re.split(r"\s*(?:Options?|Is it|A\))\s*[:\-]?", sc.narration, maxsplit=1)[0].strip()
            sc.options = [clean_markup(o) for o in sc.options if o.strip()][:3]
            if len(sc.options) < 2:
                sc.kind = "beat"
            elif sc.answer is None or not 0 <= sc.answer < len(sc.options):
                sc.answer = 0
        if fmt == "would_you_rather" and sc.kind == "choice":
            sc.options = [clean_markup(o) for o in sc.options if o.strip()][:2]
            if len(sc.options) < 2:
                sc.kind = "beat"
            elif not sc.visual2:
                sc.visual2 = sc.options[1]
                sc.visual = sc.visual or sc.options[0]
        if sc.kind == "answer" and not any(x.kind == "question" for x in out):
            sc.kind = "beat"
        if fmt == "quote" and not sc.heading:
            sc.heading = sc.narration
        if sc.narration:
            out.append(sc)
    items = [sc for sc in out if sc.kind == "item"]
    if fmt == "countdown":
        for i, sc in enumerate(items):
            sc.number = len(items) - i
    elif fmt == "facts":
        for i, sc in enumerate(items):
            sc.number = i + 1
    if script.mood not in ("chill", "hype", "emotional", "suspense", "inspiring", "funny"):
        script.mood = "inspiring"
    script.scenes = out
    if fmt == "text_story" and "fiction" not in script.instagram_caption.lower():
        script.instagram_caption += "\n\n(Fictional story.)"
    return script


SCRIPT_TEMPLATE = """TITLE: The Stone Age Surgeon
HOOK: They cut it off. He lived.
CHARACTER: a stone age man with a brown beard in fur clothes
SETTING: prehistoric cave camp in a Borneo rainforest, 31,000 years ago

31,000 years ago, someone cut off a child's leg. And the child survived.
[visual: a prehistoric healer kneeling beside a child in a cave, firelight]

Archaeologists found the skeleton in a cave in Borneo.
[visual: archaeologists brushing dirt off an ancient skeleton in a cave]

The bone had healed. The child lived for years after the surgery.
[visual: close-up of a healed leg bone glowing in torchlight]

Comment LINK and I'll send you the full story.
"""


class Visuals(BaseModel):
    title: str
    hook: str = Field(description="on-screen hook for the first 2 seconds, max 8 words")
    character: str
    setting: str
    visuals: list[str] = Field(description="one concrete visual per scene, same order and count as the scenes")
    youtube_title: str
    instagram_caption: str
    hashtags: list[str]


def clean_markup(text: str) -> str:
    """Strip Markdown so it is neither spoken nor captioned."""
    text = re.sub(r"[*_`#]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def speakable(text: str) -> str:
    """Text the voice model can read: ranges, arrows and symbols become words."""
    t = clean_markup(text)
    t = re.sub(r"(\d)\s*[–—-]\s*(\d)", r"\1 to \2", t)
    t = t.replace("→", " to ").replace("&", " and ").replace("%", " percent").replace("—", ", ").replace("–", ", ")
    t = re.sub(r"\.{3,}|…", "... ", t)
    t = re.sub(r"[^\w\s.,!?'\-:;$]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_script(text: str) -> dict:
    """Read a user script: JSON, the tagged text format (see SCRIPT_TEMPLATE) or plain paragraphs.

    With [visual: ...] lines, each visual closes a scene (all narration since the previous visual belongs
    to it, even across blank lines). Without any, every paragraph is a scene.
    """
    text = text.strip()
    if text.startswith("{"):
        data = json.loads(text)
        keep = ("kind", "heading", "number", "options", "answer", "visual2")
        scenes = [{"narration": clean_markup(s.get("narration") or s.get("text", "")), "visual": s.get("visual", ""),
                   **{k: s[k] for k in keep if k in s}}
                  for s in data.get("scenes", []) if (s.get("narration") or s.get("text"))]
        return {**{k: clean_markup(str(data.get(k, ""))) for k in ("title", "hook", "character", "setting")},
                "format": data.get("format", ""), "scenes": scenes}
    meta = {"title": "", "hook": "", "character": "", "setting": ""}
    tag_re = re.compile(r"^(TITLE|HOOK|CHARACTER|SETTING)\s*:\s*(.*)$", re.I)
    vis_re = re.compile(r"^\[?\s*(visual|image|scene)\s*:\s*(.+?)\]?$", re.I)
    has_visuals = any(vis_re.match(l.strip()) for l in text.splitlines())
    scenes: list[dict] = []
    pending: list[str] = []
    last_tag = None

    def close(visual: str = "") -> None:
        if pending:
            scenes.append({"narration": clean_markup(" ".join(pending)), "visual": visual})
            pending.clear()
        elif visual and scenes and not scenes[-1]["visual"]:
            scenes[-1]["visual"] = visual

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            last_tag = None
            if not has_visuals:
                close()
            continue
        m = tag_re.match(line)
        if m:
            last_tag = m.group(1).lower()
            meta[last_tag] = clean_markup(m.group(2))
            continue
        if last_tag and not pending and not scenes:
            meta[last_tag] = (meta[last_tag] + " " + clean_markup(line)).strip()   # tag value on the next line(s)
            continue
        v = vis_re.match(line)
        if v:
            close(v.group(2).strip())
            continue
        if not line.startswith("#"):
            pending.append(line)
    close()
    return {**meta, "format": "", "scenes": [sc for sc in scenes if sc["narration"]]}


def script_from_user(text: str, style: str, niche: str, fmt: str = "story") -> Script:
    """Keep the user's narration word for word; let the LLM fill anything missing (visuals, title, captions)."""
    parsed = parse_script(text)
    if not parsed["scenes"]:
        raise ValueError("the script has no narration")
    listing = "\n".join(f"{i + 1}. {s['narration']}" for i, s in enumerate(parsed["scenes"]))
    v = llm.structured(Visuals, "You plan visuals and packaging for a faceless short video. Do not change the narration.",
                       f"Niche: {niche or 'general'}\nVisual style: {style}\nKnown: {json.dumps({k: parsed[k] for k in ('title', 'hook', 'character', 'setting') if parsed[k]})}\n\n"
                       f"Scenes (narration):\n{listing}\n\nWrite one visual per scene.", max_tokens=2000, temperature=0.5)
    visuals = v.visuals + [""] * len(parsed["scenes"])
    extra = ("kind", "heading", "number", "options", "answer", "visual2")
    scenes = [Scene(narration=s["narration"], visual=s["visual"] or visuals[i] or s["narration"],
                    **{k: s[k] for k in extra if s.get(k) not in (None, "", [])})
              for i, s in enumerate(parsed["scenes"])]
    script = Script(title=parsed["title"] or v.title, hook=parsed["hook"] or v.hook,
                    character=parsed["character"] or v.character, setting=parsed["setting"] or v.setting,
                    scenes=scenes, youtube_title=v.youtube_title, instagram_caption=v.instagram_caption,
                    hashtags=v.hashtags, format=parsed.get("format") or fmt)
    return tidy(script)


def reference_transcript(url: str) -> str:
    """Grab a reference video's subtitles (or transcribe it) to remix the idea, never the footage."""
    import yt_dlp

    tmp = paths().tmp / "ref"
    tmp.mkdir(exist_ok=True)
    opts = {"quiet": True, "skip_download": True, "writeautomaticsub": True, "writesubtitles": True,
            "subtitleslangs": ["en", "hi", "en-orig"], "subtitlesformat": "vtt", "outtmpl": str(tmp / "%(id)s")}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
    for f in tmp.glob(f"{info['id']}*.vtt"):
        lines = [l.strip() for l in f.read_text(errors="ignore").splitlines()
                 if l.strip() and "-->" not in l and not l.startswith(("WEBVTT", "Kind:", "Language:"))]
        text = re.sub(r"<[^>]+>", "", " ".join(dict.fromkeys(lines)))
        if len(text) > 200:
            return text
    return info.get("description", "")


# ---------------------------------------------------------------- voice

def tts(text: str, out_wav: Path, voice: str = "kokoro:af_heart", speed: float = 1.0) -> float:
    from . import voices

    return voices.speak_to_file(text, voice, out_wav, speed)


# ---------------------------------------------------------------- project files
#
# work/{video}/
#   project.json   format, style, voice, seed, language (what the storyboard edits)
#   script.json    the script (scenes are edited in the storyboard)
#   scene_KK.png / scene_KK_b.png   drawn shots, with their prompts in images.json (redrawn only when changed)
#   vo_KK.wav      narration per scene, with its text+voice in vo.json (re-read only when changed)

def load_project(work: Path) -> dict:
    p = work / "project.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_project(work: Path, proj: dict) -> None:
    (work / "project.json").write_text(json.dumps(proj, indent=2))


def load_script(work: Path) -> Script:
    return Script.model_validate_json((work / "script.json").read_text())


def save_script(work: Path, script: Script) -> None:
    (work / "script.json").write_text(script.model_dump_json(indent=2))


def image_prompts(script: Script, style_key: str) -> list[tuple[str, str]]:
    """(prompt A, prompt B or '') per scene. Style goes first: it is what the image model weighs most."""
    style = STYLES.get(style_key, style_key or STYLES["cinematic"])
    story_like = script.format in ("story", "text_story")
    world = f" Setting: {script.setting}." if script.setting and story_like else ""
    char = f" Main character (only if in this shot): {script.character}." if script.character and story_like else ""
    tail = (" Vertical 9:16 composition, one clear subject, consistent character design, no text, no letters, no numbers,"
            " no charts, no signs, no watermark.")
    if script.format == "quote":
        tail = " Vertical 9:16, moody atmospheric, lots of negative space, no faces, no text, no letters."
    out = []
    for sc in script.scenes:
        a = f"{style}. {sc.visual}.{char}{world}{tail}"
        b = ""
        if sc.visual2:
            b = f"{style}. {sc.visual2}.{char}{world}{tail}"
        elif sc.kind == "choice" and len(sc.options) > 1:
            b = f"{style}. {sc.options[1]}.{tail}"
        out.append((a, b))
    return out


def image_paths(work: Path, k: int) -> tuple[Path, Path]:
    return work / f"scene_{k:02d}.png", work / f"scene_{k:02d}_b.png"


def draw_scenes(script: Script, style_key: str, work: Path, seed: int, progress, budget_b: set[int] | None = None) -> list[tuple[Path, Path | None]]:
    """Draw every shot whose prompt changed (or is missing). Returns (A, B or None) per scene."""
    prompts = image_prompts(script, style_key)
    meta_path = work / "images.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    jobs = []
    result: list[tuple[Path, Path | None]] = []
    for k, (pa, pb) in enumerate(prompts):
        a, b = image_paths(work, k)
        sk = seed + 97 * int(meta.get(f"bump:{k}", 0))
        if meta.get(a.name) != pa or not a.exists():
            jobs.append((pa, a, sk))
        use_b = bool(pb) and (budget_b is None or k in budget_b)
        if use_b and (meta.get(b.name) != pb or not b.exists()):
            jobs.append((pb, b, sk + 1))
        result.append((a, b if use_b else None))
    if jobs:
        from . import images

        def saved(j: int) -> None:                 # per shot, so a restart resumes from here
            meta[jobs[j][1].name] = jobs[j][0]
            meta_path.write_text(json.dumps(meta, indent=1))

        images.generate(jobs, progress, on_done=saved)
    return result


# ---------------------------------------------------------------- voice + sound design

def voiceover(script: Script, voice: str, work: Path, ctx: JobContext, lo: float, hi: float):
    """Read each scene separately (cached), then lay them out with thinking pauses and sound design.

    Returns (clean narration wav for transcription, mixed wav for the video, scene timings)."""
    from . import voices
    from .faceless_formats import FORMATS, SceneTiming, mix_sfx, read_audio, write_wav

    fmt = FORMATS.get(script.format, FORMATS["story"])
    cache_path = work / "vo.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    used = False
    clips = []
    for k, sc in enumerate(script.scenes):
        ctx.progress(lo + (hi - lo) * k / len(script.scenes), f"voiceover {k + 1}/{len(script.scenes)}")
        wav = work / f"vo_{k:02d}.wav"
        text = speakable(sc.narration)
        key = f"{voices.cache_tag(voice)}|{text}"
        if cache.get(str(k)) != key or not wav.exists():
            used = True
            try:
                tts(text, wav, voice)
            except Exception as exc:
                log.warning("voice failed on scene %d (%s); retrying with plain text", k, exc)
                tts(re.sub(r"[^\w .,!?']", " ", text), wav, voice)
            cache[str(k)] = key
            cache_path.write_text(json.dumps(cache, indent=1))
        clips.append(read_audio(wav))
    if used:
        voices.unload()
    sr = 48000
    gap = 0.10 if script.format in ("facts", "countdown", "would_you_rather") else 0.16
    timings, t = [], 0.0
    for sc, y in zip(script.scenes, clips):
        d = len(y) / sr
        think = fmt.think_s if sc.kind in ("question", "choice") else 0.0
        extra = 0.5 if script.format == "quote" else 0.0
        timings.append(SceneTiming(t, t + d, t + d + gap + think + extra))
        t = timings[-1].end
    total = t + 0.4
    narr = np.zeros(int(total * sr), np.float32)
    for tm, y in zip(timings, clips):
        i = int(tm.start * sr)
        narr[i:i + len(y)] += y[: len(narr) - i]
    timings[-1].end = total
    clean = work / "narration.wav"
    write_wav(clean, narr)
    mixed = work / "narration_mix.wav"
    write_wav(mixed, narr + mix_sfx(script.format, script.scenes, timings, total))
    return clean, mixed, timings


# ---------------------------------------------------------------- tasks

def _proj_defaults(p: dict) -> dict:
    from .faceless_formats import FORMATS

    f = FORMATS.get(p.get("format") or "story", FORMATS["story"])
    return {"format": f.id, "style": p.get("style") or f.style, "voice": p.get("voice") or "design:documentary",
            "seed": int(p.get("seed", 7)), "language": p.get("language", "English")}


@task("make_faceless", lane="heavy", max_attempts=1)
def make_faceless(ctx: JobContext, p: dict) -> None:
    from .pipeline import _set

    vid = p["video_id"]
    with session() as s:
        video = s.get(Video, vid)
        src = s.get(Source, video.source_id)
    work = paths().work / str(vid)
    work.mkdir(parents=True, exist_ok=True)
    if (work / "script.json").exists() and not p.get("review_script"):
        # Resumed after a restart: the script, voice lines and drawn shots are cached, so carry on from there.
        build(ctx, vid, work)
        return
    proj = _proj_defaults(p)
    save_project(work, proj)
    style = STYLES.get(proj["style"], proj["style"])

    _set(vid, status="mining", stage_message="writing the script")
    ctx.progress(0.02, "writing the script")
    reference = reference_transcript(p["reference_url"]) if p.get("reference_url") else ""
    facts, fact_titles = ("", "")
    if p.get("script_text"):
        script = script_from_user(p["script_text"], style, src.niche, proj["format"])
    else:
        if p.get("notes"):
            facts, fact_titles = p["notes"], "your notes"
        elif p.get("topic") and p.get("grounding", True) and proj["format"] not in ("text_story", "quote", "would_you_rather"):
            ctx.progress(0.03, "looking up the facts")
            facts, fact_titles = wikipedia_facts(p["topic"])
        script = write_script(p.get("topic", ""), reference, proj["language"], int(p.get("seconds", 45)), src.niche,
                              src.cta_keyword, facts, proj["format"])
        if facts and proj["format"] == "quiz":
            script = verify_quiz(script, facts)
        if facts and proj["format"] in ("story", "facts", "countdown"):
            ctx.progress(0.06, "fact-checking the script")
            script = tidy(fact_check(script, facts))
            if fact_titles != "your notes":
                script.instagram_caption += f"\n\nSource: Wikipedia ({fact_titles})"
    llm.unload()
    save_script(work, script)
    if p.get("review_script"):
        _set(vid, status="draft", title=script.title, stage_message="script ready: review the storyboard, then build")
        ctx.progress(1.0, "script ready for review", force=True)
        return
    build(ctx, vid, work)


@task("build_faceless", lane="heavy", max_attempts=1)
def build_faceless(ctx: JobContext, p: dict) -> None:
    """Build (or rebuild after storyboard edits) from the saved script: only changed scenes are re-voiced or redrawn."""
    vid = p["video_id"]
    work = paths().work / str(vid)
    proj = load_project(work)
    if p.get("voice"):
        proj["voice"] = p["voice"]
    if p.get("style"):
        proj["style"] = p["style"]
    save_project(work, proj)
    build(ctx, vid, work)


def build(ctx: JobContext, vid: int, work: Path) -> None:
    from .pipeline import _set

    proj = load_project(work)
    script = load_script(work)
    _set(vid, status="transcribing", stage_message="recording the voiceover")
    clean, mixed, timings = voiceover(script, proj.get("voice", "design:documentary"), work, ctx, 0.08, 0.22)
    _set(vid, status="rendering", stage_message="drawing scenes")
    # A second shot only where the scene is long enough to need a cut (saves drawing time).
    need_b = {k for k, tm in enumerate(timings) if tm.end - tm.start >= 3.0 or script.scenes[k].kind == "choice"}
    if settings.get("faceless_quality") == "fast":
        need_b = {k for k in need_b if script.scenes[k].kind == "choice"}
    images = draw_scenes(script, proj.get("style", "cinematic"), work, int(proj.get("seed", 7)),
                         progress=lambda f, m: ctx.progress(0.22 + 0.5 * f, m), budget_b=need_b)
    finish_base(vid, script, images, timings, clean, mixed, work, proj.get("voice", ""), ctx)
    ctx.progress(1.0, "sent to the editor", force=True)


def finish_base(vid: int, script: Script, images: list, timings: list, clean: Path, mixed: Path, work: Path,
                voice: str, ctx: JobContext) -> None:
    """Assemble the base video, transcribe the clean narration for exact word timings, and (re)create the moment."""
    from sqlmodel import select as _select

    from .faceless_formats import FORMATS, assemble
    from .models import Clip
    from .pipeline import _set
    from .understand.signals import build_timeline
    from .understand.transcribe import transcribe

    fmt = FORMATS.get(script.format, FORMATS["story"])
    base = paths().library / f"faceless_{vid}.mp4"
    _set(vid, status="rendering", stage_message="assembling")
    assemble(script.format, script.scenes, images, timings, mixed, base,
             progress=lambda f, m: ctx.progress(0.72 + 0.12 * f, m))
    pr = probe(base)
    wav16 = work / "audio16k.wav"
    ffmpeg("-i", str(clean), "-ac", "1", "-ar", "16000", str(wav16))
    lang = "hi" if script.language.lower().startswith("hi") else "en"
    data = transcribe(wav16, work / "transcript.json", language=lang)
    build_timeline(wav16, data, work / "timeline.json")
    (work / "voice.txt").write_text(voice)
    _set(vid, file_path=str(base), duration_s=pr.duration, width=1080, height=1920, fps=30,
         transcript_path=str(work / "transcript.json"), timeline_path=str(work / "timeline.json"),
         language=data.get("language", lang), title=script.title, status="rendering", stage_message="editing")
    overlay = {} if fmt.captions else {"hide_captions": True}
    meta = {"youtube_title": script.youtube_title, "youtube_description": script.title, "tags": script.hashtags[:12],
            "instagram_caption": script.instagram_caption, "hashtags": script.hashtags, "faceless": True,
            "format": script.format, "music_mood": script.mood, "overlay": overlay,
            "caption_styles": ["bold", "boxed", "pop"]}
    with session() as s:
        m = s.exec(_select(Moment).where(Moment.video_id == vid)).first()
        if m is None:
            m = Moment(video_id=vid, start_s=0.0, end_s=pr.duration, text=" ".join(x.narration for x in script.scenes),
                       title=script.title, hooks=[{"text": script.hook, "style": "curiosity_gap"}],
                       scores={"hook": 8, "standalone": 9, "emotion": 6, "surprise": 7, "value": 6, "payoff": 7,
                               "quotable": 6},
                       final_score=7.0, llm_score=7.0, status="selected", meta=meta)
        else:
            m.end_s = pr.duration
            m.title = script.title
            m.text = " ".join(x.narration for x in script.scenes)
            m.hooks = [{"text": script.hook, "style": "curiosity_gap"}]
            # The base video changed: cached edit plans and masters are stale.
            old = {k: v for k, v in (m.meta or {}).items() if not k.startswith("edit_plan") and k != "masters"}
            m.meta = {**old, **meta}
        s.add(m)
        s.commit()
        s.refresh(m)
        clips = s.exec(_select(Clip).where(Clip.moment_id == m.id)).all()
    if clips:
        with session() as s:
            for c in clips:
                cc = s.get(Clip, c.id)
                cc.status, cc.error = "rendering", ""
                s.add(cc)
            s.commit()
        for c in clips:
            enqueue("rerender_clip", {"clip_id": c.id}, priority=8, dedupe_key=f"rerender:{c.id}")
    else:
        enqueue("render_moment", {"moment_id": m.id}, priority=8, dedupe_key=f"render:{m.id}")


@task("revoice_faceless", lane="heavy", max_attempts=1)
def revoice_faceless(ctx: JobContext, p: dict) -> None:
    """Change the voice of a finished faceless video: reuses the script and drawn scenes."""
    from .pipeline import _set

    vid, voice = p["video_id"], p["voice"]
    work = paths().work / str(vid)
    proj = load_project(work) or _proj_defaults({})
    proj["voice"] = voice
    save_project(work, proj)
    _set(vid, status="transcribing", stage_message=f"re-voicing with {voice}")
    build(ctx, vid, work)
    ctx.progress(1.0, "re-voiced", force=True)


def create(topic: str = "", reference_url: str = "", style: str = "", voice: str = "design:documentary",
           seconds: int = 45, language: str = "English", niche: str = "", cta_keyword: str = "",
           accounts: list[int] | None = None, auto_publish: bool = False, notes: str = "",
           grounding: bool = True, script_text: str = "", fmt: str = "story", review_script: bool = False,
           seed: int = 7) -> Video:
    """Queue a faceless video. It is our own original content (AI-generated), so rights type is 'own'."""
    if script_text and not topic:
        topic = parse_script(script_text).get("title") or script_text.strip().splitlines()[0][:80]
    src = save(Source(name=f"Faceless: {(topic or reference_url)[:60]}", kind="direct_url", url="",
                      rights_type="own", proof_notes="Original AI-generated video made in AutoShorts",
                      credit_text="", niche=niche, watch=False, auto_ingest=False, cta_keyword=cta_keyword,
                      auto_publish=auto_publish, auto_publish_top=1, target_accounts=accounts or []))
    video = save(Video(source_id=src.id, external_id=f"faceless:{utcnow().timestamp()}", title=topic[:120] or "Faceless video",
                       status="queued", stage_message="queued"))
    work = paths().work / str(video.id)
    work.mkdir(parents=True, exist_ok=True)
    save_project(work, _proj_defaults({"format": fmt, "style": style, "voice": voice, "seed": seed, "language": language}))
    enqueue("make_faceless", {"video_id": video.id, "topic": topic, "reference_url": reference_url, "style": style,
                              "voice": voice, "seconds": seconds, "language": language, "notes": notes,
                              "grounding": grounding, "script_text": script_text, "format": fmt,
                              "review_script": review_script, "seed": seed}, priority=6)
    return video
