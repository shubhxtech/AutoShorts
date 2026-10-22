"""Motion-graphic inserts for the explainer layout (graphic on top, speaker below)
and the "Comment KEYWORD" call-to-action card."""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from . import text as T
from .plan import CardEvent

W, H = 1080, 1920
PANEL_H = 880            # graphic area height; the speaker fills the rest

PAPER = (238, 241, 243)  # BGR: warm off-white
GRID = (222, 224, 226)
INK = (20, 20, 20, 255)
ACCENT = (232, 84, 43, 255)  # warm orange-red (RGBA)


def _back_out(x: float, s: float = 1.7) -> float:
    x = max(0.0, min(1.0, x)) - 1
    return x * x * ((s + 1) * x + s) + 1


@lru_cache(maxsize=1)
def paper() -> np.ndarray:
    img = np.full((PANEL_H, W, 3), PAPER, np.uint8)
    for x in range(0, W, 54):
        img[:, x:x + 1] = GRID
    for y in range(0, PANEL_H, 54):
        img[y:y + 1, :] = GRID
    return img


def _shadowed(sprite: np.ndarray, blur: int = 18, alpha: float = 0.28) -> np.ndarray:
    """Add a soft drop shadow under a premultiplied RGBA sprite."""
    h, w = sprite.shape[:2]
    pad = blur * 2
    out = np.zeros((h + pad * 2, w + pad * 2, 4), np.float32)
    a = sprite[..., 3].astype(np.float32)
    sh = np.zeros_like(out[..., 3])
    sh[pad + blur // 2:pad + blur // 2 + h, pad:pad + w] = a * alpha
    sh = cv2.GaussianBlur(sh, (0, 0), blur / 2)
    out[..., 3] = sh
    s = sprite.astype(np.float32)
    region = out[pad:pad + h, pad:pad + w]
    k = s[..., 3:4] / 255.0
    region[..., :3] = s[..., :3] + region[..., :3] * (1 - k)
    region[..., 3] = s[..., 3] + region[..., 3] * (1 - k[..., 0])
    return np.clip(out, 0, 255).astype(np.uint8)


@lru_cache(maxsize=32)
def card_sprite(kind: str, text: str, label: str, items: tuple, emoji: str, n_items: int) -> np.ndarray:
    heavy = T.resolve_font("AvenirNext-Heavy", "Arial-Black")
    cond = T.resolve_font("Futura-CondensedExtraBold", heavy)
    if kind == "stat":
        big = T.render([[T.Span(text, ACCENT)]], T.TextStyle(font=cond, size=230, line_spacing=0.9))
        lab = T.render([[T.Span(label or "", INK)]], T.TextStyle(font=heavy, size=56)) if label else None
        parts = [big] + ([lab] if lab is not None else [])
        w = max(p.shape[1] for p in parts)
        h = sum(p.shape[0] for p in parts)
        out = np.zeros((h, w, 4), np.uint8)
        y = 0
        for p in parts:
            x = (w - p.shape[1]) // 2
            out[y:y + p.shape[0], x:x + p.shape[1]] = p
            y += p.shape[0] - 10
        return out
    if kind == "list":
        rows = [[T.Span(f"{emoji} " if emoji else "", INK), T.Span(text, INK)]] if text else []
        for k, it in enumerate(items):
            color = INK if k < n_items else (0, 0, 0, 0)
            check = (46, 160, 67, 255) if k < n_items else (0, 0, 0, 0)
            rows.append([T.Span("✓ ", check, 0.9), T.Span(it, color, 0.85)])
        st = T.TextStyle(font=heavy, size=58, background=(255, 255, 255, 255), background_pad=(48, 34),
                         background_radius=30, line_spacing=1.25)
        return _shadowed(T.render(rows, st))
    if kind == "question":
        st = T.TextStyle(font=heavy, size=62, background=(255, 255, 255, 255), background_pad=(60, 44),
                         background_radius=90)
        words = text.split()
        lines = [" ".join(words[i:i + 4]) for i in range(0, len(words), 4)][:3]
        spr = T.render([[T.Span(l, INK)] for l in lines], st)
        # Thought-bubble dots.
        h, w = spr.shape[:2]
        out = np.zeros((h + 90, w, 4), np.uint8)
        out[:h] = spr
        for (cx, cy, r) in ((w * 0.72, h + 22, 20), (w * 0.8, h + 64, 12)):
            cv2.circle(out, (int(cx), int(cy)), r, (255, 255, 255, 255), -1, lineType=cv2.LINE_AA)
        return _shadowed(out)
    # title: dark card, white headline, accent second line
    words = text.split()
    half = max(1, (len(words) + 1) // 2) if len(words) > 3 else len(words)
    rows = [[T.Span(" ".join(words[:half]).upper(), (255, 255, 255, 255))]]
    if words[half:]:
        rows.append([T.Span(" ".join(words[half:]).upper(), ACCENT)])
    if emoji:
        rows.insert(0, [T.Span(emoji, (255, 255, 255, 255), 0.9)])
    st = T.TextStyle(font=heavy, size=74, background=(20, 20, 19, 255), background_pad=(56, 40),
                     background_radius=26, line_spacing=1.05)
    spr = T.render(rows, st)
    if label:
        lab = T.render([[T.Span(label, INK)]], T.TextStyle(font=T.resolve_font("AvenirNext-DemiBold", heavy), size=40))
        out = np.zeros((spr.shape[0] + lab.shape[0] + 10, max(spr.shape[1], lab.shape[1]), 4), np.uint8)
        out[:spr.shape[0], (out.shape[1] - spr.shape[1]) // 2:(out.shape[1] - spr.shape[1]) // 2 + spr.shape[1]] = spr
        y = spr.shape[0] + 10
        out[y:y + lab.shape[0], (out.shape[1] - lab.shape[1]) // 2:(out.shape[1] - lab.shape[1]) // 2 + lab.shape[1]] = lab
        spr = out
    return _shadowed(spr)


def draw_panel(frame: np.ndarray, card: CardEvent, t: float) -> None:
    """Paint the graphic panel in the top part of an explainer frame."""
    frame[:PANEL_H] = paper()
    age = t - card.start
    n_items = len(card.items)
    if card.kind == "list" and card.items:
        n_items = min(len(card.items), 1 + int(max(0.0, age - 0.25) / 0.45))
    spr = card_sprite(card.kind, card.text, card.label, tuple(card.items), card.emoji, n_items)
    s = 0.55 + 0.45 * _back_out(age / 0.28)
    max_w, max_h = W - 120, PANEL_H - 220
    fit = min(1.0, max_w / spr.shape[1], max_h / spr.shape[0])
    spr = T.scale_sprite(spr, s * fit)
    h, w = spr.shape[:2]
    T.blend(frame, spr, (W - w) // 2, int(PANEL_H * 0.56 - h / 2))
    frame[PANEL_H - 3:PANEL_H] = (30, 30, 30)


@lru_cache(maxsize=8)
def cta_sprite(keyword: str) -> np.ndarray:
    heavy = T.resolve_font("AvenirNext-Heavy", "Arial-Black")
    top = T.render([[T.Span("💬 COMMENT", (255, 255, 255, 255))]], T.TextStyle(font=heavy, size=78))
    pill = T.render([[T.Span(keyword.upper(), (255, 255, 255, 255))]],
                    T.TextStyle(font=heavy, size=84, background=(232, 52, 43, 255),
                                background_pad=(34, 12), background_radius=18))
    w = max(top.shape[1], pill.shape[1]) + 80
    h = top.shape[0] + pill.shape[0] + 70
    card = np.zeros((h, w, 4), np.uint8)
    cv2.rectangle(card, (0, 0), (w - 1, h - 1), (18, 18, 18, 245), -1)
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (0, 0), (w - 1, h - 1), 255, -1)
    card[..., 3] = np.where(mask > 0, 245, 0).astype(np.uint8)
    card[..., :3] = (card[..., :3].astype(np.float32) * card[..., 3:4] / 255).astype(np.uint8)
    for spr, y in ((top, 30), (pill, 30 + top.shape[0])):
        x = (w - spr.shape[1]) // 2
        region = card[y:y + spr.shape[0], x:x + spr.shape[1]].astype(np.float32)
        k = spr[..., 3:4].astype(np.float32) / 255
        region[..., :3] = spr[..., :3] + region[..., :3] * (1 - k)
        region[..., 3] = np.maximum(region[..., 3], spr[..., 3])
        card[y:y + spr.shape[0], x:x + spr.shape[1]] = region.astype(np.uint8)
    return _shadowed(card, blur=22, alpha=0.4)


def draw_cta(frame: np.ndarray, keyword: str, age: float, y_center: float = 0.62) -> None:
    spr = cta_sprite(keyword)
    wobble = 1.0 + 0.025 * np.sin(age * 9) if age > 0.35 else 0.5 + 0.5 * _back_out(age / 0.35)
    spr = T.scale_sprite(spr, wobble)
    h, w = spr.shape[:2]
    T.blend(frame, spr, (W - w) // 2, int(H * y_center - h / 2))
