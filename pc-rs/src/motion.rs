//! Sonora-inspired lyric timing, spring physics and monotone sweep math.
use crate::model::{Line, Word};
use std::collections::HashMap;

// Physical springs (stiffness, damping) from Sonora (crates/ui/src/motion.rs)
const SCROLL_SPRING: (f64, f64) = (170.0, 23.0);
const ROW_SPRING: (f64, f64) = (210.0, 22.0);
const TOP_REST_PX: f64 = 16.0; // the opening lines rest this far below the top instead of being centred
const LAG_STAGGER: f64 = 0.35;
const LAG_TRAIL: f64 = 0.9;
const LAG_STALL: f64 = 0.064;
const LAG_LEAST: f64 = 0.15;
const SCROLL_REST_PX: f64 = 0.2;
const SCROLL_REST_VEL: f64 = 1.0;
const ROW_REST_VEL: f64 = 1.5;

pub const TRANSITION_S: f64 = 0.22;
pub const KARAOKE_FADE_S: f64 = 0.16;
const INSTRUMENTAL_GAP_MS: i64 = 5000;

// Karaoke pacing and effects (Sonora crates/views/src/chrome/aside.rs)
const PACE_HEADROOM: f64 = 2.0;
const PACE_LEAST: f64 = 10.0;
const PACE_LAG_MS: i64 = 400;
const RISE_LEAST_MS: i64 = 1100;
const GLOW_SHORT_S: f64 = 0.9;
const GLOW_LONG_S: f64 = 1.8;
const GLOW_LONGEST_S: f64 = 2.6;
const GLOW_BASE: f64 = 0.25;
const GLOW_MOST: f64 = 0.35;
const GLOW_FADE_S: f64 = 0.7;

pub fn ease_out_expo(t: f64) -> f64 {
    let t = t.clamp(0.0, 1.0);
    if t >= 1.0 { 1.0 } else { 1.0 - 2f64.powf(-10.0 * t) }
}

pub fn ease_out_cubic(t: f64) -> f64 {
    1.0 - (1.0 - t.clamp(0.0, 1.0)).powi(3)
}

fn ease_in_out(t: f64) -> f64 {
    let t = t.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// Sub-stepped damped spring (mass 1), stable across frame rates.
fn step_spring(mut pos: f64, mut vel: f64, target: f64, stiffness: f64, damping: f64, dt: f64) -> (f64, f64) {
    if dt <= 0.0 {
        return (pos, vel);
    }
    let dt = dt.min(LAG_STALL);
    let steps = ((dt / (1.0 / 120.0)).ceil() as u32).max(1);
    let h = dt / steps as f64;
    for _ in 0..steps {
        vel += (-stiffness * (pos - target) - damping * vel) * h;
        pos += vel * h;
    }
    (pos, vel)
}

fn lag_spring(along: f64) -> (f64, f64) {
    let freq = 1.0 - LAG_STAGGER * along.clamp(0.0, 1.0);
    (ROW_SPRING.0 * freq * freq, ROW_SPRING.1 * freq)
}

fn is_wide(ch: char) -> bool {
    matches!(ch as u32, 0x3040..=0x30FF | 0x3400..=0x4DBF | 0x4E00..=0x9FFF | 0xAC00..=0xD7AF | 0xF900..=0xFAFF)
}

/// Whether two adjacent fragments belong to separate visual words (for cohesive word rise).
fn separable(left: &[char], right: &[char]) -> bool {
    match (left.last(), right.first()) {
        (Some(&l), Some(&r)) => l.is_whitespace() || r.is_whitespace() || is_wide(l) || is_wide(r),
        _ => true,
    }
}

pub fn own_sung_end(line: &Line) -> Option<i64> {
    let w = line.words.as_ref()?.iter().rev().find(|w| !w.text.trim().is_empty())?;
    Some(w.end_ms.max(w.start_ms).max(line.start_ms))
}

fn karaoke_window(line_start_ms: i64, words: &[Word], index: usize) -> (i64, i64) {
    let w = &words[index];
    let start = if index == 0 { line_start_ms.min(w.start_ms) } else { w.start_ms };
    let sung = w.end_ms.max(start);
    let end = match words.get(index + 1) {
        Some(next) if sung > start => next.start_ms.max(start).min(sung),
        Some(next) => next.start_ms.max(start),
        None => sung,
    };
    (start, end)
}

fn letters_in(w: &Word) -> usize {
    w.text.trim().chars().count().max(1)
}

/// Fastest letters/second allowed before micro-timed word glitches are smoothed out.
pub fn pace_limit(lines: &[Line]) -> f64 {
    let mut paces = Vec::new();
    for line in lines {
        let Some(words) = &line.words else { continue };
        for i in 0..words.len() {
            let (start, end) = karaoke_window(line.start_ms, words, i);
            paces.push(letters_in(&words[i]) as f64 / ((end - start) as f64 / 1000.0).max(0.01));
        }
    }
    if paces.is_empty() {
        return f64::INFINITY;
    }
    paces.sort_by(f64::total_cmp);
    (paces[paces.len() / 2] * PACE_HEADROOM).max(PACE_LEAST)
}

fn paced(windows: Vec<(i64, i64)>, words: &[Word], pace: f64) -> Vec<(i64, i64)> {
    if !pace.is_finite() {
        return windows;
    }
    let mut free = 0;
    windows
        .into_iter()
        .zip(words)
        .map(|((start, end), w)| {
            let begin = start.max(free).min(start + PACE_LAG_MS);
            let least = (letters_in(w) as f64 * 1000.0 / pace).round() as i64;
            let finish = (begin + least).min(end + PACE_LAG_MS).max(end).max(begin);
            free = finish;
            (begin, finish)
        })
        .collect()
}

/// PCHIP monotone cubic through non-decreasing (time, value) keyframes.
pub fn monotone(keys: &[(f64, f64)], at: f64) -> f64 {
    let (Some(first), Some(last)) = (keys.first(), keys.last()) else { return 0.0 };
    if at <= first.0 {
        return 0.0;
    }
    if at >= last.0 {
        return last.1;
    }
    let seg = (0..keys.len() - 1).find(|&i| at < keys[i + 1].0).unwrap_or(0);
    let slope = |i: usize| (keys[i + 1].1 - keys[i].1) / (keys[i + 1].0 - keys[i].0).max(1e-6);
    let tangent = |i: usize| {
        if i == 0 || i + 1 >= keys.len() {
            return 0.0;
        }
        let (before, after) = (slope(i - 1), slope(i));
        if before <= 0.0 || after <= 0.0 {
            return 0.0;
        }
        let (left, right) = (keys[i].0 - keys[i - 1].0, keys[i + 1].0 - keys[i].0);
        let (w1, w2) = (2.0 * right + left, right + 2.0 * left);
        (w1 + w2) / (w1 / before + w2 / after)
    };
    let ((t0, x0), (t1, x1)) = (keys[seg], keys[seg + 1]);
    let h = (t1 - t0).max(1e-6);
    let s = ((at - t0) / h).clamp(0.0, 1.0);
    let (s2, s3) = (s * s, s * s * s);
    (2.0 * s3 - 3.0 * s2 + 1.0) * x0
        + (s3 - 2.0 * s2 + s) * h * tangent(seg)
        + (-2.0 * s3 + 3.0 * s2) * x1
        + (s3 - s2) * h * tangent(seg + 1)
}

pub fn word_rise(start_ms: i64, end_ms: i64, pos_ms: i64) -> f64 {
    let span = (end_ms - start_ms).max(0).max(RISE_LEAST_MS);
    let p = ((pos_ms - start_ms) as f64 / span as f64).clamp(0.0, 1.0);
    1.0 - (1.0 - p).powi(2)
}

pub fn word_glow(start_ms: i64, end_ms: i64, pos_ms: i64) -> f64 {
    let held = (end_ms - start_ms).max(0) as f64 / 1000.0;
    let weight = ease_in_out((held - GLOW_SHORT_S) / (GLOW_LONG_S - GLOW_SHORT_S));
    if weight <= 0.0 {
        return 0.0;
    }
    let fade = (held * 0.5).min(GLOW_FADE_S);
    let pos = (pos_ms - start_ms) as f64 / 1000.0;
    let gather = ease_in_out(pos / fade.max(1e-4));
    let gone = ease_in_out((pos - (held - fade)) / fade.max(1e-4));
    let longer = ease_in_out((held - GLOW_LONG_S) / (GLOW_LONGEST_S - GLOW_LONG_S));
    let swell = ease_in_out((pos - fade) / (held * 0.5 - fade).max(1e-4));
    (GLOW_BASE * weight + (GLOW_MOST - GLOW_BASE) * longer * swell) * gather * (1.0 - gone)
}

/// A timed slice of the line text (character offsets) plus its visual rise group.
#[derive(Debug, Clone, PartialEq)]
pub struct TimedUnit {
    pub c0: usize,
    pub c1: usize,
    pub start_ms: i64,
    pub end_ms: i64,
    pub group: usize,
}

fn find(text: &[char], needle: &[char], from: usize) -> Option<usize> {
    if needle.is_empty() || needle.len() > text.len() {
        return None;
    }
    (from..=text.len() - needle.len()).find(|&i| text[i..i + needle.len()] == *needle)
}

/// Align the words onto the line text, splitting unspaced wide words per character and grouping for rise.
pub fn timed_units(line: &Line, pace: f64) -> Vec<TimedUnit> {
    let words = line.words.as_deref().unwrap_or(&[]);
    let text: Vec<char> = line.text.chars().collect();
    if words.is_empty() || text.is_empty() {
        return Vec::new();
    }
    let windows = paced((0..words.len()).map(|i| karaoke_window(line.start_ms, words, i)).collect(), words, pace);

    let mut slices: Vec<(usize, usize, i64, i64)> = Vec::new();
    let (mut cursor, mut matched) = (0, true);
    for (w, &(s_ms, e_ms)) in words.iter().zip(&windows) {
        if w.text.is_empty() {
            continue;
        }
        let whole: Vec<char> = w.text.chars().collect();
        let trimmed: Vec<char> = w.text.trim().chars().collect(); // a trailing space may be gone at line end
        let Some((c0, len)) = find(&text, &whole, cursor)
            .map(|p| (p, whole.len()))
            .or_else(|| find(&text, &trimmed, cursor).map(|p| (p, trimmed.len())))
        else {
            matched = false;
            break;
        };
        let c1 = c0 + len;
        cursor = c1;
        if len > 1 && text[c0..c1].iter().all(|&c| is_wide(c) || c.is_whitespace()) {
            let span = (e_ms - s_ms).max(0) as f64;
            for k in 0..len {
                let at = |k: usize| s_ms + (span * k as f64 / len as f64).round() as i64;
                slices.push((c0 + k, c0 + k + 1, at(k), at(k + 1)));
            }
        } else {
            slices.push((c0, c1, s_ms, e_ms));
        }
    }

    if !matched {
        // proportional fallback over the line text
        let total: usize = words.iter().map(|w| w.text.chars().count().max(1)).sum();
        slices.clear();
        let mut pos = 0;
        for (i, w) in words.iter().enumerate() {
            let share = w.text.chars().count().max(1) as f64 / total.max(1) as f64;
            let c1 = if i == words.len() - 1 {
                text.len()
            } else {
                text.len().min(pos + ((text.len() as f64 * share).round() as usize).max(1))
            };
            if c1 > pos {
                slices.push((pos, c1, windows[i].0, windows[i].1));
                pos = c1;
            }
        }
    }

    let mut group = 0;
    let mut units = Vec::with_capacity(slices.len());
    for (i, &(c0, c1, start_ms, end_ms)) in slices.iter().enumerate() {
        if i > 0 {
            let (p0, p1, ..) = slices[i - 1];
            if c0 > p1 || separable(&text[p0..p1], &text[c0..c1]) {
                group += 1;
            }
        }
        units.push(TimedUnit { c0, c1, start_ms, end_ms, group });
    }
    units
}

/// One row of the scrolling sheet: a lyric line or an instrumental break.
#[derive(Debug, Clone)]
pub struct SheetItem {
    pub is_break: bool,
    pub start_ms: i64,
    pub end_ms: Option<i64>,
    /// index into the lyric lines
    pub line: Option<usize>,
}

pub fn build_sheet_items(lines: &[Line]) -> Vec<SheetItem> {
    let mut items = Vec::new();
    let Some(first) = lines.first() else { return items };
    let gap = |start_ms, end_ms| SheetItem { is_break: true, start_ms, end_ms: Some(end_ms), line: None };
    if first.start_ms >= INSTRUMENTAL_GAP_MS {
        items.push(gap(0, first.start_ms));
    }
    for (i, ln) in lines.iter().enumerate() {
        if i > 0 {
            if let Some(prev) = own_sung_end(&lines[i - 1]) {
                if ln.start_ms - prev >= INSTRUMENTAL_GAP_MS {
                    items.push(gap(prev, ln.start_ms));
                }
            }
        }
        let t = ln.text.trim();
        if t.is_empty() || t == "♪" {
            let end = lines.get(i + 1).map_or(ln.start_ms + INSTRUMENTAL_GAP_MS, |n| n.start_ms);
            items.push(SheetItem { is_break: true, start_ms: ln.start_ms, end_ms: Some(end), line: Some(i) });
        } else {
            items.push(SheetItem { is_break: false, start_ms: ln.start_ms, end_ms: own_sung_end(ln), line: Some(i) });
        }
    }
    items
}

fn active_sheet_row(items: &[SheetItem], pos_ms: i64) -> Option<usize> {
    let first = items.first()?;
    if pos_ms < first.start_ms {
        return first.is_break.then_some(0);
    }
    let mut active = None;
    for (i, it) in items.iter().enumerate() {
        if it.is_break {
            if it.start_ms <= pos_ms && pos_ms < it.end_ms.unwrap_or(it.start_ms) {
                return Some(i);
            }
        } else if pos_ms >= it.start_ms {
            active = Some(i);
        } else {
            break;
        }
    }
    active
}

/// Row tops and row heights for the current line sizes.
pub type Layout = (Vec<f64>, Vec<f64>);

/// Scroll spring, row stagger lag and line arrival/departure transitions.
#[derive(Default)]
pub struct SheetAnimator {
    lyrics_id: u64,
    pub active_row: Option<usize>,
    pub departing_row: Option<usize>,
    pub arrived_at: f64,
    pub departed_at: f64,
    pub scroll_y: f64,
    scroll_vel: f64,
    /// row -> [offset, velocity]
    pub row_drifts: HashMap<usize, [f64; 2]>,
    last_time: Option<f64>,
    pub settled: bool,
}

impl SheetAnimator {
    pub fn new() -> Self {
        Self { settled: true, ..Default::default() }
    }

    /// Advance the motion; the row being sung is centred on `anchor_y`. Returns the layout.
    #[allow(clippy::too_many_arguments)]
    pub fn step(
        &mut self,
        lyrics_id: u64,
        items: &[SheetItem],
        layout: &dyn Fn(&SheetAnimator) -> Layout,
        anchor_y: f64,
        view_h: f64,
        pos_ms: i64,
        now_s: f64,
        animate: bool,
    ) -> Layout {
        let target = active_sheet_row(items, pos_ms);
        let goal_idx = target.unwrap_or(0);
        let aim = |a: &SheetAnimator| {
            let (tops, heights) = layout(a);
            let centred = if items.is_empty() { 0.0 } else { tops[goal_idx] + heights[goal_idx] * 0.5 - anchor_y };
            (tops, heights, centred.max(-TOP_REST_PX))
        };
        let new_sheet = lyrics_id != self.lyrics_id;
        let jumped = matches!((self.active_row, target), (Some(a), Some(t)) if a.abs_diff(t) > 2);

        let last = match self.last_time {
            Some(t) if animate && !new_sheet && !jumped => t,
            _ => {
                self.lyrics_id = lyrics_id;
                self.active_row = target;
                self.departing_row = None;
                self.arrived_at = now_s - TRANSITION_S;
                self.departed_at = now_s - TRANSITION_S;
                let (tops, heights, goal) = aim(self);
                self.scroll_y = goal;
                self.scroll_vel = 0.0;
                self.row_drifts.clear();
                self.last_time = Some(now_s);
                self.settled = true;
                return (tops, heights);
            }
        };

        let dt = (now_s - last).clamp(0.0, LAG_STALL);
        self.last_time = Some(now_s);

        if target != self.active_row {
            if self.active_row.is_some() {
                self.departing_row = self.active_row;
                self.departed_at = now_s;
            }
            if target.is_some() {
                self.arrived_at = now_s;
            }
            self.active_row = target;
        }
        if self.departing_row.is_some() && now_s - self.departed_at >= TRANSITION_S {
            self.departing_row = None;
        }

        let (tops, heights, goal) = aim(self);
        let prev = self.scroll_y;
        (self.scroll_y, self.scroll_vel) =
            step_spring(self.scroll_y, self.scroll_vel, goal, SCROLL_SPRING.0, SCROLL_SPRING.1, dt);
        let scroll_step = self.scroll_y - prev;
        let downward = goal >= prev;

        if (self.scroll_y - goal).abs() < SCROLL_REST_PX && self.scroll_vel.abs() < SCROLL_REST_VEL {
            self.scroll_y = goal;
            self.scroll_vel = 0.0;
        }

        // cascading row lag (Sonora's iMessage-like stagger)
        let most_lag = view_h * 0.28;
        for r in 0..items.len() {
            let screen_y = tops[r] - self.scroll_y;
            if screen_y + heights[r] < -40.0 || screen_y > view_h + 40.0 {
                self.row_drifts.remove(&r);
                continue;
            }
            let mut along = (screen_y / view_h).clamp(0.0, 1.0);
            if !downward {
                along = 1.0 - along;
            }
            let [mut offset, vel] = match self.row_drifts.get(&r) {
                Some(st) => *st,
                None if scroll_step.abs() < 0.01 => continue,
                None => [0.0, 0.0],
            };
            offset = (offset + scroll_step * LAG_TRAIL * along).clamp(-most_lag, most_lag);
            let (k, c) = lag_spring(along);
            let (offset, vel) = step_spring(offset, vel, 0.0, k, c, dt);
            if offset.abs() < LAG_LEAST && vel.abs() < ROW_REST_VEL {
                self.row_drifts.remove(&r);
            } else {
                self.row_drifts.insert(r, [offset, vel]);
            }
        }

        self.settled = self.scroll_y == goal
            && self.scroll_vel == 0.0
            && self.row_drifts.is_empty()
            && self.departing_row.is_none()
            && now_s - self.arrived_at >= TRANSITION_S;
        (tops, heights)
    }

    pub fn is_animating(&self, lines: &[Line], items: &[SheetItem], pos_ms: i64, karaoke: bool) -> bool {
        if !self.settled {
            return true;
        }
        if let Some(it) = self.active_row.and_then(|r| items.get(r)) {
            if it.is_break {
                return true;
            }
            if let Some(line) = it.line.map(|i| &lines[i]).filter(|l| karaoke && l.words.is_some()) {
                if pos_ms <= own_sung_end(line).unwrap_or(line.start_ms) + 800 {
                    return true;
                }
            }
        }
        // a departing line may still be fading its karaoke highlight
        karaoke
            && items
                .iter()
                .filter(|it| !it.is_break)
                .filter_map(|it| it.line.map(|i| &lines[i]))
                .any(|l| pos_ms >= l.start_ms && own_sung_end(l).is_some_and(|end| pos_ms < end + 400))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn monotone_never_overshoots() {
        let keys = [(0.0, 0.0), (1.0, 50.0), (2.0, 50.0), (3.0, 100.0)];
        assert_eq!(monotone(&keys, -0.5), 0.0);
        assert_eq!(monotone(&keys, 3.5), 100.0);
        assert_eq!(monotone(&keys, 1.5), 50.0);
        assert!(monotone(&keys, 0.5) > 0.0 && monotone(&keys, 0.5) < 50.0);
        assert_eq!(monotone(&[], 1.0), 0.0);
    }

    #[test]
    fn timed_units_split_cjk_and_keep_latin_words() {
        let cjk = Line::worded(1000, "愛してる", vec![Word::new(1000, 2500, "愛してる")]);
        let u = timed_units(&cjk, 20.0);
        assert_eq!(u.len(), 4);
        assert_eq!((u[0].start_ms, u[3].end_ms), (1000, 2500));
        assert_eq!(u.iter().map(|u| u.group).collect::<Vec<_>>(), [0, 1, 2, 3]);
        let words = [(1000, 1200, "H"), (1200, 1400, "i"), (1400, 1500, " "), (1500, 1700, "y"), (1700, 2000, "o")];
        let lat = Line::worded(1000, "Hi yo", words.iter().map(|&(a, b, t)| Word::new(a, b, t)).collect());
        let u = timed_units(&lat, 20.0);
        assert_eq!(u[0].group, u[1].group);
        assert!(u[3].group == u[4].group && u[3].group > u[0].group);
    }

    #[test]
    fn rows_trail_behind_the_scroll() {
        let lines: Vec<Line> = (0..8).map(|i| Line::new(i * 1000, "x")).collect();
        let items = build_sheet_items(&lines);
        let mut a = SheetAnimator::new();
        let layout = |_: &SheetAnimator| ((0..8).map(|i| i as f64 * 30.0).collect(), vec![20.0; 8]);
        for (pos, now) in [(2100, 1.0), (3100, 1.03), (3100, 1.06)] {
            a.step(7, &items, &layout, 68.0, 136.0, pos, now, true);
        }
        assert!(!a.row_drifts.is_empty() && a.row_drifts.values().all(|d| d[0] >= 0.0));
    }
}
