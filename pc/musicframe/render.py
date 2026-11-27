"""Draws the whole 240x240 music screen (or dirty lyric region) as a JPEG, so the ESP32 needs no fonts or layout."""
import io
import math
import os
import time
import zlib
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from .lyrics.motion import (
    KARAOKE_FADE_S,
    SheetAnimator,
    SheetItem,
    TimedUnit,
    build_sheet_items,
    clamp,
    ease_out_cubic,
    ease_out_expo,
    mix_color,
    monotone,
    own_sung_end,
    pace_limit,
    timed_units,
    word_glow,
    word_rise,
)
from .model import Line, Lyrics, NowPlaying

W = H = 240
PAD = 14
WHITE = (255, 255, 255)
GREY = (170, 176, 196)
DIM = (110, 116, 136)

# Sonora translucent-light constants (crates/views/src/shared/mod.rs & aside.rs)
QUIET_LINEAR = 0.56
PAST_LINEAR = QUIET_LINEAR * 0.40
AHEAD_LINEAR = QUIET_LINEAR * 0.60
VEIL = 0.30

# 8px-aligned lyric viewport between header (y=14..66, credit y=44..56) and progress bar (y=218..232)
LYRICS_Y = 76
LYRICS_H = 136
AREA_TOP = 76
AREA_BOT = 208
AREA_H = AREA_BOT - AREA_TOP
PIN = 0.30
ANCHOR_Y = AREA_H * PIN  # active line top inside the lyric viewport
EDGE_FADE_PX = 14
CONTEXT_SCALE = 0.88
VERSE_GAP = 6
BREAK_H = 20
BASE_SIZE = 16
HAZE_EXP = 0.45
BLUR_RATIO = 0.13
HAZE_STEP = 0.25
HAZE_LEAST = 0.30

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONTS = {  # script -> (regular, bold)
    "latin": ("segoeui.ttf", "segoeuib.ttf"),
    "ja": ("YuGothM.ttc", "YuGothB.ttc"),
    "zh": ("msyh.ttc", "msyhbd.ttc"),
    "ko": ("malgun.ttf", "malgunbd.ttf"),
    "sym": ("seguisym.ttf", "seguisym.ttf"),  # ♪ and other symbols Segoe UI lacks
}


def _script(ch: str, has_kana: bool) -> str:
    o = ord(ch)
    if 0x2190 <= o <= 0x2BFF:
        return "sym"
    if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
        return "ko"
    if 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF:
        return "ja"
    if 0x3400 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
        return "ja" if has_kana else "zh"
    return "latin"


@lru_cache(maxsize=64)
def _font(script: str, size: int, bold: bool) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(FONT_DIR, FONTS[script][bold]), size)


def pick_font(text: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Font for the dominant non-Latin script in text (Latin otherwise)."""
    kana = any(0x3040 <= ord(c) <= 0x30FF for c in text)
    for c in text:
        s = _script(c, kana)
        if s not in ("latin", "sym"):
            return _font(s, size, bold)
    return _font("latin", size, bold)


def _runs(text: str, size: int, bold: bool):
    """Split text into same-script runs, each with its font, so mixed Vietnamese/Korean/Japanese all render."""
    kana = any(0x3040 <= ord(c) <= 0x30FF for c in text)
    runs: list[tuple[str, ImageFont.FreeTypeFont]] = []
    cur, cur_s = "", None
    for c in text:
        s = _script(c, kana) if not c.isspace() else (cur_s or "latin")
        if s != cur_s and cur:
            runs.append((cur, _font(cur_s, size, bold)))
            cur = ""
        cur, cur_s = cur + c, s
    if cur:
        runs.append((cur, _font(cur_s, size, bold)))
    return runs


def text_width(text: str, size: int, bold: bool = False) -> float:
    return sum(f.getlength(t) for t, f in _runs(text, size, bold))


def draw_text(d: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int, bold=False, fill=WHITE):
    base = y + size  # shared baseline so mixed fonts line up
    for t, f in _runs(text, size, bold):
        d.text((x, base), t, font=f, fill=fill, anchor="ls")
        x += f.getlength(t)


def fit(text: str, size: int, max_w: float, bold: bool = False) -> str:
    """Cut with an ellipsis so text fits max_w."""
    if text_width(text, size, bold) <= max_w:
        return text
    while text and text_width(text + "…", size, bold) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(text: str, size: int, max_w: float, bold: bool = False, max_lines: int = 2) -> list[str]:
    """Greedy wrap on spaces, or per character for CJK; the last line gets an ellipsis if it overflows."""
    tokens = text.split(" ") if " " in text else list(text)
    sep = " " if " " in text else ""
    lines, cur = [], ""
    for tok in tokens:
        cand = cur + sep + tok if cur else tok
        if text_width(cand, size, bold) <= max_w or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = tok
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [fit(sep.join(lines[max_lines - 1:]), size, max_w, bold)]
    return [fit(l, size, max_w, bold) for l in lines]


def _wrap_with_ranges(text: str, size: int, max_w: float, bold: bool = True) -> list[tuple[str, int, int]]:
    """Wrap text and keep exact [c0, c1) character offsets inside `text` for each row."""
    if not text:
        return [("", 0, 0)]
    if " " not in text:
        rows: list[tuple[str, int, int]] = []
        cur_start = 0
        for i, ch in enumerate(text):
            cand = text[cur_start:i + 1]
            if text_width(cand, size, bold) <= max_w or i == cur_start:
                continue
            rows.append((text[cur_start:i], cur_start, i))
            cur_start = i
        rows.append((text[cur_start:], cur_start, len(text)))
        return rows

    # Split into word tokens keeping exact character offsets
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == " ":
            i += 1
            continue
        j = i
        while j < n and text[j] != " ":
            j += 1
        spans.append((i, j))
        i = j
    if not spans:
        return [(text, 0, len(text))]

    rows = []
    row_c0, row_c1 = spans[0]
    for s0, s1 in spans[1:]:
        cand = text[row_c0:s1]
        if text_width(cand, size, bold) <= max_w:
            row_c1 = s1
        else:
            rows.append((text[row_c0:row_c1], row_c0, row_c1))
            row_c0, row_c1 = s0, s1
    rows.append((text[row_c0:row_c1], row_c0, row_c1))
    return rows


@lru_cache(maxsize=8)
def _cover(data: bytes | None, size: int) -> Image.Image | None:
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except OSError:
        return None
    # video thumbnails come 16:9, often with black bars: drop the bars, then crop the middle square (never squash)
    box = img.convert("L").point(lambda v: 255 if v > 24 else 0).getbbox()
    if box and (box[2] - box[0]) * (box[3] - box[1]) > 0.3 * img.width * img.height:
        img = img.crop(box)
    return ImageOps.fit(img, (size, size), Image.LANCZOS)


@lru_cache(maxsize=8)
def _background(data: bytes | None) -> Image.Image:
    c = _cover(data, W)
    if c is None:
        return Image.new("RGB", (W, H), (14, 18, 34))
    bg = c.filter(ImageFilter.GaussianBlur(18))
    return Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.62)


@lru_cache(maxsize=4)
def _mask(size: int, r: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], r, fill=255)
    return mask


@lru_cache(maxsize=4)
def _edge_fade_mask(w: int, h: int, fade: int) -> Image.Image:
    """Vertical alpha gradient fading top and bottom edges cleanly into the backdrop."""
    col = bytearray(h)
    for y in range(h):
        if y < fade:
            t = y / max(fade, 1)
            col[y] = round(255 * t * t * (3 - 2 * t))
        elif y >= h - fade:
            t = (h - 1 - y) / max(fade, 1)
            col[y] = round(255 * t * t * (3 - 2 * t))
        else:
            col[y] = 255
    return Image.frombytes("L", (1, h), bytes(col)).resize((w, h), Image.Resampling.NEAREST)


def clock(ms: int) -> str:
    s = max(ms, 0) // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def progress_visual(np: NowPlaying, pos: int) -> tuple[str, int]:
    """Observable progress bar state (left clock string, filled pixel x) for dirty-rect checks."""
    left = clock(min(pos, np.duration_ms) if np.duration_ms else pos)
    if np.duration_ms <= 0:
        return left, -1
    size = 12
    right = clock(np.duration_ms)
    x0 = PAD + max(text_width(left, size), text_width("0:00", size)) + 8
    x1 = W - PAD - max(text_width(right, size), text_width("0:00", size)) - 8
    x = round(x0 + (x1 - x0) * min(max(pos, 0) / np.duration_ms, 1.0))
    return left, x


def _progress(d: ImageDraw.ImageDraw, np: NowPlaying, pos: int):
    """'1:23 ━━━━━━──── 4:22' along the bottom edge."""
    size, y = 12, H - 22
    left, right = clock(min(pos, np.duration_ms) if np.duration_ms else pos), clock(np.duration_ms) if np.duration_ms else ""
    draw_text(d, PAD, y, left, size, fill=GREY)
    draw_text(d, W - PAD - text_width(right, size), y, right, size, fill=GREY)
    x0 = PAD + max(text_width(left, size), text_width("0:00", size)) + 8
    x1 = W - PAD - max(text_width(right, size), text_width("0:00", size)) - 8
    by = y + 7
    d.rounded_rectangle([x0, by, x1, by + 4], 2, fill=(60, 64, 80))
    if np.duration_ms > 0:
        x = x0 + (x1 - x0) * min(max(pos, 0) / np.duration_ms, 1)
        d.rounded_rectangle([x0, by, max(x, x0 + 4), by + 4], 2, fill=WHITE)


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def render_basic_image(np: NowPlaying, position_ms: int) -> Image.Image:
    """No lyrics: big cover, title, artist, progress (RGB Image)."""
    img = _background(np.cover).copy()
    d = ImageDraw.Draw(img)
    size = 138
    cover = _cover(np.cover, size)
    x0 = (W - size) // 2
    if cover:
        img.paste(cover, (x0, 14), _mask(size, 12))
    else:
        d.rounded_rectangle([x0, 14, x0 + size, 14 + size], 12, fill=(32, 38, 60))
        draw_text(d, W / 2 - text_width("♪", 54) / 2, 50, "♪", 54, fill=DIM)
    t = fit(np.title, 18, W - 2 * PAD, True)
    draw_text(d, (W - text_width(t, 18, True)) / 2, 160, t, 18, True)
    a = fit(np.artist, 14, W - 2 * PAD)
    draw_text(d, (W - text_width(a, 14)) / 2, 188, a, 14, fill=GREY)
    _progress(d, np, position_ms)
    return img


def render_basic(np: NowPlaying, position_ms: int) -> bytes:
    """No lyrics: big cover, title, artist, progress."""
    return _jpeg(render_basic_image(np, position_ms))


def _header(img: Image.Image, d: ImageDraw.ImageDraw, np: NowPlaying):
    cover = _cover(np.cover, 52)
    if cover:
        img.paste(cover, (PAD, PAD), _mask(52, 8))
    else:
        d.rounded_rectangle([PAD, PAD, PAD + 52, PAD + 52], 8, fill=(32, 38, 60))
    x = PAD + 62
    draw_text(d, x, PAD + 4, fit(np.title, 16, W - x - PAD, True), 16, True)
    draw_text(d, x, PAD + 28, fit(np.artist, 13, W - x - PAD), 13, fill=GREY)


@lru_cache(maxsize=16)
def _base_screen(cover: bytes | None, title: str, artist: str, source: str) -> Image.Image:
    """Cached background + header + optional credit so animated frames avoid redrawing static pixels."""
    img = _background(cover).copy()
    d = ImageDraw.Draw(img)
    cover_img = _cover(cover, 52)
    if cover_img:
        img.paste(cover_img, (PAD, PAD), _mask(52, 8))
    else:
        d.rounded_rectangle([PAD, PAD, PAD + 52, PAD + 52], 8, fill=(32, 38, 60))
    x = PAD + 62
    draw_text(d, x, PAD + 4, fit(title, 16, W - x - PAD, True), 16, True)
    draw_text(d, x, PAD + 28, fit(artist, 13, W - x - PAD), 13, fill=GREY)
    if source == "lrcred":
        draw_text(d, PAD + 62, PAD + 44, "lyrics: lrc.red", 11, fill=DIM)
    return img


@lru_cache(maxsize=512)
def _glyph_strip(text: str, size: int, bold: bool, color: tuple[int, int, int]) -> tuple[Image.Image, int]:
    """Tight RGBA bitmap for a single line/word of text; cached so 35fps frames skip FreeType shaping."""
    w = max(1, math.ceil(text_width(text, size, bold)) + 6)
    pad_top = 4
    h = size + 10
    strip = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_text(ImageDraw.Draw(strip), 3, pad_top, text, size, bold, color)
    return strip, 3  # x_pad = 3


def _blur_rgba(strip: Image.Image, radius: float) -> Image.Image:
    m = 6
    padded = Image.new("RGBA", (strip.width + 2 * m, strip.height + 2 * m), (0, 0, 0, 0))
    padded.paste(strip, (m, m))
    return padded.filter(ImageFilter.GaussianBlur(radius))


@lru_cache(maxsize=512)
def _blurred_glyph_strip(text: str, size: int, bold: bool, color: tuple[int, int, int], blur_q: float) -> Image.Image:
    base, _ = _glyph_strip(text, size, bold, color)
    if blur_q <= HAZE_LEAST:
        return base
    return _blur_rgba(base, blur_q)


def _srgb_alpha(linear_a: float) -> float:
    """Convert linear-light opacity (GPU blending) to sRGB alpha for Pillow compositing."""
    linear_a = clamp(linear_a, 0.0, 1.0)
    return linear_a ** (1.0 / 2.2) if linear_a > 0.0 else 0.0


def _apply_alpha(strip: Image.Image, alpha_mul: float) -> Image.Image:
    if alpha_mul >= 0.995:
        return strip
    out = strip.copy()
    out.putalpha(out.getchannel("A").point(lambda a: round(a * alpha_mul)))
    return out


def _viewport_softness(row_top_y: float, anchor_in_view: float) -> float:
    """Sonora's viewport_haze: 0 at PIN anchor, rising as |travel / reach| ** 0.45."""
    travel = row_top_y - anchor_in_view
    reach = (AREA_H * (1.0 - PIN)) if travel >= 0.0 else (AREA_H * PIN)
    norm = clamp(abs(travel) / max(reach, 1.0), 0.0, 1.0)
    return norm ** HAZE_EXP


def _quantized_blur(size: float, softness: float) -> float:
    raw = size * BLUR_RATIO * softness
    q = round(raw / HAZE_STEP) * HAZE_STEP
    return q if q > HAZE_LEAST else 0.0


def _soft_sweep_mask(w: int, h: int, reach: float, soft: float) -> Image.Image:
    """1D alpha mask stretched vertically: white up to reach-soft, feathered to 0 at reach."""
    buf = bytearray(w)
    if reach > 0.0:
        r_int = min(w, math.ceil(reach))
        solid_end = max(0, min(w, math.floor(reach - soft)))
        if solid_end > 0:
            buf[:solid_end] = b"\xff" * solid_end
        if r_int > solid_end:
            inv_soft = 1.0 / max(soft, 1e-4)
            for x in range(solid_end, r_int):
                t = clamp((reach - x) * inv_soft, 0.0, 1.0)
                buf[x] = round(255 * t * t * (3.0 - 2.0 * t))
    return Image.frombytes("L", (w, 1), bytes(buf)).resize((w, h), Image.Resampling.NEAREST)


@dataclass
class _RowKey:
    """One timed slice mapped onto pixel coordinates inside a wrapped row."""
    x0: float
    x1: float
    start_ms: int
    end_ms: int
    group: int
    text: str


@dataclass
class _RiseGroup:
    x0: float
    x1: float
    start_ms: int
    end_ms: int
    text: str


@dataclass
class _LinePlan:
    size: int
    lh: int
    rows: list[str]
    row_widths: list[float]
    row_keys: list[list[_RowKey]]
    rise_groups: list[list[_RiseGroup]]

    @property
    def height(self) -> int:
        return len(self.rows) * self.lh


@dataclass
class _SheetPlan:
    items: list[SheetItem]
    plans: list[_LinePlan | None]
    row_tops: list[float]
    row_heights: list[float]


def _layout_line(line: Line, pace: float, max_w: float) -> _LinePlan:
    units = timed_units(line, pace) if line.words else []
    for size in (BASE_SIZE, 15, 13, 12):
        wrapped = _wrap_with_ranges(line.text, size, max_w, True)
        lh = size + 6
        if len(wrapped) * lh <= AREA_H - 8 or size == 12:
            break

    rows = [r[0] for r in wrapped]
    row_widths = [text_width(r[0], size, True) for r in wrapped]
    row_keys: list[list[_RowKey]] = []
    rise_groups: list[list[_RiseGroup]] = []

    for row_text, rc0, rc1 in wrapped:
        keys: list[_RowKey] = []
        groups_map: dict[int, list[TimedUnit]] = {}
        for u in units:
            oc0 = max(u.c0, rc0)
            oc1 = min(u.c1, rc1)
            if oc0 >= oc1:
                continue
            lc0, lc1 = oc0 - rc0, oc1 - rc0
            x0 = text_width(row_text[:lc0], size, True)
            x1 = text_width(row_text[:lc1], size, True)
            # Split word timing proportionally if a single word wrapped across rows
            full_w = max(text_width(line.text[u.c0:u.c1], size, True), 1e-4) if (u.c0 < rc0 or u.c1 > rc1) else (x1 - x0)
            if u.c0 < rc0 or u.c1 > rc1:
                before_w = text_width(line.text[u.c0:oc0], size, True)
                span = max(u.end_ms - u.start_ms, 0)
                s_ms = u.start_ms + round(span * (before_w / full_w))
                e_ms = u.start_ms + round(span * ((before_w + (x1 - x0)) / full_w))
            else:
                s_ms, e_ms = u.start_ms, u.end_ms
            keys.append(_RowKey(x0, x1, s_ms, e_ms, u.group, row_text[lc0:lc1]))
            groups_map.setdefault(u.group, []).append(TimedUnit(lc0, lc1, s_ms, e_ms, u.group))

        rg: list[_RiseGroup] = []
        for g_units in groups_map.values():
            gc0 = min(u.c0 for u in g_units)
            gc1 = max(u.c1 for u in g_units)
            gs = min(u.start_ms for u in g_units)
            ge = max(u.end_ms for u in g_units)
            sub_text = row_text[gc0:gc1]
            if not sub_text.strip():
                continue
            rg.append(_RiseGroup(text_width(row_text[:gc0], size, True), text_width(row_text[:gc1], size, True), gs, ge, sub_text))

        row_keys.append(keys)
        rise_groups.append(rg)

    return _LinePlan(size, lh, rows, row_widths, row_keys, rise_groups)


_SHEET_CACHE: dict[tuple, _SheetPlan] = {}


def _get_sheet_plan(lyrics: Lyrics) -> _SheetPlan:
    key = (id(lyrics), lyrics.source, len(lyrics.lines), lyrics.lines[0].text if lyrics.lines else "")
    cached = _SHEET_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_SHEET_CACHE) > 8:
        _SHEET_CACHE.clear()
    items = build_sheet_items(lyrics.lines)
    pace = pace_limit(lyrics.lines)
    max_w = W - 2 * PAD
    plans: list[_LinePlan | None] = []
    row_tops: list[float] = []
    row_heights: list[float] = []
    y = 0.0
    for it in items:
        if it.kind == "break":
            plans.append(None)
            h = float(BREAK_H)
        else:
            p = _layout_line(it.line, pace, max_w)
            plans.append(p)
            h = float(p.height)
        row_tops.append(y)
        row_heights.append(h)
        y += h + VERSE_GAP
    plan = _SheetPlan(items, plans, row_tops, row_heights)
    _SHEET_CACHE[key] = plan
    return plan


def _row_reveal(keys: list[_RowKey], row_w: float, pos_ms: int, soft: float) -> float:
    """Monotone cubic sweep reach across a row, stretched across [0, row_w + soft]."""
    if not keys or row_w <= 0.0:
        return 0.0
    pts: list[tuple[float, float]] = []
    for k in keys:
        t0, t1 = k.start_ms / 1000.0, k.end_ms / 1000.0
        for t, x in ((t0, k.x0), (t1, k.x1)):
            if pts and t <= pts[-1][0]:
                pts[-1] = (pts[-1][0], max(pts[-1][1], x))
            else:
                prev_x = pts[-1][1] if pts else 0.0
                pts.append((t, max(prev_x, x)))
    front = monotone(pts, pos_ms / 1000.0)
    return front * ((row_w + soft) / row_w)


def _render_karaoke_row(
    row_text: str,
    size: int,
    row_w: float,
    keys: list[_RowKey],
    groups: list[_RiseGroup],
    base_alpha: float,
    lit_alpha: float,
    pos_ms: int,
    sung_end_ms: int | None,
    active: bool,
) -> tuple[Image.Image, int]:
    """Render a karaoke row with PCHIP monotone sweep, feathered edge, and subtle word rise/glow."""
    soft = clamp(size * 0.72, 8.0, 14.0)
    reach = _row_reveal(keys, row_w, pos_ms, soft)
    lit_full, x_pad = _glyph_strip(row_text, size, True, WHITE)
    base_strip = _apply_alpha(lit_full, base_alpha)
    sw, sh = base_strip.size
    rise_px = max(1.5, size * 0.085)

    # Settle factor once the line's words have finished
    if active or sung_end_ms is None or pos_ms < sung_end_ms:
        settle = 1.0
    else:
        rung = clamp((pos_ms - sung_end_ms) / 140.0, 0.0, 1.0)
        settle = 1.0 - ease_out_cubic(rung)

    # Compute per-group vertical lift and glow
    group_fx: list[tuple[_RiseGroup, float, float]] = []
    any_fx = False
    for g in groups:
        r = word_rise(g.start_ms, g.end_ms, pos_ms) * settle * rise_px if pos_ms >= g.start_ms else 0.0
        gl = word_glow(g.start_ms, g.end_ms, pos_ms) * settle if pos_ms >= g.start_ms else 0.0
        if r > 0.05 or gl > 0.01:
            any_fx = True
        group_fx.append((g, r, gl))

    pad_top = math.ceil(rise_px) + 2
    out = Image.new("RGBA", (sw, sh + pad_top), (0, 0, 0, 0))

    if not any_fx or not group_fx:
        if reach > 0.0 and lit_alpha > 0.01:
            mask = _soft_sweep_mask(sw, sh, reach + x_pad, soft)
            lit_a = ImageChops.multiply(lit_full.getchannel("A"), mask)
            if lit_alpha < 0.995:
                lit_a = lit_a.point(lambda a: round(a * lit_alpha))
            comb = lit_full.copy()
            comb.putalpha(ImageChops.lighter(base_strip.getchannel("A"), lit_a))
            out.paste(comb, (0, pad_top))
        else:
            out.paste(base_strip, (0, pad_top))
        return out, pad_top

    # Per-word rise/glow with continuous unbroken sweep front across the row
    # First draw ungrouped characters (punctuation/spaces) from base_strip if needed,
    # or draw each group cleanly at its exact x0, y - rise!
    # Simple & exact: draw un-risen background or draw each token slice:
    # Draw base characters first, masking out raised groups, or draw every group cleanly:
    sweep_mask = _soft_sweep_mask(sw, sh, reach + x_pad, soft) if (reach > 0.0 and lit_alpha > 0.01) else None

    def _draw_slice(box: tuple[int, int, int, int], dx: int, dy: int, gl: float):
        g_base = base_strip.crop(box)
        if gl > 0.01:
            halo_a = lit_full.crop(box).getchannel("A").filter(ImageFilter.GaussianBlur(2.0)).point(lambda a: round(a * gl * 0.9))
            halo = Image.new("RGBA", (box[2] - box[0], sh), (*WHITE, 255))
            halo.putalpha(halo_a)
            out.alpha_composite(halo, (dx, dy))
        if sweep_mask is not None:
            g_lit = lit_full.crop(box)
            lit_a = ImageChops.multiply(g_lit.getchannel("A"), sweep_mask.crop(box))
            if lit_alpha < 0.995:
                lit_a = lit_a.point(lambda a: round(a * lit_alpha))
            g_lit.putalpha(ImageChops.lighter(g_base.getchannel("A"), lit_a))
            out.alpha_composite(g_lit, (dx, dy))
        else:
            out.alpha_composite(g_base, (dx, dy))

    covered = bytearray(sw)
    for g, r_y, gl in group_fx:
        gx0 = max(0, math.floor(x_pad + g.x0) - 1)
        gx1 = min(sw, math.ceil(x_pad + g.x1) + 1)
        if gx1 <= gx0:
            continue
        covered[gx0:gx1] = b"\x01" * (gx1 - gx0)
        _draw_slice((gx0, 0, gx1, sh), gx0, round(pad_top - r_y), gl)

    if any(not c for c in covered):
        uncov_m = Image.frombytes("L", (sw, 1), bytes(0 if c else 255 for c in covered)).resize((sw, sh), Image.Resampling.NEAREST)
        b_unc = base_strip.copy()
        b_unc.putalpha(ImageChops.multiply(b_unc.getchannel("A"), uncov_m))
        if sweep_mask is not None:
            lit_a = ImageChops.multiply(lit_full.getchannel("A"), ImageChops.multiply(sweep_mask, uncov_m))
            if lit_alpha < 0.995:
                lit_a = lit_a.point(lambda a: round(a * lit_alpha))
            l_unc = lit_full.copy()
            l_unc.putalpha(ImageChops.lighter(b_unc.getchannel("A"), lit_a))
            out.alpha_composite(l_unc, (0, pad_top))
        else:
            out.alpha_composite(b_unc, (0, pad_top))

    return out, pad_top


def _draw_break_row(canvas: Image.Image, center_y: float, progress: float, past: bool, scale: float):
    """Three animated music notes ♪ ♪ ♪ for instrumental breaks, left-aligned like Sonora."""
    note_size = max(10, round(15 * scale))
    gap = round(14 * scale)
    x0 = PAD
    d = ImageDraw.Draw(canvas)
    for i in range(3):
        p = clamp(progress * 3.0 - i, 0.0, 1.0)
        a = _srgb_alpha(PAST_LINEAR if past else (AHEAD_LINEAR + (1.0 - AHEAD_LINEAR) * p))
        col = (*WHITE, round(255 * a))
        draw_text(d, x0 + i * gap, center_y - note_size * 0.65, "♪", note_size, fill=col)


def _paste_left_scaled(
    canvas: Image.Image,
    strip: Image.Image,
    left_x: float,
    center_y: float,
    scale: float,
    x_pad: float,
    pad_top: float = 0.0,
):
    """Scale strip around its left text edge and paste at (left_x, center_y)."""
    if abs(scale - 1.0) > 0.01:
        nw = max(1, round(strip.width * scale))
        nh = max(1, round(strip.height * scale))
        strip = strip.resize((nw, nh), Image.Resampling.LANCZOS)
    x = round(left_x - x_pad * scale)
    y = round(center_y - strip.height * 0.5 - pad_top * 0.5 * scale)
    canvas.alpha_composite(strip, (x, y))


def _compose_sheet(
    lyrics: Lyrics,
    pos_ms: int,
    karaoke: bool,
    animator: SheetAnimator | None,
    now_s: float,
) -> Image.Image:
    """Render the scrollable lyric viewport (W x LYRICS_H) as an RGBA layer with edge fades."""
    plan = _get_sheet_plan(lyrics)
    items = plan.items
    animate = animator is not None
    if animator is None:
        animator = SheetAnimator()
    animator.step(lyrics, items, plan.row_tops, plan.row_heights, ANCHOR_Y, pos_ms, now_s, animate=animate)

    viewport = Image.new("RGBA", (W, LYRICS_H), (0, 0, 0, 0))
    view_off_y = AREA_TOP - LYRICS_Y  # 2px top padding inside the 8px-aligned viewport
    arr_t = ease_out_expo((now_s - animator.arrived_at) / 0.22) if animate else 1.0
    dep_t = ease_out_expo((now_s - animator.departed_at) / 0.22) if animate else 1.0
    active_row = animator.active_row
    departing_row = animator.departing_row

    for r_idx, item in enumerate(items):
        drift = animator.row_drifts[r_idx][0] if r_idx in animator.row_drifts else 0.0
        top_y = view_off_y + plan.row_tops[r_idx] - animator.scroll_y + drift
        h = plan.row_heights[r_idx]
        if top_y + h < -12 or top_y > LYRICS_H + 12:
            continue

        is_active = r_idx == active_row
        is_departing = r_idx == departing_row and not is_active
        if is_active:
            scale = CONTEXT_SCALE + (1.0 - CONTEXT_SCALE) * arr_t
        elif is_departing:
            scale = 1.0 - (1.0 - CONTEXT_SCALE) * dep_t
        else:
            scale = CONTEXT_SCALE

        if item.kind == "break":
            end_ms = item.end_ms if item.end_ms is not None else item.start_ms
            span = max(end_ms - item.start_ms, 1)
            prog = clamp((pos_ms - item.start_ms) / span, 0.0, 1.0)
            past = pos_ms >= end_ms or (active_row >= 0 and r_idx < active_row)
            softness = _viewport_softness(top_y, view_off_y + ANCHOR_Y)
            eff_soft = softness * (1.0 - arr_t) if is_active else (softness * dep_t if is_departing else softness)
            blur_q = _quantized_blur(15.0, eff_soft)
            if blur_q > 0.0 or eff_soft > 0.02:
                br_strip = Image.new("RGBA", (W, BREAK_H + 12), (0, 0, 0, 0))
                _draw_break_row(br_strip, (BREAK_H + 12) * 0.5, prog, past, scale)
                if blur_q > 0.0:
                    br_strip = br_strip.filter(ImageFilter.GaussianBlur(blur_q))
                br_strip = _apply_alpha(br_strip, 1.0 - VEIL * eff_soft)
                viewport.alpha_composite(br_strip, (0, round(top_y + h * 0.5 - br_strip.height * 0.5)))
            else:
                _draw_break_row(viewport, top_y + h * 0.5, prog, past, scale)
            continue

        line = item.line
        lp = plan.plans[r_idx]
        assert line is not None and lp is not None
        sung_end = own_sung_end(line)
        line_ended = (active_row >= 0 and r_idx < active_row) or (sung_end is not None and pos_ms >= sung_end)
        worded = karaoke and bool(line.words)
        show_karaoke = worded and (is_active or (pos_ms >= line.start_ms and sung_end is not None and pos_ms < sung_end + 800))

        softness = _viewport_softness(top_y, view_off_y + ANCHOR_Y)
        if is_active:
            eff_soft = softness * (1.0 - arr_t)
        elif is_departing:
            eff_soft = softness * dep_t
        else:
            eff_soft = softness
        blur_q = _quantized_blur(lp.size, eff_soft)
        veil_mul = 1.0 - VEIL * eff_soft

        rest_linear = QUIET_LINEAR if (is_active and worded) else (1.0 if is_active else (PAST_LINEAR if line_ended else AHEAD_LINEAR))
        if is_active:
            base_linear = AHEAD_LINEAR + ((QUIET_LINEAR if worded else 1.0) - AHEAD_LINEAR) * arr_t
        elif is_departing:
            start_lin = QUIET_LINEAR if worded else 1.0
            base_linear = start_lin + (PAST_LINEAR - start_lin) * dep_t
        else:
            base_linear = rest_linear

        if show_karaoke:
            if is_active or sung_end is None:
                fade = 0.0
            else:
                fade = clamp((pos_ms - sung_end) / (KARAOKE_FADE_S * 1000.0), 0.0, 1.0)
            lit_alpha = _srgb_alpha((1.0 - fade) * veil_mul)
            base_alpha = _srgb_alpha(base_linear * veil_mul)

            for sub_i, row_text in enumerate(lp.rows):
                row_cy = top_y + (sub_i + 0.5) * lp.lh
                strip, pad_top = _render_karaoke_row(
                    row_text,
                    lp.size,
                    lp.row_widths[sub_i],
                    lp.row_keys[sub_i],
                    lp.rise_groups[sub_i],
                    base_alpha,
                    lit_alpha,
                    pos_ms,
                    sung_end,
                    is_active,
                )
                x_pad = 3
                if blur_q > 0.0:
                    strip = _blur_rgba(strip, blur_q)
                    x_pad += 6
                _paste_left_scaled(viewport, strip, PAD, row_cy, scale, x_pad, pad_top)
        else:
            alpha_srgb = _srgb_alpha(base_linear * veil_mul)
            for sub_i, row_text in enumerate(lp.rows):
                row_cy = top_y + (sub_i + 0.5) * lp.lh
                strip = _blurred_glyph_strip(row_text, lp.size, True, WHITE, blur_q)
                strip = _apply_alpha(strip, alpha_srgb)
                x_pad = 3 + (6 if blur_q > HAZE_LEAST else 0)
                _paste_left_scaled(viewport, strip, PAD, row_cy, scale, x_pad)

    edge_mask = _edge_fade_mask(W, LYRICS_H, EDGE_FADE_PX)
    viewport.putalpha(ImageChops.multiply(viewport.getchannel("A"), edge_mask))
    return viewport


def _compose_region(
    np: NowPlaying,
    position_ms: int,
    lyrics: Lyrics | None,
    karaoke: bool,
    animator: SheetAnimator | None,
    now_s: float,
    region: str,
) -> tuple[Image.Image, int, int]:
    if not lyrics or not lyrics.lines:
        return render_basic_image(np, position_ms), 0, 0
    img = _base_screen(np.cover, np.title, np.artist, lyrics.source).copy()
    sheet = _compose_sheet(lyrics, position_ms, karaoke, animator, now_s)
    img.paste(sheet, (0, LYRICS_Y), sheet)

    if region == "lyrics":
        return img.crop((0, LYRICS_Y, W, LYRICS_Y + LYRICS_H)), 0, LYRICS_Y

    _progress(ImageDraw.Draw(img), np, position_ms)
    if region == "lower":
        return img.crop((0, LYRICS_Y, W, H)), 0, LYRICS_Y
    return img, 0, 0


def render_frame(
    np: NowPlaying,
    position_ms: int,
    lyrics: Lyrics | None,
    karaoke: bool = False,
    animator: SheetAnimator | None = None,
    now_s: float | None = None,
    region: str = "full",
) -> tuple[bytes, int, int]:
    """Render full 240x240 JPEG or dirty-rect JPEG ('lyrics' or 'lower'); returns (jpeg, x, y)."""
    if now_s is None:
        now_s = time.monotonic()
    crop, x, y = _compose_region(np, position_ms, lyrics, karaoke, animator, now_s, region)
    return _jpeg(crop), x, y


def render_stream_frame(
    np: NowPlaying,
    position_ms: int,
    lyrics: Lyrics | None,
    karaoke: bool = False,
    animator: SheetAnimator | None = None,
    now_s: float | None = None,
    region: str = "full",
) -> tuple[bytes, int, int, int, int]:
    """Lossless zlib-compressed RGB888 crop for TCP stream: (payload, x, y, w, h)."""
    if now_s is None:
        now_s = time.monotonic()
    crop, x, y = _compose_region(np, position_ms, lyrics, karaoke, animator, now_s, region)
    return zlib.compress(crop.tobytes(), 1), x, y, crop.width, crop.height


def render_lyrics(
    np: NowPlaying,
    position_ms: int,
    lyrics: Lyrics | None,
    karaoke: bool = False,
    animator: SheetAnimator | None = None,
    now_s: float | None = None,
) -> bytes:
    """Full 240x240 JPEG with Sonora-style animated lyrics."""
    jpeg, _, _ = render_frame(np, position_ms, lyrics, karaoke, animator, now_s, region="full")
    return jpeg
