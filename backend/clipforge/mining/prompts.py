"""Prompts and output schemas for the local LLM."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

HOOK_STYLES = ("question", "bold_claim", "curiosity_gap", "story", "stat", "controversy", "warning", "relatable")
HookStyle = Literal["question", "bold_claim", "curiosity_gap", "story", "stat", "controversy", "warning", "relatable"]


def _clamp10(v):
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


class MomentScore(BaseModel):
    hook: float = Field(description="0-10: do the first 3 seconds grab a scrolling viewer?")
    standalone: float = Field(description="0-10: does it make sense with no earlier context?")
    emotion: float = Field(description="0-10: emotional peak (funny, moving, angry, inspiring)")
    surprise: float = Field(description="0-10: surprise, controversy or a counter-intuitive take")
    value: float = Field(description="0-10: practical, useful takeaway")
    payoff: float = Field(description="0-10: does it end on a satisfying punchline or conclusion?")
    quotable: float = Field(description="0-10: has a line people would quote or share")
    best_start: int = Field(description="line number the clip should start on")
    best_end: int = Field(description="line number the clip should end on (inclusive)")
    hook_style: HookStyle = "bold_claim"
    reason: str = Field(description="one short sentence on why this would or would not go viral")

    _c = field_validator("hook", "standalone", "emotion", "surprise", "value", "payoff", "quotable",
                         mode="before")(lambda v: _clamp10(v))

    @field_validator("hook_style", mode="before")
    @classmethod
    def _style(cls, v):
        v = str(v or "").strip().lower().replace(" ", "_").replace("-", "_")
        return v if v in HOOK_STYLES else "bold_claim"


SCORE_SYSTEM = """You are a short-form video editor who has made thousands of viral YouTube Shorts and Instagram Reels from podcasts.
You judge raw transcript excerpts for how well they would perform as a standalone 20-90 second vertical clip.
Be harsh: most excerpts are a 3-5. Reserve 8+ for moments that would clearly stop someone scrolling.
A great clip opens with a strong line (no "and", "so", "but", or pronouns that refer to earlier context), has one clear idea, builds, and ends on a payoff.
Score "standalone" and "payoff" low if the excerpt starts mid-answer or stops before the point is made: incomplete clips are what make viewers swipe."""


def score_prompt(lines: list[tuple[int, float, str]], title: str, niche: str) -> str:
    body = "\n".join(f"[{n}] ({t:.0f}s) {text}" for n, t, text in lines)
    first, last = lines[0][0], lines[-1][0]
    return (f"Episode: {title or 'untitled'}\nPage niche: {niche or 'general'}\n\n"
            f"Transcript excerpt (line number, seconds from excerpt start, text):\n{body}\n\n"
            f"Score this excerpt as a short clip. best_start and best_end must be line numbers between {first} and {last}; "
            f"tighten the clip if a different start makes a stronger hook or the ending drags, keeping it at least 20 seconds.")


class Hook(BaseModel):
    text: str = Field(description="on-screen hook, max 9 words, no hashtags")
    style: HookStyle = "bold_claim"

    @field_validator("style", mode="before")
    @classmethod
    def _style(cls, v):
        v = str(v or "").strip().lower().replace(" ", "_").replace("-", "_")
        return v if v in HOOK_STYLES else "bold_claim"


class Creative(BaseModel):
    hooks: list[Hook] = Field(description="4 different hooks, each a different style")
    title: str = Field(description="internal title for the clip, max 8 words")
    youtube_title: str = Field(description="YouTube Shorts title, max 80 characters, curiosity-driven, no clickbait lies")
    youtube_description: str = Field(description="2 short sentences describing the clip")
    tags: list[str] = Field(description="8-12 YouTube tags")
    instagram_caption: str = Field(description="Instagram caption: a hook line, one or two short lines, and a question to drive comments")
    hashtags: list[str] = Field(description="8-15 relevant hashtags without the # sign")
    emphasis: list[str] = Field(description="5-10 words from the transcript to highlight in captions")
    emoji: dict[str, str] = Field(default_factory=dict, description="up to 5 transcript words mapped to one fitting emoji")
    punchline_line: int = Field(description="line number of the single most striking line (for a cold open teaser)")


CREATIVE_SYSTEM = """You write the packaging for viral YouTube Shorts and Instagram Reels cut from podcasts and long videos.
Hooks are short, punchy, specific to what is actually said, and make the viewer need to hear the answer. Never invent facts or quotes that are not in the transcript.
Write natural, platform-native copy. Avoid generic phrases like "you won't believe" unless it truly fits."""


def creative_prompt(lines: list[tuple[int, float, str]], title: str, niche: str, language: str) -> str:
    body = "\n".join(f"[{n}] ({t:.0f}s) {text}" for n, t, text in lines)
    lang_note = {
        "hi": "Write hooks, titles and captions in natural Hinglish (Hindi written in Latin script, mixed with English) as Indian creators do.",
        "hinglish": "Write hooks, titles and captions in natural Hinglish (Hindi written in Latin script, mixed with English).",
        "hindi_devanagari": "Write hooks and titles in Hindi using Devanagari script (common English words like 'habit' may stay in English). Hashtags in English.",
    }.get(language, f"Write in the same language as the transcript (language code: {language or 'en'}).")
    return (f"Episode: {title or 'untitled'}\nPage niche: {niche or 'general'}\n{lang_note}\n\n"
            f"Clip transcript (line number, seconds from clip start, text):\n{body}\n\n"
            "Write the packaging for this clip. Hook styles: question, bold_claim, curiosity_gap, story, stat, "
            "controversy, warning, relatable. The emphasis words and emoji keys must be words that appear in the transcript.")


class ScoutPick(BaseModel):
    start_line: int = Field(description="first line of the clip; must open with a strong, self-contained line")
    end_line: int = Field(description="last line of the clip; must land a payoff, punchline or conclusion")
    why: str = Field(description="max 15 words: why this would stop a scroller")


class ScoutResult(BaseModel):
    picks: list[ScoutPick] = Field(description="the best 2-5 standalone clips in this part of the episode, best first")


SCOUT_SYSTEM = """You are the head editor of a clip channel that gets millions of views from podcasts.
Think in complete units: a question and its full answer, a story from setup to twist, a claim and its proof, a tip and its result.
You skim a section of an episode and pick the moments worth turning into Shorts/Reels: a bold claim, a surprising story, a counter-intuitive answer, a funny exchange, a strong emotional beat, or a concrete tip with a clear result.
Each pick must stand alone (no "as I said before"), open on a line that hooks instantly, and end on a payoff. 20-60 seconds is ideal. Skip intros, ads, small talk and housekeeping."""


def scout_prompt(lines: list[tuple[int, float, str]], title: str, niche: str) -> str:
    body = "\n".join(f"[{n}] ({t / 60:.0f}:{t % 60:02.0f}) {text}" for n, t, text in lines)
    return (f"Episode: {title or 'untitled'}\nNiche: {niche or 'general'}\n\nSection of the transcript "
            f"(line number, timestamp, text):\n{body}\n\nPick the best clips in this section.")


Structure = Literal["question_answer", "story_lesson", "claim_proof", "tip_result", "funny_exchange", "hot_take", "other"]


class Refined(BaseModel):
    start_line: int = Field(description="line the clip opens on: the hooking question or bold line (include the question even if the other person asked it)")
    end_line: int = Field(description="line where the answer/story is complete: the conclusion, punchline or lesson. Never stop mid-thought.")
    drop_lines: list[int] = Field(default_factory=list, description="lines INSIDE the clip to cut: tangents, interruptions, 'yeah'/'right' interjections, repeated points. Only if the clip still flows without them.")
    structure: Structure = "other"
    makes_sense_alone: bool = Field(description="would a stranger who never saw the episode understand it?")
    opening_hook: float = Field(default=5, description="0-10: how strongly the NEW first line grabs a scrolling viewer")
    complete: bool = Field(description="does it deliver the full payoff (the answer, the end of the story)?")
    note: str = Field(default="", description="max 12 words: what the clip is")

    @field_validator("structure", mode="before")
    @classmethod
    def _st(cls, v):
        v = str(v or "").strip().lower().replace(" ", "_").replace("-", "_").replace("→", "_")
        return v if v in Structure.__args__ else "other"


REFINE_SYSTEM = """You are the senior editor who signs off every clip before it is posted.
A clip works when it has a complete arc: it OPENS on a hook (a sharp question, a bold claim, the start of a story) and it ENDS when that hook is paid off (the full answer, the twist, the lesson, the punchline).
Common fixes you make:
- If the clip starts in the middle of an answer, move the start back to the question that was asked (by either person).
- If it stops before the point is made, move the end forward to where the thought concludes.
- If the middle wanders (tangent, interruption, filler agreement like "yeah, right"), list those lines to drop so the question flows straight into the answer.
- Never start on a line that refers to something not in the clip ("that", "he said", "as I mentioned").
Be honest: if it still doesn't make sense alone or never pays off, say so."""


def refine_prompt(lines: list[tuple[int, float, str]], start: int, end: int, title: str) -> str:
    body = "\n".join(f"{'>>' if start <= n <= end else '  '} [{n}] ({t:.0f}s) {text}" for n, t, text in lines)
    return (f"Episode: {title or 'untitled'}\nThe proposed clip is lines {start}-{end} (marked >>). Surrounding lines are context you may extend into.\n\n"
            f"{body}\n\nFix the clip boundaries and say whether it works. Aim for 25-60 seconds after drops (75 max): one complete idea, not a whole segment.")
