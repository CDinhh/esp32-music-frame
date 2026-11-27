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
    TRANSITION_S,
    TimedUnit,
    build_sheet_items,
    clamp,
    ease_out_cubic,
    ease_out_expo,
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

# Sonora's text opacities and haze (crates/views/src/shared/mod.rs & chrome/aside.rs)
QUIET = 0.56
PAST = QUIET * 0.40
AHEAD = QUIET * 0.60
VEIL = 0.30

# Lyric viewport between the header and the progress bar; the line being sung sits in its middle
LYRICS_Y = 76
LYRICS_H = 136
AREA_TOP = LYRICS_Y
ANCHOR_Y = LYRICS_H / 2
EDGE_FADE_PX = 16
CONTEXT_SCALE = 0.7  # the other lines rest at 14 px, the one being sung is 20 px
VERSE_GAP = 12
BREAK_H = 20
BASE_SIZE = 20
HAZE_EXP = 0.45
HAZE_REACH = LYRICS_H  # keeps a neighbour about half as soft as a far line, as on Sonora's tall sheet
BLUR_RATIO = 0.13
HAZE_STEP = 0.25
HAZE_LEAST = 0.30
SS = 2   # lyrics are drawn at twice the size and scaled down: even letter spacing, smooth sub-pixel motion
EDGE = 12  # transparent margin around a supersampled block

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


ACCENT = (124, 199, 255)  # light blue, the same as the web panel
BARS_Y = 225             # vertical middle of the timeline
TIMELINE_Y = 212         # everything below belongs to the timeline and is streamed on its own
RAIL = (112, 118, 140)   # the time still to play
WAVE_LEN, WAVE_AMP, WAVE_SPEED = 16.0, 2.8, 18.0  # px, px, px per second


def _span(np: NowPlaying, pos: int) -> tuple[str, str, int, int]:
    """(elapsed text, remaining text, left and right end of the track)."""
    left = clock(min(pos, np.duration_ms) if np.duration_ms else pos)
    right = "-" + clock(np.duration_ms - min(pos, np.duration_ms)) if np.duration_ms else ""
    widest = clock(np.duration_ms) if np.duration_ms else "0:00"  # fixed ends: the track never jumps
    x0 = PAD + math.ceil(text_width(widest, 11, True)) + 9
    x1 = W - PAD - math.ceil(text_width("-" + widest, 11, True)) - 9
    return left, right, x0, x1


def progress_visual(np: NowPlaying, pos: int) -> tuple[str, int]:
    """Observable progress bar state (left clock string, filled pixel x) for dirty-rect checks."""
    left, _, x0, x1 = _span(np, pos)
    if np.duration_ms <= 0:
        return left, -1
    return left, round(x0 + (x1 - x0) * min(max(pos, 0) / np.duration_ms, 1.0))


def _progress(img: Image.Image, np: NowPlaying, pos: int, t: float = 0.0):
    """'1:23 ~~~~~o──────── -2:59': the played part is a flowing wave, the rest a plain rail."""
    left, right, x0, x1 = _span(np, pos)
    d = ImageDraw.Draw(img)
    draw_text(d, PAD, BARS_Y - 8, left, 11, True, WHITE)
    draw_text(d, W - PAD - text_width(right, 11, True), BARS_Y - 8, right, 11, True, WHITE)

    k, h = 3, 16  # drawn three times larger, then scaled down for smooth edges
    pad = 6
    w = x1 - x0 + 2 * pad
    layer = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    mid = h * k / 2
    done = (x1 - x0) * (min(max(pos, 0) / np.duration_ms, 1.0) if np.duration_ms > 0 else 0.0)
    head = (pad + done) * k
    end = (pad + x1 - x0) * k
    ld.line([(head, mid), (end, mid)], fill=(*RAIL, 255), width=4 * k)
    ld.ellipse([end - 2 * k, mid - 2 * k, end + 2 * k, mid + 2 * k], fill=(*RAIL, 255))

    points = []
    for i in range(int(done * 2) + 1):  # a point every half pixel
        x = i / 2
        ease = min(1.0, x / 8, (done - x) / 8)  # flat where it leaves the start and meets the knob
        y = math.sin((x - t * WAVE_SPEED) * math.tau / WAVE_LEN) * WAVE_AMP * max(ease, 0.0)
        points.append(((pad + x) * k, mid + y * k))
    if len(points) > 1:
        ld.line(points, fill=(*ACCENT, 255), width=3 * k, joint="curve")
        x, y = points[0]
        ld.ellipse([x - 1.5 * k, y - 1.5 * k, x + 1.5 * k, y + 1.5 * k], fill=(*ACCENT, 255))

    ld.ellipse([head - 5.5 * k, mid - 5.5 * k, head + 5.5 * k, mid + 5.5 * k], fill=(*ACCENT, 110))  # halo
    ld.ellipse([head - 4 * k, mid - 4 * k, head + 4 * k, mid + 4 * k], fill=(*WHITE, 255))
    layer = layer.resize((w, h), Image.Resampling.LANCZOS)
    img.paste(layer, (x0 - pad, BARS_Y - h // 2), layer)


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def render_basic_image(np: NowPlaying, position_ms: int, now_s: float = 0.0) -> Image.Image:
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
    _progress(img, np, position_ms, now_s)
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


def _viewport_softness(row_centre_y: float, anchor_in_view: float) -> float:
    """Sonora's viewport_haze: 0 on the line being sung, rising as |travel / reach| ** 0.45."""
    return clamp(abs(row_centre_y - anchor_in_view) / HAZE_REACH, 0.0, 1.0) ** HAZE_EXP


def _quantized_blur(size: float, softness: float) -> float:
    q = round(size * BLUR_RATIO * softness / HAZE_STEP) * HAZE_STEP
    return q if q > HAZE_LEAST else 0.0


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
    heights: list[float]  # at full size


def _layout_line(line: Line, pace: float, max_w: float) -> _LinePlan:
    units = timed_units(line, pace) if line.words else []
    def width(text: str, size: int) -> float:  # measured at the size it is drawn at
        return text_width(text, size * SS, True) / SS

    for size in (BASE_SIZE, 18, 16, 14, 12):
        wrapped = _wrap_with_ranges(line.text, size, max_w, True)
        lh = size + 6
        if len(wrapped) * lh <= LYRICS_H - 8 or size == 12:
            break

    rows = [r[0] for r in wrapped]
    row_widths = [width(r[0], size) for r in wrapped]
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
            x0 = width(row_text[:lc0], size)
            x1 = width(row_text[:lc1], size)
            # Split word timing proportionally if a single word wrapped across rows
            full_w = max(width(line.text[u.c0:u.c1], size), 1e-4) if (u.c0 < rc0 or u.c1 > rc1) else (x1 - x0)
            if u.c0 < rc0 or u.c1 > rc1:
                before_w = width(line.text[u.c0:oc0], size)
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
            rg.append(_RiseGroup(width(row_text[:gc0], size), width(row_text[:gc1], size), gs, ge, sub_text))

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
    plans = [None if it.kind == "break" else _layout_line(it.line, pace, max_w) for it in items]
    heights = [float(BREAK_H if p is None else p.height) for p in plans]
    plan = _SheetPlan(items, plans, heights)
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


@lru_cache(maxsize=128)
def _block(rows: tuple[str, ...], size: int, lh: int) -> Image.Image:
    """Alpha mask of a wrapped line at SS times its size, with an EDGE margin."""
    width = max(math.ceil(text_width(t, size * SS, True)) for t in rows)
    mask = Image.new("L", (width + 2 * EDGE + 4, len(rows) * lh * SS + 2 * EDGE), 0)
    d = ImageDraw.Draw(mask)
    for i, t in enumerate(rows):
        # centre the letters (not the font box) in their line
        draw_text(d, EDGE, EDGE + (i * lh + 3 - 0.14 * size) * SS, t, size * SS, True, 255)
    return mask


def _sweep(width: int, reach: float, soft: float, base: int, lit: int) -> bytes:
    """One row of alpha values: `lit` up to the sweep front, feathered into `base`."""
    solid = max(0, min(width, math.floor(reach - soft)))
    buf = bytearray([lit]) * solid + bytearray([base]) * (width - solid)
    for x in range(solid, min(width, math.ceil(reach))):
        t = clamp((reach - x) / soft, 0.0, 1.0)
        buf[x] = round(base + (lit - base) * t * t * (3.0 - 2.0 * t))
    return bytes(buf)


def _karaoke_block(lp: _LinePlan, base: float, lit: float, pos_ms: int, sung_end: int | None, active: bool) -> Image.Image:
    """The line's mask with the sung part lit, sung words raised and long syllables glowing."""
    text = _block(tuple(lp.rows), lp.size, lp.lh)
    out = Image.new("L", text.size, 0)
    row_h = lp.lh * SS
    soft = clamp(lp.size * 0.72, 8.0, 14.0)
    rise_px = max(1.5, lp.size * 0.085)
    settle = 1.0
    if not (active or sung_end is None or pos_ms < sung_end):
        settle = 1.0 - ease_out_cubic(clamp((pos_ms - sung_end) / 140.0, 0.0, 1.0))
    base_v, lit_v = round(base * 255), round(max(lit, base) * 255)

    for i in range(len(lp.rows)):
        top = EDGE + i * row_h
        y0 = top - (EDGE if i == 0 else 0)
        y1 = top + row_h + (EDGE if i == len(lp.rows) - 1 else 0)
        reach = _row_reveal(lp.row_keys[i], lp.row_widths[i], pos_ms, soft)
        front = EDGE + reach * SS if reach > 0.0 else 0.0
        grad = Image.frombytes("L", (text.width, 1), _sweep(text.width, front, soft * SS, base_v, lit_v))
        row = ImageChops.multiply(text.crop((0, y0, text.width, y1)), grad.resize((text.width, y1 - y0), Image.Resampling.NEAREST))

        groups = lp.rise_groups[i]
        # cut the row between words, so a raised word never slices a letter
        cuts = [0] + [round(EDGE + (a.x1 + b.x0) * 0.5 * SS) for a, b in zip(groups, groups[1:])] + [text.width]
        if not groups:
            out.paste(ImageChops.lighter(out.crop((0, y0, text.width, y1)), row), (0, y0))
            continue
        for g, x0, x1 in zip(groups, cuts, cuts[1:]):
            if x1 <= x0:
                continue
            sung = pos_ms >= g.start_ms
            lift = round(word_rise(g.start_ms, g.end_ms, pos_ms) * settle * rise_px * SS) if sung else 0
            glow = word_glow(g.start_ms, g.end_ms, pos_ms) * settle if sung else 0.0
            word = row.crop((x0, 0, x1, y1 - y0))
            if glow > 0.01:
                word = ImageChops.add(word, word.filter(ImageFilter.GaussianBlur(2.0 * SS)).point(lambda v: round(v * glow * 0.9)))
            box = (x0, y0 - lift, x1, y1 - lift)
            out.paste(ImageChops.lighter(out.crop(box), word), box)
    return out


def _break_block(progress: float, past: bool) -> Image.Image:
    """Three notes that light up one by one through an instrumental break."""
    mask = Image.new("L", (60 * SS + 2 * EDGE, BREAK_H * SS + 2 * EDGE), 0)
    d = ImageDraw.Draw(mask)
    for i in range(3):
        p = clamp(progress * 3.0 - i, 0.0, 1.0)
        a = PAST if past else AHEAD + (1.0 - AHEAD) * p
        draw_text(d, EDGE + i * 14 * SS, EDGE + 2 * SS, "\u266a", 15 * SS, False, round(a * 255))
    return mask


def _stamp(sheet: Image.Image, block: Image.Image, x: float, y: float, scale: float, blur: float, alpha: float = 1.0):
    """Scale a supersampled block and put its text origin at the (fractional) point x, y."""
    f = scale / SS
    ox, oy = math.ceil(x - EDGE * f), math.ceil(y - EDGE * f)
    u, v = max(EDGE - (x - ox) / f, 0.0), max(EDGE - (y - oy) / f, 0.0)
    w, h = int((block.width - u) * f), int((block.height - v) * f)
    if w <= 0 or h <= 0:
        return
    small = block.resize((w, h), Image.Resampling.HAMMING, box=(u, v, u + w / f, v + h / f))
    if blur > 0.0:
        m = math.ceil(blur * 2) + 1
        small = ImageOps.expand(small, m, 0).filter(ImageFilter.GaussianBlur(blur))
        ox, oy = ox - m, oy - m
    if alpha < 0.995:
        small = small.point(lambda a: round(a * alpha))
    box = (ox, oy, ox + small.width, oy + small.height)
    sheet.paste(ImageChops.lighter(sheet.crop(box), small), box)


@lru_cache(maxsize=1)
def _edge_fade() -> Image.Image:
    col = bytearray(LYRICS_H)
    for y in range(LYRICS_H):
        t = clamp(min(y, LYRICS_H - 1 - y) / EDGE_FADE_PX, 0.0, 1.0)
        col[y] = round(255 * t * t * (3 - 2 * t))
    return Image.frombytes("L", (1, LYRICS_H), bytes(col)).resize((W, LYRICS_H), Image.Resampling.NEAREST)


def _compose_sheet(
    lyrics: Lyrics,
    pos_ms: int,
    karaoke: bool,
    animator: SheetAnimator | None,
    now_s: float,
) -> Image.Image:
    """The scrolling lyric viewport (W x LYRICS_H) as an alpha mask for white text."""
    plan = _get_sheet_plan(lyrics)
    items = plan.items
    animate = animator is not None
    if animator is None:
        animator = SheetAnimator()

    def sizes(a: SheetAnimator) -> list[float]:
        arrived = ease_out_expo((now_s - a.arrived_at) / TRANSITION_S) if animate else 1.0
        departed = ease_out_expo((now_s - a.departed_at) / TRANSITION_S) if animate else 1.0
        out = [CONTEXT_SCALE] * len(items)
        if 0 <= a.active_row < len(items):
            out[a.active_row] = CONTEXT_SCALE + (1.0 - CONTEXT_SCALE) * arrived
        if a.departing_row is not None and a.departing_row != a.active_row:
            out[a.departing_row] = 1.0 - (1.0 - CONTEXT_SCALE) * departed
        return out

    def layout(a: SheetAnimator):
        # rows take exactly the height they are drawn at, so the gap between lines is always the same
        heights = [h * s for h, s in zip(plan.heights, sizes(a))]
        tops, y = [], 0.0
        for h in heights:
            tops.append(y)
            y += h + VERSE_GAP
        return tops, heights

    tops, heights = animator.step(lyrics, items, layout, ANCHOR_Y, LYRICS_H, pos_ms, now_s, animate=animate)
    scales = sizes(animator)
    arr_t = ease_out_expo((now_s - animator.arrived_at) / TRANSITION_S) if animate else 1.0
    dep_t = ease_out_expo((now_s - animator.departed_at) / TRANSITION_S) if animate else 1.0
    active_row = animator.active_row
    departing_row = animator.departing_row
    sheet = Image.new("L", (W, LYRICS_H), 0)

    for r_idx, item in enumerate(items):
        drift = animator.row_drifts[r_idx][0] if r_idx in animator.row_drifts else 0.0
        top = tops[r_idx] - animator.scroll_y + drift
        h = heights[r_idx]
        if top + h < -12 or top > LYRICS_H + 12:
            continue

        is_active = r_idx == active_row
        is_departing = r_idx == departing_row and not is_active
        softness = _viewport_softness(top + h * 0.5, ANCHOR_Y)
        if is_active:
            softness *= 1.0 - arr_t
        elif is_departing:
            softness *= dep_t
        veil = 1.0 - VEIL * softness

        if item.kind == "break":
            end_ms = item.end_ms if item.end_ms is not None else item.start_ms
            prog = clamp((pos_ms - item.start_ms) / max(end_ms - item.start_ms, 1), 0.0, 1.0)
            past = pos_ms >= end_ms or (active_row >= 0 and r_idx < active_row)
            _stamp(sheet, _break_block(prog, past), PAD, top, scales[r_idx], _quantized_blur(15.0, softness), veil)
            continue

        line = item.line
        lp = plan.plans[r_idx]
        assert line is not None and lp is not None
        sung_end = own_sung_end(line)
        line_ended = (active_row >= 0 and r_idx < active_row) or (sung_end is not None and pos_ms >= sung_end)
        worded = karaoke and bool(line.words)
        show_karaoke = worded and (is_active or (pos_ms >= line.start_ms and sung_end is not None and pos_ms < sung_end + 800))

        if is_active:
            base = AHEAD + ((QUIET if worded else 1.0) - AHEAD) * arr_t
        elif is_departing:
            start = QUIET if worded else 1.0
            base = start + (PAST - start) * dep_t
        else:
            base = PAST if line_ended else AHEAD
        blur = _quantized_blur(lp.size * CONTEXT_SCALE, softness)

        if show_karaoke:
            fade = 0.0
            if not is_active and sung_end is not None:
                fade = clamp((pos_ms - sung_end) / (KARAOKE_FADE_S * 1000.0), 0.0, 1.0)
            block = _karaoke_block(lp, base * veil, (1.0 - fade) * veil, pos_ms, sung_end, is_active)
            _stamp(sheet, block, PAD, top, scales[r_idx], blur)
        else:
            _stamp(sheet, _block(tuple(lp.rows), lp.size, lp.lh), PAD, top, scales[r_idx], blur, base * veil)

    return ImageChops.multiply(sheet, _edge_fade())


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
        return render_basic_image(np, position_ms, now_s), 0, 0
    img = _base_screen(np.cover, np.title, np.artist, lyrics.source).copy()
    sheet = _compose_sheet(lyrics, position_ms, karaoke, animator, now_s)
    img.paste((255, 255, 255), (0, LYRICS_Y), sheet)

    if region == "lyrics":
        return img.crop((0, LYRICS_Y, W, LYRICS_Y + LYRICS_H)), 0, LYRICS_Y

    _progress(img, np, position_ms, now_s)
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
    previous: Image.Image | None = None,
) -> tuple[list[tuple[bytes, int, int, int, int]], Image.Image]:
    """Zlib RGB888 of the parts that differ from `previous` (what the screen shows now), as
    [(payload, x, y, w, h)], and the whole new frame. Less data per frame is what makes motion
    smooth; the ever-moving timeline goes on its own so it does not drag the lyrics along."""
    if now_s is None:
        now_s = time.monotonic()
    frame, _, _ = _compose_region(np, position_ms, lyrics, karaoke, animator, now_s, "full")
    if previous is None:
        boxes = [(0, 0, W, H)]
    else:
        diff = ImageChops.difference(frame, previous)
        boxes = []
        for top, bottom in ((0, TIMELINE_Y), (TIMELINE_Y, H)):
            box = diff.crop((0, top, W, bottom)).getbbox()
            if box:
                boxes.append((box[0], box[1] + top, box[2], box[3] + top))
        # nothing changed: one pixel in the corner keeps the screen in music mode
        boxes = boxes or [(W - 1, H - 1, W, H)]
    parts = []
    for box in boxes:
        crop = frame.crop(box)
        parts.append((zlib.compress(crop.tobytes(), 1), box[0], box[1], crop.width, crop.height))
    return parts, frame


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
