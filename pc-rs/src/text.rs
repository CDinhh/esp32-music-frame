//! Text with the fonts Windows ships, picked per script so mixed Vietnamese/Korean/Japanese all render.
use ab_glyph::{point, Font, FontRef, Glyph, PxScale, ScaleFont};
use std::collections::HashMap;
use std::sync::Mutex;

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Script {
    Latin,
    Japanese,
    Chinese,
    Korean,
    Symbol,
}

fn files(script: Script) -> (&'static str, &'static str) {
    match script {
        Script::Latin => ("segoeui.ttf", "segoeuib.ttf"),
        Script::Japanese => ("YuGothM.ttc", "YuGothB.ttc"),
        Script::Chinese => ("msyh.ttc", "msyhbd.ttc"),
        Script::Korean => ("malgun.ttf", "malgunbd.ttf"),
        Script::Symbol => ("seguisym.ttf", "seguisym.ttf"), // ♪ and other symbols Segoe UI lacks
    }
}

fn script_of(ch: char, has_kana: bool) -> Script {
    match ch as u32 {
        0x2190..=0x2BFF => Script::Symbol,
        0xAC00..=0xD7AF | 0x1100..=0x11FF | 0x3130..=0x318F => Script::Korean,
        0x3040..=0x30FF | 0x31F0..=0x31FF => Script::Japanese,
        0x3400..=0x9FFF | 0x3000..=0x303F | 0xFF00..=0xFFEF => {
            if has_kana {
                Script::Japanese
            } else {
                Script::Chinese
            }
        }
        _ => Script::Latin,
    }
}

/// A font file mapped into memory once and kept: only the pages actually read use RAM.
fn font(script: Script, bold: bool) -> Option<&'static FontRef<'static>> {
    type Cache = HashMap<(Script, bool), Option<&'static FontRef<'static>>>;
    static CACHE: Mutex<Option<Cache>> = Mutex::new(None);
    let mut guard = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    let found = *guard.get_or_insert_with(HashMap::new).entry((script, bold)).or_insert_with(|| {
        let dir = std::env::var("WINDIR").unwrap_or_else(|_| r"C:\Windows".into());
        let name = if bold { files(script).1 } else { files(script).0 };
        let file = std::fs::File::open(format!(r"{dir}\Fonts\{name}")).ok()?;
        // SAFETY: a system font file; nothing rewrites it while the app runs
        let map = unsafe { memmap2::Mmap::map(&file) }.ok()?;
        let data: &'static memmap2::Mmap = Box::leak(Box::new(map));
        FontRef::try_from_slice_and_index(data, 0).ok().map(|f| &*Box::leak(Box::new(f)))
    });
    drop(guard);
    // a missing font falls back to Segoe UI, which every Windows has
    found.or_else(|| if script == Script::Latin { None } else { self::font(Script::Latin, bold) })
}

/// ab_glyph sizes by line height; these sizes are em pixels like Pillow's.
fn px_scale(f: &FontRef, size: f64) -> PxScale {
    let em = f.units_per_em().unwrap_or(2048.0);
    PxScale::from(size as f32 * f.height_unscaled() / em)
}

/// Same-script runs of the text.
fn runs(text: &str) -> Vec<(Script, String)> {
    let kana = text.chars().any(|c| ('\u{3040}'..='\u{30ff}').contains(&c));
    let mut out: Vec<(Script, String)> = Vec::new();
    for c in text.chars() {
        let s = if c.is_whitespace() { out.last().map_or(Script::Latin, |r| r.0) } else { script_of(c, kana) };
        match out.last_mut() {
            Some((last, run)) if *last == s => run.push(c),
            _ => out.push((s, c.to_string())),
        }
    }
    out
}

/// Walk the glyphs of the text starting at x = 0; returns the total advance.
fn layout(text: &str, size: f64, bold: bool, mut each: impl FnMut(&FontRef, Glyph)) -> f64 {
    let mut x = 0f32;
    for (script, run) in runs(text) {
        let Some(f) = font(script, bold) else { continue };
        let scale = px_scale(f, size);
        let scaled = f.as_scaled(scale);
        let mut prev = None;
        for c in run.chars() {
            let id = f.glyph_id(c);
            if let Some(p) = prev {
                x += scaled.kern(p, id);
            }
            each(f, id.with_scale_and_position(scale, point(x, 0.0)));
            x += scaled.h_advance(id);
            prev = Some(id);
        }
    }
    x as f64
}

pub fn width(text: &str, size: f64, bold: bool) -> f64 {
    layout(text, size, bold, |_, _| {})
}

/// Draw with the top of the line at y (baseline at y + size, shared by mixed fonts).
/// `plot` receives pixel positions and coverage.
pub fn draw(x: f64, y: f64, text: &str, size: f64, bold: bool, mut plot: impl FnMut(i32, i32, f32)) {
    let base = (y + size) as f32;
    layout(text, size, bold, |f, mut g| {
        g.position = point(g.position.x + x as f32, base);
        if let Some(outline) = f.outline_glyph(g) {
            let b = outline.px_bounds();
            outline.draw(|gx, gy, c| plot(b.min.x as i32 + gx as i32, b.min.y as i32 + gy as i32, c));
        }
    });
}

/// Cut with an ellipsis so the text fits `max_w`.
pub fn fit(text: &str, size: f64, max_w: f64, bold: bool) -> String {
    if width(text, size, bold) <= max_w {
        return text.to_string();
    }
    let mut cut = text.to_string();
    while !cut.is_empty() && width(&format!("{cut}…"), size, bold) > max_w {
        cut.pop();
    }
    format!("{}…", cut.trim_end())
}

/// A wrapped row and the character range of the line it shows.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub text: String,
    pub c0: usize,
    pub c1: usize,
}

/// Wrap on spaces, or per character for text without spaces (CJK, very long words).
pub fn wrap(text: &str, size: f64, max_w: f64, bold: bool) -> Vec<Row> {
    let chars: Vec<char> = text.chars().collect();
    let row = |c0: usize, c1: usize| Row { text: chars[c0..c1].iter().collect(), c0, c1 };
    let wide = |c0: usize, c1: usize| width(&chars[c0..c1].iter().collect::<String>(), size, bold) > max_w;
    // words as [start, end); a word wider than the screen is split into pieces that fit
    let mut spans: Vec<(usize, usize)> = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == ' ' {
            i += 1;
            continue;
        }
        let mut j = i;
        while j < chars.len() && chars[j] != ' ' {
            j += 1;
        }
        let mut start = i;
        for k in i + 1..j {
            if wide(start, k + 1) {
                spans.push((start, k));
                start = k;
            }
        }
        spans.push((start, j));
        i = j;
    }
    let Some(&(mut c0, mut c1)) = spans.first() else { return vec![row(0, chars.len())] };
    let mut rows = Vec::new();
    for &(s0, s1) in &spans[1..] {
        if wide(c0, s1) {
            rows.push(row(c0, c1));
            c0 = s0;
        }
        c1 = s1;
    }
    rows.push(row(c0, c1));
    rows
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fit_cuts_long_titles() {
        let s = fit(&"A".repeat(200), 18.0, 220.0, false);
        assert!(s.ends_with('…') && width(&s, 18.0, false) <= 220.0);
    }

    #[test]
    fn scripts_pick_their_font() {
        assert_eq!(runs("사랑해")[0].0, Script::Korean);
        assert_eq!(runs("心做し")[0].0, Script::Japanese);
        assert_eq!(runs("我爱你")[0].0, Script::Chinese);
        assert_eq!(runs("Tiếng Việt")[0].0, Script::Latin);
        for s in [Script::Latin, Script::Japanese, Script::Chinese, Script::Korean, Script::Symbol] {
            assert!(font(s, true).is_some(), "{s:?}");
        }
    }

    #[test]
    fn every_wrapped_row_fits_and_covers_the_text() {
        let samples = [
            "あなたのことなんて全然好きじゃないけれどずっとそばにいたい",
            "Anh yêu em 사랑해 愛してる và mãi mãi về sau nữa",
            "Pneumonoultramicroscopicsilicovolcanoconiosis",
            "🎵🎵🎵",
            "",
        ];
        for text in samples {
            let rows = wrap(text, 20.0, 212.0, true);
            assert!(rows.iter().all(|r| width(&r.text, 20.0, true) <= 212.0), "{text}");
            let shown: String = rows.iter().map(|r| r.text.replace(' ', "")).collect();
            assert_eq!(shown, text.replace(' ', ""));
        }
    }
}
