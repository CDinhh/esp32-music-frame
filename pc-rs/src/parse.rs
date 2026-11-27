//! Lyric formats: LRC, NetEase yrc, Kugou krc, Musixmatch richsync, Apple-style TTML.
use crate::model::{Line, Word};
use base64::Engine;
use regex_lite::Regex;
use std::io::Read;

const KRC_KEY: [u8; 16] = [64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105];

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("pattern")
}

/// "[00:01.50][00:10.00]la" -> two lines; metadata tags like [ar:x] are skipped.
pub fn parse_lrc(text: &str) -> Vec<Line> {
    let stamp = re(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]");
    let num = |s: &str| s.parse::<i64>().unwrap_or(0);
    let mut out = Vec::new();
    for raw in text.lines() {
        let stamps: Vec<_> = stamp.captures_iter(raw).collect();
        let Some(last) = stamps.last() else { continue };
        let lyric = raw[last.get(0).map_or(0, |m| m.end())..].trim();
        for m in &stamps {
            let frac = m.get(3).map_or("0", |g| g.as_str());
            let ms = num(frac) * 10i64.pow(3 - frac.len() as u32);
            out.push(Line::new(num(&m[1]) * 60000 + num(&m[2]) * 1000 + ms, lyric));
        }
    }
    out.sort_by_key(|l| l.start_ms);
    out
}

fn joined(words: &[Word]) -> String {
    words.iter().map(|w| w.text.as_str()).collect::<String>().trim().to_string()
}

/// "[start,duration]body" lines; `word` captures (offset, duration, text).
fn bracket_lines(text: &str, word: &Regex, relative: bool) -> Vec<Line> {
    let line = re(r"^\[(\d+),(\d+)\](.*)$");
    let num = |s: &str| s.parse::<i64>().unwrap_or(0);
    let mut out = Vec::new();
    for raw in text.lines() {
        let Some(m) = line.captures(raw.trim()) else { continue }; // JSON credit lines start with '{'
        let start = num(&m[1]);
        let words: Vec<Word> = word
            .captures_iter(m.get(3).map_or("", |g| g.as_str()))
            .map(|w| {
                let s = num(&w[1]) + if relative { start } else { 0 };
                Word::new(s, s + num(&w[2]), &w[3])
            })
            .collect();
        if !words.is_empty() {
            out.push(Line::worded(start, joined(&words), words));
        }
    }
    out
}

/// "[1000,2000](1000,500,0)Hel(1500,500,0)lo": word times are absolute ms.
pub fn parse_yrc(text: &str) -> Vec<Line> {
    bracket_lines(text, &re(r"\((\d+),(\d+),\d+\)([^(]*)"), false)
}

/// "[2000,1000]<0,400,0>A<400,600,0>B": word offsets are relative to the line start.
pub fn parse_krc(text: &str) -> Vec<Line> {
    bracket_lines(text, &re(r"<(\d+),(\d+),\d+>([^<]*)"), true)
}

pub fn decode_krc(b64: &str) -> Option<String> {
    let raw = base64::engine::general_purpose::STANDARD.decode(b64.trim()).ok()?;
    let plain: Vec<u8> = raw.get(4..)?.iter().enumerate().map(|(i, b)| b ^ KRC_KEY[i % 16]).collect(); // drop "krc1"
    let mut out = String::new();
    flate2::read::ZlibDecoder::new(plain.as_slice()).take(4 << 20).read_to_string(&mut out).ok()?;
    Some(out)
}

/// Musixmatch: [{"ts":s,"te":s,"l":[{"c":chars,"o":offset_s}],"x":text}], per-char timings kept.
pub fn parse_richsync(json: &str) -> Vec<Line> {
    let Ok(serde_json::Value::Array(items)) = serde_json::from_str(json) else { return Vec::new() };
    let ms = |s: f64| (s * 1000.0).round() as i64;
    let mut out = Vec::new();
    for item in items {
        let (Some(ts), Some(te)) = (item["ts"].as_f64(), item["te"].as_f64()) else { continue };
        let parts = item["l"].as_array().cloned().unwrap_or_default();
        let mut words = Vec::new();
        for (i, p) in parts.iter().enumerate() {
            let ch = p["c"].as_str().unwrap_or("");
            let Some(o) = p["o"].as_f64() else { continue };
            if ch.is_empty() {
                continue;
            }
            let start = ms(ts + o);
            let end = parts.get(i + 1).and_then(|n| n["o"].as_f64()).map_or(ms(te), |n| ms(ts + n));
            words.push(Word::new(start, end.max(start), ch));
        }
        if !words.is_empty() {
            let text = item["x"].as_str().filter(|x| !x.is_empty()).map_or_else(|| joined(&words), str::to_string);
            out.push(Line::worded(ms(ts), text, words));
        }
    }
    out
}

/// "11.933", "4:22.164" or "1:02:03.5" -> ms.
fn ttml_time(t: &str) -> Option<i64> {
    let mut secs = 0.0;
    for part in t.trim().trim_end_matches('s').split(':') {
        secs = secs * 60.0 + part.parse::<f64>().ok()?;
    }
    Some((secs * 1000.0).round() as i64)
}

/// `<p begin>` lines holding `<span begin end>` words -> (lines, word_timed).
/// Background-vocal spans (ttm:role) are left out so the main line reads cleanly.
pub fn parse_ttml(text: &str) -> (Vec<Line>, bool) {
    let Ok(doc) = roxmltree::Document::parse(text) else { return (Vec::new(), false) };
    let (mut out, mut worded) = (Vec::new(), false);
    for p in doc.descendants().filter(|n| n.is_element() && n.tag_name().name() == "p") {
        let Some(begin) = p.attribute("begin").and_then(ttml_time) else { continue };
        let mut words = Vec::new();
        for sp in p.children().filter(|n| n.is_element() && n.tag_name().name() == "span") {
            if sp.attributes().any(|a| a.name() == "role") {
                continue;
            }
            let Some(start) = sp.attribute("begin").and_then(ttml_time) else { continue };
            let end = sp.attribute("end").and_then(ttml_time).unwrap_or(start);
            let mut word = sp.text().unwrap_or("").to_string();
            // the space between two words lives in the text after the span
            let tail = sp.next_sibling().filter(|n| n.is_text()).and_then(|n| n.text());
            if tail.is_some_and(|t| !t.is_empty() && t.trim().is_empty()) {
                word.push(' ');
            }
            words.push(Word::new(start, end, word));
        }
        if !words.is_empty() {
            worded = true;
            out.push(Line::worded(words[0].start_ms, joined(&words), words));
        } else {
            let line: String = p.descendants().filter(|n| n.is_text()).filter_map(|n| n.text()).collect();
            if !line.trim().is_empty() {
                out.push(Line::new(begin, line.trim()));
            }
        }
    }
    out.sort_by_key(|l| l.start_ms);
    (out, worded)
}

#[cfg(test)]
pub mod tests {
    use super::*;

    pub const TTML: &str = concat!(
        r#"<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>"#,
        r#"<p begin="11.933" end="16.989"><span begin="11.933" end="12.343">Anh</span> <span begin="12.343" end="12.738">tìm</span>"#,
        r#"<span ttm:role="x-bg" begin="12.5" end="13"><span begin="12.5" end="13">(ooh)</span></span></p>"#,
        r#"<p begin="1:02.5" end="1:05">Dòng không có chữ</p></div></body></tt>"#
    );

    #[test]
    fn lrc_multi_stamp() {
        let ls = parse_lrc("[ar:x]\n[00:01.50][00:10.00]la\n[00:05.123]li\n[01:02]x");
        let got: Vec<_> = ls.iter().map(|l| (l.start_ms, l.text.as_str())).collect();
        assert_eq!(got, [(1500, "la"), (5123, "li"), (10000, "la"), (62000, "x")]);
    }

    #[test]
    fn yrc_words() {
        let ls = parse_yrc("{\"t\":0,\"c\":[]}\n[1000,2000](1000,500,0)Hel(1500,500,0)lo");
        assert_eq!(ls.len(), 1);
        assert_eq!((ls[0].start_ms, ls[0].text.as_str()), (1000, "Hello"));
        assert_eq!(ls[0].words.as_ref().unwrap()[1], Word::new(1500, 2000, "lo"));
    }

    #[test]
    fn krc_relative_and_decoded() {
        let l = &parse_krc("[language:xx]\n[2000,1000]<0,400,0>A<400,600,0>B")[0];
        let w = l.words.as_ref().unwrap();
        assert_eq!((w[0].start_ms, w[1].start_ms), (2000, 2400));

        use std::io::Write;
        let plain = "[0,1]<0,1,0>x";
        let mut z = flate2::write::ZlibEncoder::new(Vec::new(), Default::default());
        z.write_all(plain.as_bytes()).unwrap();
        let mut enc = b"krc1".to_vec();
        enc.extend(z.finish().unwrap().iter().enumerate().map(|(i, b)| b ^ KRC_KEY[i % 16]));
        assert_eq!(decode_krc(&base64::engine::general_purpose::STANDARD.encode(enc)).as_deref(), Some(plain));
        assert_eq!(decode_krc("????"), None);
    }

    #[test]
    fn richsync_words() {
        let l = &parse_richsync(r#"[{"ts":1.0,"te":2.0,"l":[{"c":"H","o":0},{"c":"i","o":0.2},{"c":" ","o":0.4},{"c":"y","o":0.5},{"c":"o","o":0.7}],"x":"Hi yo"}]"#)[0];
        let w = l.words.as_ref().unwrap();
        assert_eq!(w.iter().map(|w| w.text.as_str()).collect::<Vec<_>>(), ["H", "i", " ", "y", "o"]);
        assert_eq!((w[0].start_ms, w[0].end_ms, w[3].start_ms, w[4].end_ms), (1000, 1200, 1500, 2000));
        assert!(parse_richsync("not json").is_empty());
    }

    #[test]
    fn ttml_words_and_lines() {
        let (lines, worded) = parse_ttml(TTML);
        assert!(worded);
        assert_eq!((lines[0].start_ms, lines[0].text.as_str()), (11933, "Anh tìm"));
        let w: Vec<_> = lines[0].words.as_ref().unwrap().iter().map(|w| (w.start_ms, w.end_ms)).collect();
        assert_eq!(w, [(11933, 12343), (12343, 12738)]);
        assert_eq!((lines[1].start_ms, lines[1].text.as_str()), (62500, "Dòng không có chữ"));
        assert!(lines[1].words.is_none());
        assert!(parse_ttml("<broken").0.is_empty());
    }
}
