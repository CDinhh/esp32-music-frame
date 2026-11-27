//! Draws the whole 240x240 music screen; only changed parts are sent to the ESP32.
use crate::img::{rounded_rect, Filter, Gray, Rgb};
use crate::model::{Line, Lyrics, NowPlaying};
use crate::motion::{
    build_sheet_items, ease_out_cubic, ease_out_expo, monotone, own_sung_end, pace_limit, timed_units, word_glow,
    word_rise, Layout, SheetAnimator, SheetItem, KARAOKE_FADE_S, TRANSITION_S,
};
use crate::text;
use std::collections::HashMap;
use std::rc::Rc;

pub const W: usize = 240;
pub const H: usize = 240;
const PAD: usize = 14;
const WHITE: [u8; 3] = [255, 255, 255];
const GREY: [u8; 3] = [170, 176, 196];
const DIM: [u8; 3] = [110, 116, 136];

// Sonora's text opacities and haze
const QUIET: f64 = 0.56;
const PAST: f64 = QUIET * 0.40;
const AHEAD: f64 = QUIET * 0.60;
const VEIL: f64 = 0.30;

// Lyric viewport between the header and the timeline; the line being sung sits in its middle
pub const LYRICS_Y: usize = 76;
pub const LYRICS_H: usize = 136;
const ANCHOR_Y: f64 = LYRICS_H as f64 / 2.0;
const EDGE_FADE_PX: f64 = 16.0;
const CONTEXT_SCALE: f64 = 0.7; // the other lines rest at 14 px, the one being sung is 20 px
const VERSE_GAP: f64 = 12.0;
const BREAK_H: usize = 20;
const BASE_SIZES: [f64; 5] = [20.0, 18.0, 16.0, 14.0, 12.0];
const HAZE_EXP: f64 = 0.45;
const HAZE_REACH: f64 = LYRICS_H as f64;
const BLUR_RATIO: f64 = 0.13;
const HAZE_STEP: f64 = 0.25;
const HAZE_LEAST: f64 = 0.30;
const SS: f64 = 2.0; // lyrics are drawn twice as large and scaled down: even spacing, smooth sub-pixel motion
const EDGE: usize = 12; // transparent margin around a supersampled block

// Timeline
const ACCENT: [u8; 3] = [124, 199, 255]; // light blue, the same as the web panel
const BARS_Y: f64 = 225.0;
pub const TIMELINE_Y: usize = 212; // everything below is streamed on its own
const RAIL: [u8; 3] = [112, 118, 140];
const WAVE_LEN: f64 = 16.0;
const WAVE_AMP: f64 = 2.8;
const WAVE_SPEED: f64 = 18.0;

fn draw_rgb(img: &mut Rgb, x: f64, y: f64, s: &str, size: f64, bold: bool, color: [u8; 3]) {
    text::draw(x, y, s, size, bold, |px, py, c| img.blend_pixel(px, py, color, c));
}

fn draw_gray(img: &mut Gray, x: f64, y: f64, s: &str, size: f64, bold: bool, value: u8) {
    text::draw(x, y, s, size, bold, |px, py, c| img.blend_pixel(px, py, value, c));
}

fn clock(ms: i64) -> String {
    let s = ms.max(0) / 1000;
    if s >= 3600 {
        format!("{}:{:02}:{:02}", s / 3600, s / 60 % 60, s % 60)
    } else {
        format!("{}:{:02}", s / 60, s % 60)
    }
}

/// Video thumbnails come 16:9, often with black bars: drop the bars, then crop the middle square (never squash).
fn square_cover(data: &[u8], size: usize) -> Option<Rgb> {
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(4096);
    limits.max_image_height = Some(4096);
    let mut reader = image::ImageReader::new(std::io::Cursor::new(data)).with_guessed_format().ok()?;
    reader.limits(limits);
    let decoded = reader.decode().ok()?.to_rgb8();
    let (w, h) = (decoded.width() as usize, decoded.height() as usize);
    if w == 0 || h == 0 {
        return None;
    }
    let img = Rgb { w, h, px: decoded.into_raw() };
    let (mut x0, mut y0, mut x1, mut y1) = (w, h, 0, 0);
    for y in 0..h {
        for x in 0..w {
            let [r, g, b] = img.get(x, y);
            if (r as u32 * 299 + g as u32 * 587 + b as u32 * 114) / 1000 > 24 {
                (x0, y0, x1, y1) = (x0.min(x), y0.min(y), x1.max(x + 1), y1.max(y + 1));
            }
        }
    }
    let (bx, by, bw, bh) = if x1 > x0 && (x1 - x0) * (y1 - y0) * 10 > w * h * 3 {
        (x0, y0, x1 - x0, y1 - y0)
    } else {
        (0, 0, w, h)
    };
    let side = bw.min(bh) as f64;
    let left = bx as f64 + (bw as f64 - side) / 2.0;
    let top = by as f64 + (bh as f64 - side) / 2.0;
    Some(img.resample([left, top, left + side, top + side], size, size, Filter::Lanczos))
}

fn background(cover: Option<&[u8]>) -> Rgb {
    match cover.and_then(|d| square_cover(d, W)) {
        Some(mut bg) => {
            bg.blur(18.0);
            bg.darken(1.0 - 0.62);
            bg
        }
        None => Rgb::new(W, H, [14, 18, 34]),
    }
}

/// The cover (or a ♪ placeholder) as a rounded square at (x, y).
fn paste_cover(img: &mut Rgb, cover: Option<&[u8]>, x: usize, y: usize, size: usize, r: f64) -> bool {
    match cover.and_then(|d| square_cover(d, size)) {
        Some(c) => {
            img.paste_rounded(&c, x, y, r);
            true
        }
        None => {
            let boxed = [x as f64, y as f64, (x + size) as f64, (y + size) as f64];
            img.fill_shape(boxed, [32, 38, 60], 1.0, |px, py| rounded_rect(px, py, boxed, r));
            false
        }
    }
}

/// (elapsed text, remaining text, left and right end of the track)
fn span(np: &NowPlaying, pos: i64) -> (String, String, f64, f64) {
    let dur = np.duration_ms;
    let at = if dur > 0 { pos.min(dur) } else { pos };
    let left = clock(at);
    let right = if dur > 0 { format!("-{}", clock(dur - at)) } else { String::new() };
    let widest = if dur > 0 { clock(dur) } else { "0:00".into() }; // fixed ends: the track never jumps
    let x0 = PAD as f64 + text::width(&widest, 11.0, true).ceil() + 9.0;
    let x1 = (W - PAD) as f64 - text::width(&format!("-{widest}"), 11.0, true).ceil() - 9.0;
    (left, right, x0, x1)
}

/// "1:23 ~~~~~o-------- -2:59": the played part is a flowing wave, the rest a plain rail.
fn progress(img: &mut Rgb, np: &NowPlaying, pos: i64, t: f64) {
    let (left, right, x0, x1) = span(np, pos);
    draw_rgb(img, PAD as f64, BARS_Y - 8.0, &left, 11.0, true, WHITE);
    draw_rgb(img, (W - PAD) as f64 - text::width(&right, 11.0, true), BARS_Y - 8.0, &right, 11.0, true, WHITE);

    let part = if np.duration_ms > 0 { (pos.max(0) as f64 / np.duration_ms as f64).min(1.0) } else { 0.0 };
    let done = (x1 - x0) * part;
    let head = x0 + done;
    let boxed = |a: f64, b: f64| [a - 7.0, BARS_Y - 8.0, b + 7.0, BARS_Y + 8.0];
    let dot = |cx: f64, cy: f64, r: f64| move |x: f64, y: f64| ((x - cx).powi(2) + (y - cy).powi(2)).sqrt() - r;

    // the time still to play
    img.fill_shape(boxed(head, x1), RAIL, 1.0, |x, y| rounded_rect(x, y, [head, BARS_Y - 2.0, x1, BARS_Y + 2.0], 0.0));
    img.fill_shape(boxed(x1, x1), RAIL, 1.0, dot(x1, BARS_Y, 2.0));

    // the wave: a point every half pixel, flat where it leaves the start and meets the knob
    let points: Vec<(f64, f64)> = (0..=(done * 2.0) as usize)
        .map(|i| {
            let x = i as f64 / 2.0;
            let ease = (x / 8.0).min((done - x) / 8.0).clamp(0.0, 1.0);
            let y = ((x - t * WAVE_SPEED) * std::f64::consts::TAU / WAVE_LEN).sin() * WAVE_AMP * ease;
            (x0 + x, BARS_Y + y)
        })
        .collect();
    if points.len() > 1 {
        img.fill_shape(boxed(x0, head), ACCENT, 1.0, |x, y| {
            let mid = ((x - x0) * 2.0).round() as isize;
            let from = (mid - 8).clamp(0, points.len() as isize - 1) as usize;
            let to = (mid + 8).clamp(0, points.len() as isize - 1) as usize;
            points[from..=to].iter().map(|&(px, py)| (x - px).powi(2) + (y - py).powi(2)).fold(f64::MAX, f64::min).sqrt() - 1.5
        });
    }
    img.fill_shape(boxed(head, head), ACCENT, 110.0 / 255.0, dot(head, BARS_Y, 5.5)); // halo
    img.fill_shape(boxed(head, head), WHITE, 1.0, dot(head, BARS_Y, 4.0));
}

/// No lyrics: big cover, title, artist, progress.
fn basic_screen(back: &Rgb, np: &NowPlaying, cover: Option<&[u8]>) -> Rgb {
    let mut img = back.clone();
    let size = 138;
    let x0 = (W - size) / 2;
    if !paste_cover(&mut img, cover, x0, 14, size, 12.0) {
        let nx = W as f64 / 2.0 - text::width("♪", 54.0, false) / 2.0;
        draw_rgb(&mut img, nx, 50.0, "♪", 54.0, false, DIM);
    }
    let max = (W - 2 * PAD) as f64;
    let centred = |img: &mut Rgb, s: &str, y: f64, size: f64, bold: bool, color| {
        let x = (W as f64 - text::width(s, size, bold)) / 2.0;
        draw_rgb(img, x, y, s, size, bold, color);
    };
    centred(&mut img, &text::fit(&np.title, 18.0, max, true), 160.0, 18.0, true, WHITE);
    centred(&mut img, &text::fit(&np.artist, 14.0, max, false), 188.0, 14.0, false, GREY);
    img
}

/// Background, header and optional credit: the static pixels of the lyric screen.
fn lyric_base(back: &Rgb, np: &NowPlaying, cover: Option<&[u8]>, source: &str) -> Rgb {
    let mut img = back.clone();
    paste_cover(&mut img, cover, PAD, PAD, 52, 8.0);
    let x = (PAD + 62) as f64;
    let max = (W - PAD) as f64 - x;
    draw_rgb(&mut img, x, (PAD + 4) as f64, &text::fit(&np.title, 16.0, max, true), 16.0, true, WHITE);
    draw_rgb(&mut img, x, (PAD + 28) as f64, &text::fit(&np.artist, 13.0, max, false), 13.0, false, GREY);
    if source == "lrcred" {
        draw_rgb(&mut img, x, (PAD + 44) as f64, "lyrics: lrc.red", 11.0, false, DIM);
    }
    img
}

/// 0 on the line being sung, rising as |travel / reach| ^ 0.45 (Sonora's viewport haze).
fn viewport_softness(row_centre_y: f64) -> f64 {
    ((row_centre_y - ANCHOR_Y).abs() / HAZE_REACH).clamp(0.0, 1.0).powf(HAZE_EXP)
}

fn quantized_blur(size: f64, softness: f64) -> f64 {
    let q = (size * BLUR_RATIO * softness / HAZE_STEP).round() * HAZE_STEP;
    if q > HAZE_LEAST {
        q
    } else {
        0.0
    }
}

/// One timed slice mapped onto pixel positions inside a wrapped row.
struct Key {
    x0: f64,
    x1: f64,
    start_ms: i64,
    end_ms: i64,
}

struct Rise {
    x0: f64,
    x1: f64,
    start_ms: i64,
    end_ms: i64,
}

struct Plan {
    size: f64,
    lh: usize,
    rows: Vec<String>,
    row_widths: Vec<f64>,
    keys: Vec<Vec<Key>>,
    rises: Vec<Vec<Rise>>,
}

fn layout_line(line: &Line, pace: f64, max_w: f64) -> Plan {
    let units = if line.words.is_some() { timed_units(line, pace) } else { Vec::new() };
    let width = |s: &str, size: f64| text::width(s, size * SS, true) / SS; // measured at the size it is drawn
    let (mut size, mut lh, mut wrapped) = (BASE_SIZES[0], 0, Vec::new());
    for &s in &BASE_SIZES {
        size = s;
        wrapped = text::wrap(&line.text, s, max_w, true);
        lh = s as usize + 6;
        if wrapped.len() * lh <= LYRICS_H - 8 {
            break;
        }
    }
    let chars: Vec<char> = line.text.chars().collect();
    let slice = |a: usize, b: usize| chars[a.min(chars.len())..b.min(chars.len())].iter().collect::<String>();

    let mut plan = Plan {
        size,
        lh,
        rows: Vec::new(),
        row_widths: Vec::new(),
        keys: Vec::new(),
        rises: Vec::new(),
    };
    for row in wrapped {
        let mut keys = Vec::new();
        let mut groups: Vec<(usize, usize, usize, i64, i64)> = Vec::new(); // group, c0, c1, start, end
        for u in &units {
            let (oc0, oc1) = (u.c0.max(row.c0), u.c1.min(row.c1));
            if oc0 >= oc1 {
                continue;
            }
            let (lc0, lc1) = (oc0 - row.c0, oc1 - row.c0);
            let x0 = width(&slice(row.c0, oc0), size);
            let x1 = width(&slice(row.c0, oc1), size);
            let (mut s_ms, mut e_ms) = (u.start_ms, u.end_ms);
            if u.c0 < row.c0 || u.c1 > row.c1 {
                // a word wrapped across rows: split its time by width
                let full = width(&slice(u.c0, u.c1), size).max(1e-4);
                let before = width(&slice(u.c0, oc0), size);
                let total = (u.end_ms - u.start_ms).max(0) as f64;
                s_ms = u.start_ms + (total * before / full).round() as i64;
                e_ms = u.start_ms + (total * (before + (x1 - x0)) / full).round() as i64;
            }
            keys.push(Key { x0, x1, start_ms: s_ms, end_ms: e_ms });
            match groups.iter_mut().find(|g| g.0 == u.group) {
                Some(g) => *g = (g.0, g.1.min(lc0), g.2.max(lc1), g.3.min(s_ms), g.4.max(e_ms)),
                None => groups.push((u.group, lc0, lc1, s_ms, e_ms)),
            }
        }
        let rises = groups
            .into_iter()
            .filter(|g| !slice(row.c0 + g.1, row.c0 + g.2).trim().is_empty())
            .map(|g| Rise {
                x0: width(&slice(row.c0, row.c0 + g.1), size),
                x1: width(&slice(row.c0, row.c0 + g.2), size),
                start_ms: g.3,
                end_ms: g.4,
            })
            .collect();
        plan.row_widths.push(width(&row.text, size));
        plan.rows.push(row.text);
        plan.keys.push(keys);
        plan.rises.push(rises);
    }
    plan
}

/// Monotone cubic sweep reach across a row, stretched over [0, row_w + soft].
fn row_reveal(keys: &[Key], row_w: f64, pos_ms: i64, soft: f64) -> f64 {
    if keys.is_empty() || row_w <= 0.0 {
        return 0.0;
    }
    let mut points: Vec<(f64, f64)> = Vec::new();
    for k in keys {
        for (t, x) in [(k.start_ms as f64 / 1000.0, k.x0), (k.end_ms as f64 / 1000.0, k.x1)] {
            match points.last_mut() {
                Some(last) if t <= last.0 => last.1 = last.1.max(x),
                last => {
                    let prev = last.map_or(0.0, |l| l.1);
                    points.push((t, prev.max(x)));
                }
            }
        }
    }
    monotone(&points, pos_ms as f64 / 1000.0) * ((row_w + soft) / row_w)
}

/// Alpha mask of a wrapped line at SS times its size, with an EDGE margin.
fn block(plan: &Plan) -> Gray {
    let widest = plan.rows.iter().map(|r| text::width(r, plan.size * SS, true).ceil() as usize).max().unwrap_or(0);
    let mut mask = Gray::new(widest + 2 * EDGE + 4, plan.rows.len() * plan.lh * SS as usize + 2 * EDGE);
    for (i, row) in plan.rows.iter().enumerate() {
        // centre the letters (not the font box) in their line
        let y = EDGE as f64 + ((i * plan.lh) as f64 + 3.0 - 0.14 * plan.size) * SS;
        draw_gray(&mut mask, EDGE as f64, y, row, plan.size * SS, true, 255);
    }
    mask
}

/// One row of alpha values: `lit` up to the sweep front, feathered into `base`.
fn sweep(width: usize, reach: f64, soft: f64, base: u8, lit: u8) -> Vec<u8> {
    let solid = ((reach - soft).floor().max(0.0) as usize).min(width);
    let mut buf = vec![base; width];
    buf[..solid].fill(lit);
    for x in solid..(reach.ceil().max(0.0) as usize).min(width) {
        let t = ((reach - x as f64) / soft).clamp(0.0, 1.0);
        buf[x] = (base as f64 + (lit as f64 - base as f64) * t * t * (3.0 - 2.0 * t)).round() as u8;
    }
    buf
}

/// The line's mask with the sung part lit, sung words raised and long syllables glowing.
#[allow(clippy::too_many_arguments)]
fn karaoke_block(plan: &Plan, text_mask: &Gray, base: f64, lit: f64, pos_ms: i64, sung_end: Option<i64>, active: bool) -> Gray {
    let mut out = Gray::new(text_mask.w, text_mask.h);
    let ss = SS as usize;
    let row_h = plan.lh * ss;
    let soft = (plan.size * 0.72).clamp(8.0, 14.0);
    let rise_px = (plan.size * 0.085).max(1.5);
    let settle = match sung_end {
        Some(end) if !active && pos_ms >= end => 1.0 - ease_out_cubic((pos_ms - end) as f64 / 140.0),
        _ => 1.0,
    };
    let base_v = (base * 255.0).round() as u8;
    let lit_v = (lit.max(base) * 255.0).round() as u8;

    let count = plan.rows.len();
    for i in 0..count {
        let top = EDGE + i * row_h;
        let y0 = top - if i == 0 { EDGE } else { 0 };
        let y1 = top + row_h + if i == count - 1 { EDGE } else { 0 };
        let reach = row_reveal(&plan.keys[i], plan.row_widths[i], pos_ms, soft);
        let front = if reach > 0.0 { EDGE as f64 + reach * SS } else { 0.0 };
        let mut row = text_mask.rows(y0 as isize, y1 - y0);
        row.multiply_columns(&sweep(text_mask.w, front, soft * SS, base_v, lit_v));

        let groups = &plan.rises[i];
        if groups.is_empty() {
            out.lighten(&row, 0, y0 as isize);
            continue;
        }
        // cut the row between words, so a raised word never slices a letter
        let mut cuts = vec![0usize];
        cuts.extend(groups.windows(2).map(|w| (EDGE as f64 + (w[0].x1 + w[1].x0) * 0.5 * SS).round() as usize));
        cuts.push(text_mask.w);
        for (g, pair) in groups.iter().zip(cuts.windows(2)) {
            let (x0, x1) = (pair[0], pair[1]);
            if x1 <= x0 {
                continue;
            }
            let sung = pos_ms >= g.start_ms;
            let lift = if sung { (word_rise(g.start_ms, g.end_ms, pos_ms) * settle * rise_px * SS).round() as isize } else { 0 };
            let glow = if sung { word_glow(g.start_ms, g.end_ms, pos_ms) * settle } else { 0.0 };
            let mut word = row.columns(x0, x1);
            if glow > 0.01 {
                let mut halo = word.clone();
                halo.blur(2.0 * SS);
                halo.scale_values(glow * 0.9);
                word.px.iter_mut().zip(&halo.px).for_each(|(w, h)| *w = w.saturating_add(*h));
            }
            out.lighten(&word, x0 as isize, y0 as isize - lift);
        }
    }
    out
}

/// Three notes that light up one by one through an instrumental break.
fn break_block(progress: f64, past: bool) -> Gray {
    let ss = SS as usize;
    let mut mask = Gray::new(60 * ss + 2 * EDGE, BREAK_H * ss + 2 * EDGE);
    for i in 0..3 {
        let p = (progress * 3.0 - i as f64).clamp(0.0, 1.0);
        let a = if past { PAST } else { AHEAD + (1.0 - AHEAD) * p };
        let (x, y) = ((EDGE + i * 14 * ss) as f64, (EDGE + 2 * ss) as f64);
        draw_gray(&mut mask, x, y, "\u{266a}", 15.0 * SS, false, (a * 255.0).round() as u8);
    }
    mask
}

/// Scale a supersampled block and put its text origin at the (fractional) point x, y.
fn stamp(sheet: &mut Gray, block: &Gray, x: f64, y: f64, scale: f64, blur: f64, alpha: f64) {
    let f = scale / SS;
    let e = EDGE as f64;
    let (mut ox, mut oy) = ((x - e * f).ceil(), (y - e * f).ceil());
    let (u, v) = ((e - (x - ox) / f).max(0.0), (e - (y - oy) / f).max(0.0));
    let (w, h) = (((block.w as f64 - u) * f) as isize, ((block.h as f64 - v) * f) as isize);
    if w <= 0 || h <= 0 {
        return;
    }
    let mut small = block.resample([u, v, u + w as f64 / f, v + h as f64 / f], w as usize, h as usize, Filter::Hamming);
    if blur > 0.0 {
        let m = (blur * 2.0).ceil() as usize + 1;
        small = small.expand(m);
        small.blur(blur);
        ox -= m as f64;
        oy -= m as f64;
    }
    if alpha < 0.995 {
        small.scale_values(alpha);
    }
    sheet.lighten(&small, ox as isize, oy as isize);
}

fn edge_fade() -> Vec<u8> {
    (0..LYRICS_H)
        .map(|y| {
            let t = (y.min(LYRICS_H - 1 - y) as f64 / EDGE_FADE_PX).clamp(0.0, 1.0);
            (255.0 * t * t * (3.0 - 2.0 * t)).round() as u8
        })
        .collect()
}

struct Sheet {
    id: u64,
    items: Vec<SheetItem>,
    plans: Vec<Option<Plan>>,
    heights: Vec<f64>,
    blocks: HashMap<usize, Rc<Gray>>,
}

impl Sheet {
    fn new(lyrics: &Lyrics) -> Self {
        let items = build_sheet_items(&lyrics.lines);
        let pace = pace_limit(&lyrics.lines);
        let max_w = (W - 2 * PAD) as f64;
        let plans: Vec<Option<Plan>> = items
            .iter()
            .map(|it| match it.line {
                Some(i) if !it.is_break => Some(layout_line(&lyrics.lines[i], pace, max_w)),
                _ => None,
            })
            .collect();
        let heights = plans.iter().map(|p| p.as_ref().map_or(BREAK_H, |p| p.rows.len() * p.lh) as f64).collect();
        Self { id: lyrics.id, items, plans, heights, blocks: HashMap::new() }
    }
}

/// The scrolling lyric viewport (W x LYRICS_H) as an alpha mask for white text.
fn compose_sheet(
    sheet_data: &mut Sheet,
    lyrics: &Lyrics,
    pos_ms: i64,
    karaoke: bool,
    animator: Option<&mut SheetAnimator>,
    now_s: f64,
) -> Gray {
    let animate = animator.is_some();
    let mut still = SheetAnimator::new();
    let animator = animator.unwrap_or(&mut still);
    let Sheet { items, plans, heights, blocks, .. } = sheet_data;
    let count = items.len();

    let ease = move |since: f64| if animate { ease_out_expo((now_s - since) / TRANSITION_S) } else { 1.0 };
    let sizes = |a: &SheetAnimator| {
        let mut out = vec![CONTEXT_SCALE; count];
        if let Some(r) = a.active_row.filter(|&r| r < count) {
            out[r] = CONTEXT_SCALE + (1.0 - CONTEXT_SCALE) * ease(a.arrived_at);
        }
        if let Some(r) = a.departing_row.filter(|&r| r < count && Some(r) != a.active_row) {
            out[r] = 1.0 - (1.0 - CONTEXT_SCALE) * ease(a.departed_at);
        }
        out
    };
    // rows take exactly the height they are drawn at, so the gap between lines is always the same
    let layout = |a: &SheetAnimator| -> Layout {
        let hs: Vec<f64> = heights.iter().zip(sizes(a)).map(|(h, s)| h * s).collect();
        let mut y = 0.0;
        let tops = hs.iter().map(|h| {
            let top = y;
            y += h + VERSE_GAP;
            top
        });
        (tops.collect(), hs.clone())
    };

    let (tops, hs) = animator.step(lyrics.id, items, &layout, ANCHOR_Y, LYRICS_H as f64, pos_ms, now_s, animate);
    let scales = sizes(animator);
    let (arrived, departed) = (ease(animator.arrived_at), ease(animator.departed_at));
    let (active_row, departing_row) = (animator.active_row, animator.departing_row);
    let mut sheet = Gray::new(W, LYRICS_H);

    for r in 0..count {
        let drift = animator.row_drifts.get(&r).map_or(0.0, |d| d[0]);
        let top = tops[r] - animator.scroll_y + drift;
        let h = hs[r];
        if top + h < -12.0 || top > LYRICS_H as f64 + 12.0 {
            continue;
        }
        let item = &items[r];
        let is_active = active_row == Some(r);
        let is_departing = departing_row == Some(r) && !is_active;
        let mut softness = viewport_softness(top + h * 0.5);
        if is_active {
            softness *= 1.0 - arrived;
        } else if is_departing {
            softness *= departed;
        }
        let veil = 1.0 - VEIL * softness;
        let before_active = active_row.is_some_and(|a| r < a);

        let Some(plan) = plans[r].as_ref() else {
            let end = item.end_ms.unwrap_or(item.start_ms);
            let prog = ((pos_ms - item.start_ms) as f64 / (end - item.start_ms).max(1) as f64).clamp(0.0, 1.0);
            let past = pos_ms >= end || before_active;
            stamp(&mut sheet, &break_block(prog, past), PAD as f64, top, scales[r], quantized_blur(15.0, softness), veil);
            continue;
        };
        let Some(line) = item.line.map(|i| &lyrics.lines[i]) else { continue };
        let sung_end = own_sung_end(line);
        let ended = before_active || sung_end.is_some_and(|end| pos_ms >= end);
        let worded = karaoke && line.words.is_some();
        let show_karaoke =
            worded && (is_active || (pos_ms >= line.start_ms && sung_end.is_some_and(|end| pos_ms < end + 800)));

        let base = if is_active {
            AHEAD + (if worded { QUIET } else { 1.0 } - AHEAD) * arrived
        } else if is_departing {
            let start = if worded { QUIET } else { 1.0 };
            start + (PAST - start) * departed
        } else if ended {
            PAST
        } else {
            AHEAD
        };
        let blur = quantized_blur(plan.size * CONTEXT_SCALE, softness);
        let mask = blocks.entry(r).or_insert_with(|| Rc::new(block(plan))).clone();

        if show_karaoke {
            let fade = match sung_end {
                Some(end) if !is_active => ((pos_ms - end) as f64 / (KARAOKE_FADE_S * 1000.0)).clamp(0.0, 1.0),
                _ => 0.0,
            };
            let lit = karaoke_block(plan, &mask, base * veil, (1.0 - fade) * veil, pos_ms, sung_end, is_active);
            stamp(&mut sheet, &lit, PAD as f64, top, scales[r], blur, 1.0);
        } else {
            stamp(&mut sheet, &mask, PAD as f64, top, scales[r], blur, base * veil);
        }
    }
    sheet.multiply_rows(&edge_fade());
    sheet
}

type SongKey = (u64, String, String, String);

/// Draws screens, keeping what does not change between frames.
#[derive(Default)]
pub struct Renderer {
    /// blurred cover behind everything, by cover
    back: Option<(u64, Rgb)>,
    /// static pixels of the current screen, by (cover, title, artist, source)
    base: Option<(SongKey, Rgb)>,
    sheet: Option<Sheet>,
}

fn hash(data: Option<&[u8]>) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    data.hash(&mut h);
    h.finish()
}

impl Renderer {
    /// The whole 240x240 screen at this moment.
    pub fn screen(
        &mut self,
        np: &NowPlaying,
        pos_ms: i64,
        lyrics: Option<&Lyrics>,
        karaoke: bool,
        animator: Option<&mut SheetAnimator>,
        now_s: f64,
    ) -> Rgb {
        let cover = np.cover.as_ref().map(|c| c.as_slice());
        let cover_key = hash(cover);
        let lyrics = lyrics.filter(|l| !l.lines.is_empty());
        let source = lyrics.map_or(String::new(), |l| l.source.clone());
        let kind = if lyrics.is_some() { source.clone() } else { "\0basic".into() };
        let key = (cover_key, np.title.clone(), np.artist.clone(), kind);

        if self.back.as_ref().map(|b| b.0) != Some(cover_key) {
            self.back = Some((cover_key, background(cover)));
            self.base = None;
        }
        if self.base.as_ref().map(|b| &b.0) != Some(&key) {
            let back = &self.back.as_ref().expect("just set").1;
            let img = match lyrics {
                Some(_) => lyric_base(back, np, cover, &source),
                None => basic_screen(back, np, cover),
            };
            self.base = Some((key, img));
        }
        let mut img = self.base.as_ref().expect("just set").1.clone();
        if let Some(ly) = lyrics {
            if self.sheet.as_ref().map(|s| s.id) != Some(ly.id) {
                self.sheet = Some(Sheet::new(ly));
            }
            let sheet = self.sheet.as_mut().expect("just set");
            let mask = compose_sheet(sheet, ly, pos_ms, karaoke, animator, now_s);
            img.paint(&mask, LYRICS_Y, WHITE);
        }
        progress(&mut img, np, pos_ms, now_s);
        img
    }
}

/// A rectangle of the screen: x, y, w, h.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Rect {
    pub x: usize,
    pub y: usize,
    pub w: usize,
    pub h: usize,
}

/// Parts that differ from what the screen shows now. The ever-moving timeline goes on its own so it
/// does not drag the lyrics along.
pub fn dirty(previous: Option<&Rgb>, frame: &Rgb) -> Vec<Rect> {
    let Some(prev) = previous else { return vec![Rect { x: 0, y: 0, w: W, h: H }] };
    let mut out = Vec::new();
    for (top, bottom) in [(0, TIMELINE_Y), (TIMELINE_Y, H)] {
        let (mut x0, mut y0, mut x1, mut y1) = (W, H, 0, 0);
        for y in top..bottom {
            for x in 0..W {
                if frame.get(x, y) != prev.get(x, y) {
                    (x0, y0, x1, y1) = (x0.min(x), y0.min(y), x1.max(x + 1), y1.max(y + 1));
                }
            }
        }
        if x1 > x0 {
            out.push(Rect { x: x0, y: y0, w: x1 - x0, h: y1 - y0 });
        }
    }
    if out.is_empty() {
        // nothing changed: one pixel in the corner keeps the screen in music mode
        out.push(Rect { x: W - 1, y: H - 1, w: 1, h: 1 });
    }
    out
}

/// The panel's own pixel format, two bytes each, high byte first.
pub fn rgb565(img: &Rgb) -> Vec<u8> {
    let mut out = Vec::with_capacity(img.w * img.h * 2);
    for p in img.px.chunks_exact(3) {
        out.push((p[0] & 0xF8) | (p[1] >> 5));
        out.push(((p[1] & 0x1C) << 3) | (p[2] >> 3));
    }
    out
}

#[cfg(test)]
mod tests;
