import bisect
import re

from ..model import Line

_STAMP = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str) -> list[Line]:
    """'[00:01.50][00:10.00]la' -> two lines; metadata tags like [ar:x] are skipped."""
    out = []
    for raw in text.splitlines():
        stamps = list(_STAMP.finditer(raw))
        if not stamps:
            continue
        lyric = raw[stamps[-1].end():].strip()
        for m in stamps:
            frac = m.group(3) or "0"
            ms = int(frac) * (10 ** (3 - len(frac)))
            out.append(Line(int(m.group(1)) * 60000 + int(m.group(2)) * 1000 + ms, lyric))
    out.sort(key=lambda l: l.start_ms)
    return out


def line_index(lines: list[Line], position_ms: int) -> int:
    """Index of the line being sung at position_ms; -1 before the first. Bisect, so seeking works both ways."""
    return bisect.bisect_right([l.start_ms for l in lines], position_ms) - 1
