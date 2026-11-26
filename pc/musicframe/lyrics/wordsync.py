"""Word-timed lyric formats: NetEase yrc, Kugou krc, Musixmatch richsync."""
import base64
import json
import re
import zlib

from ..model import Line, Word

_KRC_KEY = bytes([64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105])
_LINE = re.compile(r"^\[(\d+),(\d+)\](.*)$")


def _line(start: int, words: list[Word]) -> Line:
    return Line(start, "".join(w.text for w in words).strip(), words)


def parse_yrc(text: str) -> list[Line]:
    """'[1000,2000](1000,500,0)Hel(1500,500,0)lo' — word times are absolute ms."""
    out = []
    for raw in text.splitlines():
        m = _LINE.match(raw.strip())
        if not m:
            continue  # JSON credit lines start with '{'
        words = [Word(int(s), int(s) + int(d), w) for s, d, w in re.findall(r"\((\d+),(\d+),\d+\)([^(]*)", m.group(3))]
        if words:
            out.append(_line(int(m.group(1)), words))
    return out


def decode_krc(b64: str) -> str:
    raw = base64.b64decode(b64)[4:]  # drop 'krc1'
    return zlib.decompress(bytes(b ^ _KRC_KEY[i % 16] for i, b in enumerate(raw))).decode("utf-8")


def parse_krc(text: str) -> list[Line]:
    """'[2000,1000]<0,400,0>A<400,600,0>B' — word offsets are relative to the line start."""
    out = []
    for raw in text.splitlines():
        m = _LINE.match(raw.strip())
        if not m:
            continue
        start = int(m.group(1))
        words = [Word(start + int(o), start + int(o) + int(d), w) for o, d, w in re.findall(r"<(\d+),(\d+),\d+>([^<]*)", m.group(3))]
        if words:
            out.append(_line(start, words))
    return out


def parse_richsync(json_str: str) -> list[Line]:
    """Musixmatch: [{"ts":s,"te":s,"l":[{"c":chars,"o":offset_s}],"x":text}] — chars grouped into words on spaces."""
    out = []
    for item in json.loads(json_str):
        ts, te = float(item["ts"]), float(item["te"])
        parts = item.get("l", [])
        words: list[Word] = []
        cur = None
        for i, p in enumerate(parts):
            if p["c"].isspace():
                cur = None
                continue
            start = int((ts + float(p["o"])) * 1000)
            end = int((ts + float(parts[i + 1]["o"])) * 1000) if i + 1 < len(parts) else int(te * 1000)
            if cur is None:
                cur = Word(start, end, p["c"])
                words.append(cur)
            else:
                cur.end_ms, cur.text = end, cur.text + p["c"]
        if words:
            out.append(Line(int(ts * 1000), item.get("x", " ".join(w.text for w in words)), words))
    return out


def _ttml_time(t: str) -> int:
    """'11.933', '4:22.164' or '1:02:03.5' -> ms."""
    secs = 0.0
    for part in t.strip().rstrip("s").split(":"):
        secs = secs * 60 + float(part)
    return int(round(secs * 1000))


def parse_ttml(text: str) -> tuple[list[Line], bool]:
    """Apple-style TTML (lrc.red): <p begin> lines holding <span begin end> words. Returns (lines, word_timed).
    Background-vocal spans (ttm:role) are left out so the main line reads cleanly."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)
    tag = lambda el: el.tag.rsplit("}", 1)[-1]
    out, worded = [], False
    for p in root.iter():
        if tag(p) != "p" or "begin" not in p.attrib:
            continue
        words: list[Word] = []
        for sp in p:
            if tag(sp) != "span" or any(k.endswith("role") for k in sp.attrib) or "begin" not in sp.attrib:
                continue
            # the space between two words lives in the tail of the first span
            text_ = (sp.text or "") + (" " if sp.tail and not sp.tail.strip() else "")
            words.append(Word(_ttml_time(sp.attrib["begin"]), _ttml_time(sp.attrib.get("end", sp.attrib["begin"])), text_))
        if words:
            worded = True
            out.append(Line(words[0].start_ms, "".join(w.text for w in words).strip(), words))
        else:
            line_text = "".join(p.itertext()).strip()
            if line_text:
                out.append(Line(_ttml_time(p.attrib["begin"]), line_text))
    out.sort(key=lambda l: l.start_ms)
    return out, worded
