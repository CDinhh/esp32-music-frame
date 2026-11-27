"""Sonora-inspired lyric timing, spring physics, and PCHIP monotone sweep math."""
from dataclasses import dataclass, field
import math

from ..model import Line, Lyrics, Word

# Physical springs (stiffness, damping) from Sonora (crates/ui/src/motion.rs)
SCROLL_SPRING = (170.0, 23.0)
ROW_SPRING = (210.0, 22.0)
LAG_STAGGER = 0.35
LAG_TRAIL = 0.9
LAG_STALL = 0.064
LAG_LEAST = 0.15
SCROLL_REST_PX = 0.2
SCROLL_REST_VEL = 1.0
ROW_REST_VEL = 1.5

# Transitions (seconds)
TRANSITION_S = 0.22
KARAOKE_FADE_S = 0.16
INSTRUMENTAL_GAP_MS = 5000

# Karaoke pacing & expressive effects (from Sonora crates/views/src/chrome/aside.rs)
PACE_HEADROOM = 2.0
PACE_LEAST = 10.0
PACE_LAG_MS = 400
RISE_LEAST_MS = 1100
GLOW_SHORT_S = 0.9
GLOW_LONG_S = 1.8
GLOW_LONGEST_S = 2.6
GLOW_BASE = 0.25
GLOW_MOST = 0.35
GLOW_FADE_S = 0.7


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def ease_out_expo(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return 1.0 if t >= 1.0 else 1.0 - (2.0 ** (-10.0 * t))


def ease_out_cubic(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def mix_color(c0: tuple[int, int, int], c1: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = clamp(t, 0.0, 1.0)
    return (
        round(c0[0] + (c1[0] - c0[0]) * t),
        round(c0[1] + (c1[1] - c0[1]) * t),
        round(c0[2] + (c1[2] - c0[2]) * t),
    )


def step_spring(pos: float, vel: float, target: float, stiffness: float, damping: float, dt: float) -> tuple[float, float]:
    """Sub-stepped damped harmonic spring (mass=1) for stability across frame rates."""
    if dt <= 0.0:
        return pos, vel
    dt = min(dt, LAG_STALL)
    steps = max(1, math.ceil(dt / (1.0 / 120.0)))
    h = dt / steps
    for _ in range(steps):
        acc = -stiffness * (pos - target) - damping * vel
        vel += acc * h
        pos += vel * h
    return pos, vel


def lag_spring(along: float) -> tuple[float, float]:
    freq = 1.0 - LAG_STAGGER * clamp(along, 0.0, 1.0)
    return ROW_SPRING[0] * freq * freq, ROW_SPRING[1] * freq


def is_wide(ch: str) -> bool:
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF
        or 0x3400 <= o <= 0x4DBF
        or 0x4E00 <= o <= 0x9FFF
        or 0xAC00 <= o <= 0xD7AF
        or 0xF900 <= o <= 0xFAFF
    )


def separable(left: str, right: str) -> bool:
    """Whether two adjacent fragments belong to separate visual words (for cohesive word rise)."""
    if not left or not right:
        return True
    lc, rc = left[-1], right[0]
    return lc.isspace() or rc.isspace() or is_wide(lc) or is_wide(rc)


def own_sung_end(line: Line) -> int | None:
    if line.words:
        for w in reversed(line.words):
            if w.text.strip():
                return max(w.end_ms, w.start_ms, line.start_ms)
    return None


def karaoke_window(line_start_ms: int, words: list[Word], index: int) -> tuple[int, int]:
    w = words[index]
    start = min(line_start_ms, w.start_ms) if index == 0 else w.start_ms
    sung = max(w.end_ms, start)
    if index + 1 < len(words):
        nxt = words[index + 1].start_ms
        end = min(max(nxt, start), sung) if sung > start else max(nxt, start)
    else:
        end = sung
    return start, end


def _letters_in(w: Word) -> int:
    return max(len(w.text.strip()), 1)


def pace_limit(lines: list[Line]) -> float:
    """Fastest letters/sec allowed before smoothing out micro-timed word glitches."""
    paces: list[float] = []
    for line in lines:
        if not line.words:
            continue
        for i in range(len(line.words)):
            start, end = karaoke_window(line.start_ms, line.words, i)
            paces.append(_letters_in(line.words[i]) / max((end - start) / 1000.0, 0.01))
    if not paces:
        return math.inf
    paces.sort()
    median = paces[len(paces) // 2]
    return max(median * PACE_HEADROOM, PACE_LEAST)


def paced(windows: list[tuple[int, int]], words: list[Word], pace: float) -> list[tuple[int, int]]:
    if not math.isfinite(pace):
        return windows
    free = 0
    out: list[tuple[int, int]] = []
    for (start, end), w in zip(windows, words):
        begin = min(max(start, free), start + PACE_LAG_MS)
        least = round(_letters_in(w) * 1000.0 / pace)
        finish = max(min(begin + least, end + PACE_LAG_MS), end, begin)
        free = finish
        out.append((begin, finish))
    return out


def monotone(keys: list[tuple[float, float]], at: float) -> float:
    """PCHIP monotone cubic through non-decreasing (time, value) keyframes."""
    if not keys:
        return 0.0
    if at <= keys[0][0]:
        return 0.0
    if at >= keys[-1][0]:
        return keys[-1][1]
    seg = 0
    for i in range(len(keys) - 1):
        if at < keys[i + 1][0]:
            seg = i
            break

    def slope(idx: int) -> float:
        t0, x0 = keys[idx]
        t1, x1 = keys[idx + 1]
        return (x1 - x0) / max(t1 - t0, 1e-6)

    def tangent(idx: int) -> float:
        if idx == 0 or idx + 1 >= len(keys):
            return 0.0
        before, after = slope(idx - 1), slope(idx)
        if before <= 0.0 or after <= 0.0:
            return 0.0
        left = keys[idx][0] - keys[idx - 1][0]
        right = keys[idx + 1][0] - keys[idx][0]
        w1, w2 = 2.0 * right + left, right + 2.0 * left
        return (w1 + w2) / (w1 / before + w2 / after)

    t0, x0 = keys[seg]
    t1, x1 = keys[seg + 1]
    h = max(t1 - t0, 1e-6)
    s = clamp((at - t0) / h, 0.0, 1.0)
    s2 = s * s
    s3 = s2 * s
    return (
        (2.0 * s3 - 3.0 * s2 + 1.0) * x0
        + (s3 - 2.0 * s2 + s) * h * tangent(seg)
        + (-2.0 * s3 + 3.0 * s2) * x1
        + (s3 - s2) * h * tangent(seg + 1)
    )


def word_rise(start_ms: int, end_ms: int, pos_ms: int) -> float:
    held = max(end_ms - start_ms, 0)
    span = max(held, RISE_LEAST_MS)
    p = clamp((pos_ms - start_ms) / span, 0.0, 1.0)
    return 1.0 - (1.0 - p) ** 2


def word_glow(start_ms: int, end_ms: int, pos_ms: int) -> float:
    held_s = max(end_ms - start_ms, 0) / 1000.0
    weight = ease_in_out(clamp((held_s - GLOW_SHORT_S) / (GLOW_LONG_S - GLOW_SHORT_S), 0.0, 1.0))
    if weight <= 0.0:
        return 0.0
    fade_s = min(held_s * 0.5, GLOW_FADE_S)
    pos_s = (pos_ms - start_ms) / 1000.0
    gather = ease_in_out(pos_s / max(fade_s, 1e-4))
    gone = ease_in_out((pos_s - (held_s - fade_s)) / max(fade_s, 1e-4))
    longer = ease_in_out(clamp((held_s - GLOW_LONG_S) / (GLOW_LONGEST_S - GLOW_LONG_S), 0.0, 1.0))
    swell = ease_in_out((pos_s - fade_s) / max(held_s * 0.5 - fade_s, 1e-4))
    return (GLOW_BASE * weight + (GLOW_MOST - GLOW_BASE) * longer * swell) * gather * (1.0 - gone)


@dataclass
class TimedUnit:
    """A timed character/word slice in line.text, plus its visual rise group."""
    c0: int
    c1: int
    start_ms: int
    end_ms: int
    group: int


def timed_units(line: Line, pace: float) -> list[TimedUnit]:
    """Align line.words onto line.text, splitting unspaced wide words per character and grouping for rise."""
    words = line.words or []
    if not words or not line.text:
        return []
    raw_windows = [karaoke_window(line.start_ms, words, i) for i in range(len(words))]
    windows = paced(raw_windows, words, pace)

    text = line.text
    slices: list[tuple[int, int, int, int]] = []  # (c0, c1, start_ms, end_ms)
    cursor = 0
    matched = True
    for idx, w in enumerate(words):
        if not w.text:
            continue
        pos = text.find(w.text, cursor)
        if pos < 0:
            # Try trimmed text in case trailing space was stripped at line end
            stripped = w.text.strip()
            pos = text.find(stripped, cursor) if stripped else -1
            if pos < 0:
                matched = False
                break
            length = len(stripped)
        else:
            length = len(w.text)
        c0, c1 = pos, pos + length
        cursor = c1
        s_ms, e_ms = windows[idx]
        sub = text[c0:c1]
        if len(sub) > 1 and all(is_wide(ch) or ch.isspace() for ch in sub):
            # Split unspaced CJK/wide word evenly per character
            n = len(sub)
            span = max(e_ms - s_ms, 0)
            for k in range(n):
                u_s = s_ms + round(span * k / n)
                u_e = s_ms + round(span * (k + 1) / n)
                slices.append((c0 + k, c0 + k + 1, u_s, u_e))
        else:
            slices.append((c0, c1, s_ms, e_ms))

    if not matched:
        # Proportional fallback over line.text
        total_w = sum(max(len(w.text), 1) for w in words)
        slices = []
        c_pos = 0
        for idx, w in enumerate(words):
            share = max(len(w.text), 1) / max(total_w, 1)
            c1 = len(text) if idx == len(words) - 1 else min(len(text), c_pos + max(1, round(len(text) * share)))
            if c1 > c_pos:
                s_ms, e_ms = windows[idx]
                slices.append((c_pos, c1, s_ms, e_ms))
                c_pos = c1

    units: list[TimedUnit] = []
    group = 0
    for i, (c0, c1, s_ms, e_ms) in enumerate(slices):
        if i > 0:
            prev_c0, prev_c1, _, _ = slices[i - 1]
            # Separate rise group if there was a gap in text (spaces) or separable boundary
            if c0 > prev_c1 or separable(text[prev_c0:prev_c1], text[c0:c1]):
                group += 1
        units.append(TimedUnit(c0, c1, s_ms, e_ms, group))
    return units


@dataclass
class SheetItem:
    """One row in the scrollable lyrics sheet: a lyric line or an instrumental break."""
    kind: str  # "line" | "break"
    start_ms: int
    end_ms: int | None = None
    line_idx: int = -1
    line: Line | None = None


def build_sheet_items(lines: list[Line]) -> list[SheetItem]:
    items: list[SheetItem] = []
    if not lines:
        return items
    if lines[0].start_ms >= INSTRUMENTAL_GAP_MS:
        items.append(SheetItem("break", 0, lines[0].start_ms))
    for i, ln in enumerate(lines):
        if i > 0:
            prev_end = own_sung_end(lines[i - 1])
            if prev_end is not None and ln.start_ms - prev_end >= INSTRUMENTAL_GAP_MS:
                items.append(SheetItem("break", prev_end, ln.start_ms))
        if not ln.text.strip() or ln.text.strip() == "♪":
            end_ms = lines[i + 1].start_ms if i + 1 < len(lines) else ln.start_ms + INSTRUMENTAL_GAP_MS
            items.append(SheetItem("break", ln.start_ms, end_ms, i, ln))
        else:
            items.append(SheetItem("line", ln.start_ms, own_sung_end(ln), i, ln))
    return items


def active_sheet_row(items: list[SheetItem], pos_ms: int) -> int:
    if not items:
        return -1
    # Before first item
    if pos_ms < items[0].start_ms:
        return 0 if items[0].kind == "break" else -1
    active = -1
    for i, it in enumerate(items):
        if it.kind == "break":
            end = it.end_ms if it.end_ms is not None else it.start_ms
            if it.start_ms <= pos_ms < end:
                return i
        elif pos_ms >= it.start_ms:
            active = i
        else:
            break
    return active


@dataclass
class SheetAnimator:
    """Tracks scroll spring, row stagger lag, and line arrival/departure transitions."""
    lyrics_key: tuple | None = None
    active_row: int = -1
    departing_row: int | None = None
    arrived_at: float = 0.0
    departed_at: float = 0.0
    scroll_y: float = 0.0
    scroll_vel: float = 0.0
    goal_y: float = 0.0
    row_drifts: dict[int, list[float]] = field(default_factory=dict)
    last_time: float = 0.0
    settled: bool = True

    def reset(self) -> None:
        self.lyrics_key = None
        self.active_row = -1
        self.departing_row = None
        self.scroll_y = 0.0
        self.scroll_vel = 0.0
        self.goal_y = 0.0
        self.row_drifts.clear()
        self.last_time = 0.0
        self.settled = True

    def step(
        self,
        lyrics: Lyrics,
        items: list[SheetItem],
        layout,  # layout(animator) -> (row_tops, row_heights) for the current line sizes
        anchor_y: float,
        view_h: float,
        pos_ms: int,
        now_s: float,
        animate: bool = True,
    ) -> tuple[list[float], list[float]]:
        """Advance the motion; the row being sung is centred on anchor_y. Returns the layout."""
        key = (id(lyrics), lyrics.source, len(lyrics.lines), lyrics.lines[0].text if lyrics.lines else "")
        target_row = active_sheet_row(items, pos_ms)
        goal_idx = target_row if target_row >= 0 else 0

        def aim():
            tops, heights = layout(self)
            return tops, heights, (tops[goal_idx] + heights[goal_idx] * 0.5 - anchor_y if items else 0.0)

        is_new_sheet = key != self.lyrics_key
        jumped_far = self.active_row >= 0 and target_row >= 0 and abs(target_row - self.active_row) > 2

        if not animate or is_new_sheet or jumped_far or self.last_time == 0.0:
            self.lyrics_key = key
            self.active_row = target_row
            self.departing_row = None
            self.arrived_at = now_s - TRANSITION_S
            self.departed_at = now_s - TRANSITION_S
            row_tops, row_heights, goal_y = aim()
            self.scroll_y = goal_y
            self.scroll_vel = 0.0
            self.goal_y = goal_y
            self.row_drifts.clear()
            self.last_time = now_s
            self.settled = True
            return row_tops, row_heights

        dt = clamp(now_s - self.last_time, 0.0, LAG_STALL)
        self.last_time = now_s

        if target_row != self.active_row:
            if self.active_row >= 0:
                self.departing_row = self.active_row
                self.departed_at = now_s
            if target_row >= 0:
                self.arrived_at = now_s
            self.active_row = target_row

        if self.departing_row is not None and (now_s - self.departed_at) >= TRANSITION_S:
            self.departing_row = None

        row_tops, row_heights, goal_y = aim()
        self.goal_y = goal_y
        prev_scroll = self.scroll_y
        self.scroll_y, self.scroll_vel = step_spring(
            self.scroll_y, self.scroll_vel, goal_y, SCROLL_SPRING[0], SCROLL_SPRING[1], dt
        )
        scroll_step = self.scroll_y - prev_scroll
        downward = goal_y >= prev_scroll

        if abs(self.scroll_y - goal_y) < SCROLL_REST_PX and abs(self.scroll_vel) < SCROLL_REST_VEL:
            self.scroll_y = goal_y
            self.scroll_vel = 0.0

        # Cascading row lag (Sonora's iMessage-like stagger)
        most_lag = view_h * 0.28
        for r_idx in range(len(items)):
            screen_y = row_tops[r_idx] - self.scroll_y
            if screen_y + row_heights[r_idx] < -40 or screen_y > view_h + 40:
                self.row_drifts.pop(r_idx, None)
                continue
            along = clamp(screen_y / view_h, 0.0, 1.0)
            if not downward:
                along = 1.0 - along
            st = self.row_drifts.get(r_idx)
            if st is None:
                if abs(scroll_step) < 0.01:
                    continue
                st = [0.0, 0.0]
            st[0] = clamp(st[0] + scroll_step * LAG_TRAIL * along, -most_lag, most_lag)
            k, c = lag_spring(along)
            st[0], st[1] = step_spring(st[0], st[1], 0.0, k, c, dt)
            if abs(st[0]) < LAG_LEAST and abs(st[1]) < ROW_REST_VEL:
                self.row_drifts.pop(r_idx, None)
            else:
                self.row_drifts[r_idx] = st

        self.settled = (
            self.scroll_y == goal_y
            and self.scroll_vel == 0.0
            and not self.row_drifts
            and self.departing_row is None
            and (now_s - self.arrived_at) >= TRANSITION_S
        )
        return row_tops, row_heights

    def is_animating(self, items: list[SheetItem], pos_ms: int, now_s: float, karaoke: bool) -> bool:
        if not self.settled:
            return True
        if 0 <= self.active_row < len(items):
            it = items[self.active_row]
            if it.kind == "break":
                return True
            if karaoke and it.line and it.line.words:
                end_ms = own_sung_end(it.line) or it.line.start_ms
                if pos_ms <= end_ms + 800:
                    return True
        # Check if departing/recent line is still fading karaoke highlight
        if karaoke:
            for it in items:
                if it.kind == "line" and it.line and it.line.words and pos_ms >= it.line.start_ms:
                    end_ms = own_sung_end(it.line)
                    if end_ms is not None and pos_ms < end_ms + 400:
                        return True
        return False
