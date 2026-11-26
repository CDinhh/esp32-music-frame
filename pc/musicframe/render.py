"""Draws the whole 240x240 music screen as a JPEG, so the ESP32 needs no fonts or layout."""
import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .lyrics.lrc import line_index
from .model import Line, Lyrics, NowPlaying

W = H = 240
PAD = 14
WHITE, GREY, DIM = (255, 255, 255), (170, 176, 196), (110, 116, 136)
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


def clock(ms: int) -> str:
    s = max(ms, 0) // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


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
        x = x0 + (x1 - x0) * min(pos / np.duration_ms, 1)
        d.rounded_rectangle([x0, by, max(x, x0 + 4), by + 4], 2, fill=WHITE)


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def render_basic(np: NowPlaying, position_ms: int) -> bytes:
    """No lyrics: big cover, title, artist, progress."""
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
    return _jpeg(img)


def _header(img: Image.Image, d: ImageDraw.ImageDraw, np: NowPlaying):
    cover = _cover(np.cover, 52)
    if cover:
        img.paste(cover, (PAD, PAD), _mask(52, 8))
    else:
        d.rounded_rectangle([PAD, PAD, PAD + 52, PAD + 52], 8, fill=(32, 38, 60))
    x = PAD + 62
    draw_text(d, x, PAD + 4, fit(np.title, 16, W - x - PAD, True), 16, True)
    draw_text(d, x, PAD + 28, fit(np.artist, 13, W - x - PAD), 13, fill=GREY)


def _sung_chars(line: Line, pos: int) -> float:
    """How many characters of line.text are sung at pos (word timing, partial word interpolated)."""
    n = 0.0
    for w in line.words or []:
        if pos >= w.end_ms:
            n += len(w.text)
        elif pos > w.start_ms:
            n += len(w.text) * (pos - w.start_ms) / max(w.end_ms - w.start_ms, 1)
            break
        else:
            break
    # words may carry spaces the joined text dropped at the ends
    return min(n, len(line.text))


def render_lyrics(np: NowPlaying, position_ms: int, lyrics: Lyrics | None, karaoke: bool = False) -> bytes:
    if not lyrics or not lyrics.lines:
        return render_basic(np, position_ms)
    img = _background(np.cover).copy()
    d = ImageDraw.Draw(img)
    _header(img, d, np)
    lines = lyrics.lines
    idx = line_index(lines, position_ms)
    max_w = W - 2 * PAD
    area_top, area_bot = PAD + 52 + 10, H - 36  # between the header and the timeline
    area_h = area_bot - area_top
    cur_text = (lines[idx].text if idx >= 0 else "") or "♪"
    # the sung line is always shown whole: wrap it and step the size down until it fits
    for size in (19, 17, 15, 13, 12):
        cur = wrap(cur_text, size, max_w, True, max_lines=99)
        lh = size + 7
        if len(cur) * lh <= area_h:
            break
    block = len(cur) * lh
    # neighbouring lines share what is left, whole when they fit, cut short otherwise
    ctx, ctx_lh = 13, 19
    room = max((area_h - block - 12) // 2, 0)
    rows = room // ctx_lh

    def context(i: int) -> list[str]:
        if rows < 1 or not (0 <= i < len(lines)) or not lines[i].text:
            return []
        return wrap(lines[i].text, ctx, max_w, max_lines=rows)

    prev, nxt = context(idx - 1), context(idx + 1)
    top = area_top + (area_h - block) // 2
    for j, ln in enumerate(prev):
        y = top - 6 - (len(prev) - j) * ctx_lh
        draw_text(d, (W - text_width(ln, ctx)) / 2, y, ln, ctx, fill=DIM)
    sung = _sung_chars(lines[idx], position_ms) if karaoke and idx >= 0 and lines[idx].words else None
    done = 0
    for i, ln in enumerate(cur):
        x, y = (W - text_width(ln, size, True)) / 2, top + i * lh
        if sung is None:
            draw_text(d, x, y, ln, size, True, WHITE)
        else:
            draw_text(d, x, y, ln, size, True, GREY)
            k = max(0.0, min(len(ln), sung - done))
            whole = int(k)
            cut = text_width(ln[:whole], size, True) + (text_width(ln[whole], size, True) * (k - whole) if whole < len(ln) else 0)
            if cut > 0:  # paint the sung part white, clipped to its width
                layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                draw_text(ImageDraw.Draw(layer), x, y, ln, size, True, WHITE)
                clip = Image.new("L", (W, H), 0)
                ImageDraw.Draw(clip).rectangle([x, y - 4, x + cut, y + lh + 2], fill=255)
                img.paste(layer, (0, 0), Image.composite(layer.getchannel("A"), clip, clip))
                d = ImageDraw.Draw(img)
            done += len(ln) + (1 if " " in cur_text else 0)
    for j, ln in enumerate(nxt):
        y = top + block + 6 + j * ctx_lh
        draw_text(d, (W - text_width(ln, ctx)) / 2, y, ln, ctx, fill=DIM)
    if lyrics.source == "lrcred":  # lrc.red's terms ask apps showing its sheets to credit it
        draw_text(d, PAD + 62, PAD + 44, "lyrics: lrc.red", 11, fill=DIM)  # header, under the artist
    _progress(d, np, position_ms)
    return _jpeg(img)
