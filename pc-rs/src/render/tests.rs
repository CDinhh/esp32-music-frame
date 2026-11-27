use super::*;
use crate::model::Word;
use std::sync::Arc;

fn song(pos: i64) -> NowPlaying {
    NowPlaying { title: "Song".into(), artist: "Artist".into(), duration_ms: 200_000, position_ms: pos, playing: true, ..Default::default() }
}

fn hellos(n: i64) -> Lyrics {
    Lyrics::new((0..n).map(|i| Line::new(i * 1000, "Hello")).collect(), false, "lrc", 0)
}

fn sheet_at(ly: &Lyrics, pos: i64) -> Gray {
    compose_sheet(&mut Sheet::new(ly), ly, pos, false, None, 1.0)
}

/// (top, bottom) of each run of lit rows.
fn bands(mask: &Gray) -> Vec<(usize, usize)> {
    let mut out = Vec::new();
    let mut start = None;
    for y in 0..=mask.h {
        let on = y < mask.h && mask.px[y * mask.w..(y + 1) * mask.w].iter().any(|&v| v > 25);
        match (on, start) {
            (true, None) => start = Some(y),
            (false, Some(s)) => {
                out.push((s, y));
                start = None;
            }
            _ => {}
        }
    }
    out
}

#[test]
fn active_line_is_centred_and_lines_are_evenly_spaced() {
    let centres: Vec<f64> = bands(&sheet_at(&hellos(7), 3100)).iter().map(|&(a, b)| (a + b) as f64 / 2.0).collect();
    let mid = LYRICS_H as f64 / 2.0;
    let i = (0..centres.len()).min_by(|&a, &b| (centres[a] - mid).abs().total_cmp(&(centres[b] - mid).abs())).unwrap();
    assert!((centres[i] - mid).abs() <= 2.5, "{centres:?}");
    assert!(((centres[i] - centres[i - 1]) - (centres[i + 1] - centres[i])).abs() <= 1.5, "{centres:?}");
}

#[test]
fn first_line_starts_at_the_top() {
    assert!(bands(&sheet_at(&hellos(7), 100))[0].0 < 30);
}

#[test]
fn karaoke_lights_only_the_sung_half() {
    let words = vec![Word::new(1000, 2000, "AAAA"), Word::new(2000, 3000, "BBBB")];
    let ly = Lyrics::new(vec![Line::worded(1000, "AAAABBBB", words)], true, "t", 0);
    let img = Renderer::default().screen(&song(2000), 2000, Some(&ly), true, None, 1.0);
    let white: Vec<usize> = (90..118)
        .flat_map(|y| (20..220).map(move |x| (x, y)))
        .filter(|&(x, y)| img.get(x, y).iter().all(|&c| c > 235))
        .map(|(x, _)| x)
        .collect();
    assert!(!white.is_empty());
    assert!(*white.iter().max().unwrap() < 125);
}

#[test]
fn haze_blurs_far_lines_more() {
    assert_eq!(viewport_softness(ANCHOR_Y), 0.0);
    assert_eq!(quantized_blur(17.0, 0.0), 0.0);
    let (near, far) = (viewport_softness(ANCHOR_Y + 28.0), viewport_softness(ANCHOR_Y + 65.0));
    assert!(0.0 < near && near < far && far <= 1.0);
    assert!(0.0 < quantized_blur(17.0, near) && quantized_blur(17.0, near) <= quantized_blur(17.0, far));
}

#[test]
fn cover_shows_and_timeline_stays_blue_up_to_the_knob() {
    let red = image::RgbImage::from_pixel(64, 64, image::Rgb([200, 30, 30]));
    let mut png = std::io::Cursor::new(Vec::new());
    red.write_to(&mut png, image::ImageFormat::Png).unwrap();
    let mut np = song(100_000);
    np.cover = Some(Arc::new(png.into_inner()));
    let img = Renderer::default().screen(&np, 100_000, None, false, None, 1.0);
    let [r, g, _] = img.get(120, 80);
    assert!(r > 150 && g < 80, "cover");
    let blue = (45..195)
        .flat_map(|x| (219..232).map(move |y| (x, y)))
        .filter(|&(x, y)| {
            let [r, _, b] = img.get(x, y);
            b > 180 && b as i32 - r as i32 > 50
        })
        .map(|(x, _)| x)
        .max()
        .unwrap();
    assert!(blue < 130, "the wave stops at the half-way knob: {blue}");
    assert!(img.get(160, 225).iter().zip(RAIL).all(|(&a, b)| (a as i32 - b as i32).abs() < 20), "{:?}", img.get(160, 225));
}

#[test]
fn only_changed_parts_are_sent() {
    let ly = Lyrics::new(vec![Line::new(0, "Hello")], false, "lrc", 0);
    let mut r = Renderer::default();
    let shown = r.screen(&song(0), 5000, Some(&ly), false, None, 1.0);
    assert_eq!(dirty(None, &shown), [Rect { x: 0, y: 0, w: W, h: H }]);
    let same = r.screen(&song(0), 5000, Some(&ly), false, None, 1.0);
    assert_eq!(dirty(Some(&shown), &same), [Rect { x: 239, y: 239, w: 1, h: 1 }]);
    for (pos, now) in [(5000, 1.2), (30000, 1.0)] {
        let next = r.screen(&song(0), pos, Some(&ly), false, None, now);
        let parts = dirty(Some(&shown), &next);
        assert_eq!(parts.len(), 1);
        assert!(parts[0].y >= TIMELINE_Y);
    }
}

#[test]
fn rgb565_matches_the_panel_format() {
    let img = Rgb { w: 3, h: 1, px: vec![255, 0, 0, 0, 255, 0, 8, 12, 255] };
    assert_eq!(rgb565(&img), [0xF8, 0x00, 0x07, 0xE0, 0x08, 0x7F]);
}

#[test]
fn odd_input_still_draws() {
    let lines = vec![
        Line::new(0, "🎵🎵🎵"),
        Line::new(1000, "Pneumonoultramicroscopicsilicovolcanoconiosis".repeat(4)),
        Line::new(2000, ""),
        Line::new(3000, "心做し".repeat(60)),
    ];
    let ly = Lyrics::new(lines, false, "lrc", 0);
    let mut np = song(0);
    np.duration_ms = 0;
    np.title = String::new();
    np.cover = Some(Arc::new(vec![1, 2, 3]));
    let mut r = Renderer::default();
    let mut a = SheetAnimator::new();
    for pos in [0, 1500, 2500, 3500, 999_999] {
        r.screen(&np, pos, Some(&ly), true, Some(&mut a), pos as f64 / 1000.0);
        r.screen(&np, pos, None, false, None, 1.0);
    }
}

/// `SAMPLE_DIR=... cargo test samples -- --ignored` writes the screens compared with the Python app.
#[test]
#[ignore]
fn samples() {
    let dir = std::env::var("SAMPLE_DIR").expect("SAMPLE_DIR");
    let cover = std::fs::read(format!("{dir}/cover.jpg")).expect("cover.jpg");
    let texts = [
        "Dường như em đã quên rồi",
        "tiếng Việt có dấu đầy đủ ở đây nhé các bạn",
        "あなたのことなんて全然好きじゃない",
        "사랑해 그리고 미안해",
        "我爱你 到永远",
        "last line here",
    ];
    let lines: Vec<Line> = texts
        .iter()
        .enumerate()
        .map(|(i, t)| {
            let start = 1000 + i as i64 * 3000;
            let parts: Vec<&str> = t.split(' ').collect();
            let step = 2500 / parts.len() as i64;
            let words = parts
                .iter()
                .enumerate()
                .map(|(k, p)| {
                    let k64 = k as i64;
                    let space = if k < parts.len() - 1 { " " } else { "" };
                    Word::new(start + k64 * step, start + (k64 + 1) * step, format!("{p}{space}"))
                })
                .collect();
            Line::worded(start, *t, words)
        })
        .collect();
    let ly = Lyrics::new(lines, true, "lrcred", 0);
    let mut np = song(0);
    np.title = "Đây là tên bài hát rất dài để cắt".into();
    np.artist = "Ca sĩ".into();
    np.cover = Some(Arc::new(cover));
    let mut r = Renderer::default();
    let save = |img: Rgb, name: &str| {
        image::save_buffer(format!("{dir}/{name}.png"), &img.px, W as u32, H as u32, image::ExtendedColorType::Rgb8).unwrap();
    };
    for (i, pos) in [2200, 5200, 8200, 11200, 14200].into_iter().enumerate() {
        save(r.screen(&np, pos, Some(&ly), true, None, 10.0), &format!("rs{i}"));
    }
    save(r.screen(&np, 100_000, None, false, None, 10.0), "rs5");

    // cost of one animated frame
    let mut a = SheetAnimator::new();
    let began = std::time::Instant::now();
    let mut prev: Option<Rgb> = None;
    for n in 0..240 {
        let frame = r.screen(&np, 3800 + n * 33, Some(&ly), true, Some(&mut a), 100.0 + n as f64 * 0.033);
        for rect in dirty(prev.as_ref(), &frame) {
            std::hint::black_box(rgb565(&frame.crop(rect.x, rect.y, rect.w, rect.h)));
        }
        prev = Some(frame);
    }
    println!("frame: {:.1} ms", began.elapsed().as_secs_f64() * 1000.0 / 240.0);
}
