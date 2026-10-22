from datetime import timedelta

from conftest import make_words

from autoshorts import llm, rights
from autoshorts.community.tasks import _funnel_match, heuristic
from autoshorts.ingest import viral
from autoshorts.models import Source, utcnow
from autoshorts.publish import hosting
from autoshorts.render import edl as E
from autoshorts.render import plan as P
from autoshorts.render.captions import STYLES, chunk_words
from autoshorts.understand.transcribe import build_sentences


TEXT = ("Nobody tells you this. um The first million is the hardest money you will ever make. "
        "I I was broke at twenty seven. And I made one decision that changed everything.")


def words():
    return make_words(TEXT, pauses={3: 1.2, 12: 0.8})


def test_edl_cuts_never_inside_words_and_removes_fillers():
    ws = words()
    edl = E.build(ws, ws[0]["s"], ws[-1]["e"], max_pause=0.45)
    for seg in edl.segments:
        for t in (seg.src_start, seg.src_end):
            assert not any(w["s"] + 0.01 < t < w["e"] - 0.01 for w in ws), t
    texts = [w.text for w in edl.words]
    assert "um" not in texts
    assert 16 not in [w.index for w in edl.words] and 17 in [w.index for w in edl.words]  # "I I" -> "I"
    assert edl.duration < ws[-1]["e"] - ws[0]["s"] - 1.5   # long pauses tightened
    # output word times are monotonic and inside the clip
    starts = [w.start for w in edl.words]
    assert starts == sorted(starts) and starts[-1] < edl.duration


def test_edl_padding_does_not_eat_neighbouring_words():
    ws = words()
    i0, i1 = 5, 15
    edl = E.build(ws, ws[i0]["s"], ws[i1]["e"])
    assert edl.segments[0].src_start >= ws[i0 - 1]["e"]
    assert edl.segments[-1].src_end <= ws[i1 + 1]["s"]


def test_cold_open_goes_first_and_is_short():
    ws = words()
    edl = E.build(ws, ws[0]["s"], ws[-1]["e"], cold_open=(ws[22]["s"], ws[-1]["e"]))
    assert edl.segments[0].kind == "cold_open"
    assert 1.0 <= edl.segments[0].duration <= 3.6


def test_caption_chunks_respect_limits_and_dont_end_on_weak_words():
    ws = words()
    edl = E.build(ws, ws[0]["s"], ws[-1]["e"])
    st = STYLES["clean"]
    chunks = chunk_words(edl.words, st)
    assert chunks
    for ch in chunks:
        assert len(ch.words) <= st.max_words * st.lines
        assert ch.end >= ch.start
    ends = [edl.words[ch.words[-1]].text.lower().strip(",.") for ch in chunks[:-1]]
    assert "the" not in ends


def test_schedule_framing_changes_land_on_boundaries_and_cover_gaps():
    ws = make_words(" ".join(["word"] * 30 + ["end."] + ["more"] * 30 + ["done."]))
    edl = E.build(ws, ws[0]["s"], ws[-1]["e"])
    tl = P.schedule(edl, None, emphasis_words=["word"], emoji_map={}, hook_seconds=2.5, preset="clean")
    times = [t for t, _ in tl.framings]
    assert times[0] == 0.0
    gaps = [b - a for a, b in zip(times, times[1:] + [edl.duration])]
    assert max(gaps) < 9.0
    # punches respect the preset cap
    assert len(tl.punches) <= P.PRESETS["clean"]["punches"]
    assert tl.zoom_at(0.0) >= 1.0


def test_rights_gate():
    s = Source(name="x", rights_type="licensed", proof_url="", proof_notes="")
    assert not rights.check_source(s).ok                       # no proof
    s.proof_url = "https://example.com/licence"
    assert rights.check_source(s, "youtube").ok
    s.allowed_platforms = ["youtube"]
    assert not rights.check_source(s, "instagram").ok          # platform not covered
    s.expires_at = utcnow() - timedelta(days=1)
    assert not rights.check_source(s).ok                       # expired
    assert not rights.check_source(None).ok


def test_viral_score_ranks_outliers():
    now = utcnow()
    cands = [viral.Candidate(str(i), "", "", v, now - timedelta(days=10), 1800) for i, v in enumerate([1000, 1200, 900, 15000])]
    viral.score(cands)
    best = max(cands, key=lambda c: c.viral_score)
    assert best.views == 15000 and best.viral_score > 5


def test_extract_json_handles_fences_and_trailing_commas():
    assert llm.extract_json('Sure!\n```json\n{"a": [1, 2,], "b": "x}y"}\n```') == {"a": [1, 2], "b": "x}y"}
    assert llm.extract_json('<think>hmm</think> {"ok": true}') == {"ok": True}


def test_comment_triage_and_dm_funnel():
    assert heuristic("check my page for free followers")[0] == "spam"
    assert heuristic("where can I watch the full episode?")[0] == "wants_full_episode"
    f = [{"keyword": "LINK", "message": "https://x"}]
    assert _funnel_match("link please 🙏", f) == f[0]
    assert _funnel_match("unlinked", f) is None


def test_signed_media_links():
    tok = hosting.sign(42, ttl_s=60)
    assert hosting.verify(tok) == 42
    assert hosting.verify(tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None


def test_sentence_splitting():
    sents = build_sentences(make_words("Hello there. How are you? Fine"))
    assert [s["text"] for s in sents] == ["Hello there.", "How are you?", "Fine"]


def test_script_formats():
    from autoshorts.faceless import SCRIPT_TEMPLATE, parse_script

    t = parse_script(SCRIPT_TEMPLATE)
    assert t["title"] == "The Stone Age Surgeon" and t["hook"].startswith("They cut")
    assert len(t["scenes"]) == 4 and t["scenes"][0]["visual"].startswith("a prehistoric")
    assert t["scenes"][3]["visual"] == ""
    p = parse_script("First line here.\n\nSecond scene line.")
    assert [s["narration"] for s in p["scenes"]] == ["First line here.", "Second scene line."]
    j = parse_script('{"title":"T","scenes":[{"narration":"a","visual":"b"}]}')
    assert j["scenes"][0] == {"narration": "a", "visual": "b"}


def test_edl_excludes_dropped_lines():
    ws = make_words("What is the secret? Well um let me tell you about my dog. The secret is consistency every day.")
    # drop the tangent "Well um let me tell you about my dog."
    tangent = (ws[4]["s"] - 0.01, ws[12]["e"] + 0.01)
    edl = E.build(ws, ws[0]["s"], ws[-1]["e"], exclude=[tangent])
    texts = " ".join(w.text for w in edl.words)
    assert "dog" not in texts and texts.startswith("What is the secret?") and texts.endswith("day.")
    assert len(edl.segments) >= 2


def test_snap_to_sentences_never_starts_on_a_fragment():
    from autoshorts.mining.miner import snap_to_sentences

    sents = [{"text": "I get up early", "s": 0, "e": 2}, {"text": "on a morning when it's cold.", "s": 2.5, "e": 5},
             {"text": "That's how you level up", "s": 6, "e": 8}, {"text": "every single day.", "s": 8.2, "e": 10}]
    assert snap_to_sentences(sents, 1, 2) == (0, 3)


def test_user_script_with_markdown_and_loose_visuals():
    from autoshorts.faceless import parse_script, speakable

    t = parse_script("TITLE: T\n\nHOOK: They cut off his leg.\nHe didn't die.\n\nFirst line.\n\n[visual: one]\n\n"
                     "But somehow...\n\nthe child survived.\n\n[visual: two]\n\nIt lasted **6–9 years**.\n")
    assert t["hook"] == "They cut off his leg. He didn't die."
    assert [s["visual"] for s in t["scenes"]] == ["one", "two", ""]
    assert t["scenes"][1]["narration"] == "But somehow... the child survived."
    assert "**" not in t["scenes"][2]["narration"]
    assert speakable("**6–9 years** → done") == "6 to 9 years to done"


def test_hindi_words_and_fillers():
    from autoshorts.render.edl import LEADING_FILLER, _norm

    assert _norm("तो,") == "तो" and _norm("तो") in LEADING_FILLER
    ws = make_words("तो सफलता का असली राज़ है रोज़ थोड़ा बेहतर होना और यही आदत सब बदल देती है।")
    edl = E.build(ws, ws[0]["s"], ws[-1]["e"])
    assert edl.words[0].text == "सफलता"


def test_overlapping_word_times_never_cut_inside_first_word():
    ws = make_words("so the habit that changes everything is perseverance over many years of work")
    ws[0]["e"] = ws[1]["s"] + 0.1          # overlapping timestamps, as Whisper sometimes gives
    edl = E.build(ws, ws[0]["s"], ws[-1]["e"])
    assert edl.segments[0].src_start <= ws[1]["s"]


def test_snap_hindi_fragment():
    from autoshorts.mining.miner import snap_to_sentences

    sents = [{"text": "धोनी", "s": 0, "e": 1}, {"text": "ने कहा कि यह हेलिकॉप्टर शॉट है।", "s": 1.2, "e": 4},
             {"text": "सबने देखा।", "s": 4.5, "e": 6}]
    assert snap_to_sentences(sents, 1, 2)[0] == 0


def test_faceless_tidy_repairs_formats():
    from autoshorts.faceless import Scene, Script, tidy

    def mk(fmt, scenes):
        return Script(title="t", hook="h", scenes=scenes, youtube_title="y", instagram_caption="c", hashtags=[], format=fmt)

    s = tidy(mk("countdown", [Scene(narration="intro", visual="v", kind="intro"),
                              Scene(narration="a", visual="v", kind="item", number=1),
                              Scene(narration="b", visual="v", kind="item", number=1)]))
    assert [x.number for x in s.scenes if x.kind == "item"] == [2, 1]
    q = tidy(mk("quiz", [Scene(narration="q", visual="v", kind="question", options=["a", "b", "c", "d"], answer=7),
                         Scene(narration="bad", visual="v", kind="question", options=["only"]),
                         Scene(narration="ans", visual="v", kind="answer")]))
    assert q.scenes[0].options == ["a", "b", "c"] and q.scenes[0].answer == 0
    assert q.scenes[1].kind == "beat"
    w = tidy(mk("would_you_rather", [Scene(narration="x", visual="", kind="choice", options=["fly", "swim"])]))
    assert w.scenes[0].visual2 == "swim"
    t = tidy(mk("text_story", [Scene(narration="x", visual="v")]))
    assert "Fictional" in t.instagram_caption


def test_scene_coerces_nulls():
    from autoshorts.faceless import Scene

    sc = Scene.model_validate({"narration": "n", "visual": "v", "options": None, "visual2": None, "number": "#4"})
    assert sc.options == [] and sc.visual2 == "" and sc.number == 4


def test_plan_shots_cuts_long_scenes():
    from autoshorts.faceless import Scene
    from autoshorts.faceless_formats import SceneTiming, plan_shots

    scenes = [Scene(narration="a", visual="v"), Scene(narration="b", visual="v"), Scene(narration="c", visual="v", kind="choice", options=["x", "y"])]
    tms = [SceneTiming(0, 1.8, 2.0), SceneTiming(2.0, 7.5, 7.6), SceneTiming(7.6, 10, 12)]
    shots = plan_shots("story", scenes, tms, [(0, None), (1, 2), (3, 4)])
    assert len([s for s in shots if s.start < 2.0]) == 1
    mid = [s for s in shots if 2.0 <= s.start < 7.6]
    assert len(mid) == 3 and {s.image for s in mid} == {1, 2}
    assert len({s.move for s in mid}) == 3                       # every cut gets a different camera move
    assert {s.half for s in shots if s.start >= 7.6} == {0, 1}   # would-you-rather split screen


def test_generated_music_is_loopable_and_quiet():
    from autoshorts.render.music import SR, render

    y = render("chill", bars=4, seed=1)
    assert y.shape[1] == 2 and y.shape[0] > SR * 8
    assert abs(y).max() <= 0.86 and abs(y[:100]).max() < 0.05     # fades in, never clips


def test_hook_size_matches_preview_rule():
    from autoshorts.render.overlays import hook_size

    assert hook_size("Can I just be myself?") == 84
    assert hook_size("x" * 50) == 66 and hook_size("x" * 80) == 58


def test_quiz_verification_drops_unsupported_questions():
    from autoshorts.faceless import Scene, Script, tidy, verify_quiz

    facts = ("Mercury is the smallest planet in the Solar System and has almost no atmosphere. "
             "Jupiter is the largest planet and was first visited by Pioneer 10 in 1973.")
    sc = [Scene(narration="q1", visual="", kind="question", heading="Question 1", options=["Mars", "Mercury", "Venus"], answer=1,
                evidence="Mercury is the smallest planet in the Solar System and has almost no atmosphere."),
          Scene(narration="It's Mercury", visual="", kind="answer"),
          Scene(narration="When were outer planets first explored?", visual="", kind="question", options=["1960s", "1973"], answer=0,
                evidence="Humans explored the outer planets in the 1960s with many probes."),
          Scene(narration="1960s", visual="", kind="answer"),
          Scene(narration="Which planet is largest?", visual="", kind="question", options=["Jupiter", "Saturn"], answer=0,
                evidence="Jupiter is the largest planet and was first visited by Pioneer 10 in 1973."),
          Scene(narration="Jupiter", visual="", kind="answer")]
    s = tidy(Script(title="t", hook="h", scenes=sc, youtube_title="", instagram_caption="", hashtags=[], format="quiz"))
    assert s.scenes[0].heading == "q1"                        # "Question 1" replaced by the real question
    s = verify_quiz(s, facts)
    kinds = [x.kind for x in s.scenes]
    assert kinds == ["question", "answer", "question", "answer"]
    assert all("1960s" not in x.narration for x in s.scenes)


def test_skia_text_renders_devanagari_and_boxes(monkeypatch):
    import pytest

    from autoshorts.render import text as T
    from autoshorts.render import text_skia

    if not text_skia.available():
        pytest.skip("skia-python not installed")
    monkeypatch.setenv("AUTOSHORTS_TEXT", "skia")
    monkeypatch.setattr(text_skia, "ensure_fonts", lambda: [])        # system fonts only: no network in tests
    text_skia._collection.cache_clear()
    st = T.TextStyle(font=T.resolve_font("AvenirNext-Heavy", "Arial-Black"), size=60, stroke=4,
                     background=(255, 255, 255, 255))
    spr = T.render([[T.Span("IQ ", (0, 0, 0, 255), 1.0, (255, 214, 10, 255)), T.Span("से बेहतर", (0, 0, 0, 255))]], st)
    assert spr.ndim == 3 and spr.shape[2] == 4 and spr.shape[1] > 200
    assert (spr[..., 3] > 0).mean() > 0.3                      # the background box is drawn
    assert T.measure("से बेहतर", st) > T.measure("से", st) > 0  # shaped width grows with text
    text_skia._collection.cache_clear()


def test_encoder_falls_back_to_software(monkeypatch):
    from autoshorts import media

    monkeypatch.setattr(media, "encoders", lambda: {"libx264", "h264_nvenc"})
    monkeypatch.setattr(media, "encoder_works", lambda name: False)   # NVENC listed but no GPU
    args = media.video_encoder_args("auto")
    assert args[:2] == ["-c:v", "libx264"]
    monkeypatch.setattr(media, "encoder_works", lambda name: name == "h264_nvenc")
    assert media.video_encoder_args("auto")[:2] == ["-c:v", "h264_nvenc"]


def test_llm_server_backend(monkeypatch):
    from autoshorts import llm, settings
    from autoshorts.titles import TitleOptions

    vals = {"llm_backend": "server", "llm_enabled": True, "llm_server_url": "http://x/v1", "llm_server_model": "m"}
    monkeypatch.setattr(settings, "get", lambda k, d=None: vals.get(k, d))
    monkeypatch.setattr(llm, "_server_up", lambda: True)
    seen = {}

    def fake(system, user, max_tokens, temperature, json_mode):
        seen["json"] = json_mode
        return '<think>hmm</think>{"titles": ["One", "Two"]}'

    monkeypatch.setattr(llm, "_server_complete", fake)
    assert llm.backend() == "server"
    assert llm.structured(TitleOptions, "s", "u").titles == ["One", "Two"] and seen["json"] is True

