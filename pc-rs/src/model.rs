//! Shared data: what is playing and its timed lyrics.
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

#[derive(Clone, Debug, PartialEq)]
pub struct Word {
    pub start_ms: i64,
    pub end_ms: i64,
    pub text: String,
}

impl Word {
    pub fn new(start_ms: i64, end_ms: i64, text: impl Into<String>) -> Self {
        Self { start_ms, end_ms, text: text.into() }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Line {
    pub start_ms: i64,
    pub text: String,
    pub words: Option<Vec<Word>>,
}

impl Line {
    pub fn new(start_ms: i64, text: impl Into<String>) -> Self {
        Self { start_ms, text: text.into(), words: None }
    }
    pub fn worded(start_ms: i64, text: impl Into<String>, words: Vec<Word>) -> Self {
        Self { start_ms, text: text.into(), words: Some(words) }
    }
}

#[derive(Clone, Debug)]
pub struct Lyrics {
    pub lines: Vec<Line>,
    pub word_synced: bool,
    pub source: String,
    /// length of the recording these lyrics were timed to, 0 if unknown
    pub duration_ms: i64,
    /// tells two sheets apart for the animator and the layout cache
    pub id: u64,
}

impl Lyrics {
    pub fn new(lines: Vec<Line>, word_synced: bool, source: &str, duration_ms: i64) -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(1);
        Self { lines, word_synced, source: source.into(), duration_ms, id: NEXT.fetch_add(1, Ordering::Relaxed) }
    }
}

#[derive(Clone, Debug, Default)]
pub struct NowPlaying {
    pub title: String,
    pub artist: String,
    pub duration_ms: i64,
    /// position at `updated_at`
    pub position_ms: i64,
    pub playing: bool,
    pub cover: Option<Arc<Vec<u8>>>,
    /// unix seconds when position_ms was true
    pub updated_at: f64,
    pub app: String,
}

/// Windows only refreshes the position now and then; add the time played since.
pub fn interpolate(np: &NowPlaying, now: f64) -> i64 {
    let mut pos = np.position_ms + if np.playing { ((now - np.updated_at) * 1000.0) as i64 } else { 0 };
    if np.duration_ms > 0 {
        pos = pos.min(np.duration_ms);
    }
    pos.max(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interpolate_clamps_and_pauses() {
        let np = |dur, pos, playing, at| NowPlaying { duration_ms: dur, position_ms: pos, playing, updated_at: at, ..Default::default() };
        assert_eq!(interpolate(&np(1000, 900, true, 0.0), 5.0), 1000);
        assert_eq!(interpolate(&np(200000, 900, false, 0.0), 5.0), 900);
        assert_eq!(interpolate(&np(200000, 1000, true, 10.0), 10.5), 1500);
    }
}
