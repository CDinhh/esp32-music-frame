//! Reads what Windows is playing, draws the music screen and pushes it to the ESP32.
use crate::clean::clean_title;
use crate::log;
use crate::media;
use crate::model::{interpolate, Lyrics, NowPlaying};
use crate::motion::{build_sheet_items, SheetAnimator};
use crate::render::{dirty, rgb565, Rect, Renderer, H, W};
use crate::screen::{Client, Settings};
use crate::sources;
use std::collections::HashMap;
use std::io::Write;
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const LINE_WAIT: f64 = 1.0;
const ANIM_WAIT: f64 = 1.0 / 50.0;
const WAVE_WAIT: f64 = 1.0 / 20.0; // the timeline wave keeps moving
const RETRY: Duration = Duration::from_secs(120); // look for lyrics again after a miss

/// What the tray icon says.
pub type Status = Arc<Mutex<String>>;

fn set(status: &Status, text: &str) {
    let mut now = status.lock().unwrap_or_else(|e| e.into_inner());
    if *now != text {
        *now = text.to_string();
    }
}

pub fn unix_now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

fn sleep(seconds: f64) {
    std::thread::sleep(Duration::from_secs_f64(seconds.max(0.001)));
}

fn next_wait(lyrics: Option<&Lyrics>, pos: i64, karaoke: bool, animator: &SheetAnimator) -> f64 {
    let Some(ly) = lyrics.filter(|l| !l.lines.is_empty()) else { return LINE_WAIT };
    let items = build_sheet_items(&ly.lines);
    if animator.is_animating(&ly.lines, &items, pos, karaoke) {
        return ANIM_WAIT;
    }
    items
        .iter()
        .find(|it| it.start_ms > pos)
        .map_or(LINE_WAIT, |it| ((it.start_ms - pos) as f64 / 1000.0).clamp(0.02, LINE_WAIT))
}

pub fn compress(raw: &[u8]) -> Vec<u8> {
    let mut z = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::fast());
    // writing into a Vec cannot fail
    let _ = z.write_all(raw);
    z.finish().unwrap_or_default()
}

pub fn clean(np: &mut NowPlaying) {
    (np.title, np.artist) = clean_title(&np.title, &np.artist); // the cleaned name is shown on screen too
}

type Key = (String, String, Vec<String>, bool);

#[derive(Default)]
struct Shelf {
    found: HashMap<Key, Option<Arc<Lyrics>>>,
    pending: HashMap<Key, mpsc::Receiver<Option<Lyrics>>>,
    retry_at: HashMap<Key, Instant>,
}

impl Shelf {
    /// The lyrics for this song if they are here; otherwise they are being looked for.
    fn get(&mut self, key: &Key, duration_ms: i64) -> Option<Arc<Lyrics>> {
        if self.retry_at.get(key).is_some_and(|&t| Instant::now() >= t) {
            // a source may have timed out: look again
            self.found.remove(key);
            self.retry_at.remove(key);
        }
        if !self.found.contains_key(key) && !self.pending.contains_key(key) {
            log::line(&format!("now playing: {} - {}", key.1, key.0));
            let (send, receive) = mpsc::channel();
            let (title, artist, order, prefer_word) = key.clone();
            std::thread::spawn(move || {
                let _ = send.send(sources::fetch(&sources::internet, &title, &artist, duration_ms, &order, prefer_word));
            });
            self.pending.insert(key.clone(), receive);
        }
        if let Some(result) = self.pending.get(key).map(|r| r.try_recv()) {
            if !matches!(result, Err(mpsc::TryRecvError::Empty)) {
                self.pending.remove(key);
                let ly = result.ok().flatten();
                match &ly {
                    Some(l) => log::line(&format!("lyrics: {} ({})", l.source, if l.word_synced { "word" } else { "line" })),
                    None => {
                        log::line("lyrics: none");
                        self.retry_at.insert(key.clone(), Instant::now() + RETRY);
                    }
                }
                self.found.insert(key.clone(), ly.map(Arc::new));
            }
        }
        self.found.get(key).cloned().flatten()
    }
}

/// The frame loop; never returns.
pub fn run(host: &str, status: Status) {
    let mut client = Client::new(host);
    let mut reply = client.settings().unwrap_or_else(|| Settings { in_music: false, ..Default::default() });
    let mut reader = media::Reader::default();
    let mut app = String::new(); // media app being shown; stay with it while it plays
    let mut shelf = Shelf::default();
    let mut animator = SheetAnimator::new();
    let mut renderer = Renderer::default();
    let mut shown = None; // the frame the screen is showing, so only changes are streamed
    let mut np: Option<NowPlaying> = None;
    let mut read_at = 0.0;
    let mut lag = 0.02; // seconds from send to pixels on screen, smoothed
    let mut media_error = String::new();
    let began = Instant::now();
    log::line(&format!("sending to {host}"));
    loop {
        let now = unix_now();
        if now - read_at >= 1.0 {
            np = match reader.read(&app) {
                Ok(found) => {
                    media_error.clear();
                    found
                }
                Err(e) => {
                    let message = e.to_string();
                    if message != media_error {
                        log::line(&format!("media session: {message}"));
                        media_error = message;
                    }
                    reader = media::Reader::default();
                    None
                }
            };
            if let Some(found) = np.as_mut() {
                if found.playing {
                    app = found.app.clone();
                }
                clean(found);
            }
            read_at = now;
        }
        let Some(playing) = np.as_ref().filter(|n| n.playing) else {
            shown = None;
            set(&status, "Chưa phát nhạc");
            sleep(0.5); // nothing sent: the screen returns to its GIF after 5 s
            continue;
        };
        let key = (playing.title.clone(), playing.artist.clone(), reply.sources.clone(), reply.word_sync);
        let lyrics = shelf.get(&key, playing.duration_ms);
        let lyrics = lyrics.as_deref();

        let sent_at = Instant::now();
        let pos = interpolate(playing, now + lag) + reply.offset_ms;
        let karaoke = reply.word_sync && lyrics.is_some_and(|l| l.word_synced);
        let t = began.elapsed().as_secs_f64() + 10.0;
        let frame = renderer.screen(playing, pos, lyrics, karaoke, Some(&mut animator), t);
        let wait = next_wait(lyrics, pos, karaoke, &animator).min(WAVE_WAIT);
        let port = reply.stream;
        let mut got = None;
        for r in dirty(shown.as_ref(), &frame) {
            let whole = r == Rect { x: 0, y: 0, w: W, h: H };
            let pixels = if whole { rgb565(&frame) } else { rgb565(&frame.crop(r.x, r.y, r.w, r.h)) };
            got = client.send(&compress(&pixels), r, port);
            if got.is_none() {
                break;
            }
        }
        let took = sent_at.elapsed().as_secs_f64();
        let Some(got) = got else {
            shown = None;
            set(&status, "Không thấy màn");
            sleep(2.0);
            continue;
        };
        set(&status, &format!("{} - {}", playing.artist, playing.title));
        // only trust it as "on screen" if the screen says it is showing music
        let visible = got.music_mode && got.in_music;
        shown = visible.then_some(frame);
        lag = 0.7 * lag + 0.3 * took.min(1.0);
        reply = got;
        // a screen that ignores frames is checked now and then in case music is turned back on
        sleep(if visible { wait - took } else { 2.0 - took }); // the send already used part of the wait
    }
}
