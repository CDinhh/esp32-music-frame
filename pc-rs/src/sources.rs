//! Lyric sources. LRCLIB is a public API; the others are unofficial and may break.
use crate::config;
use crate::log;
use crate::model::{Line, Lyrics};
use crate::parse::{decode_krc, parse_krc, parse_lrc, parse_richsync, parse_ttml, parse_yrc};
use serde_json::{json, Value};
use std::io::Read;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

pub const IDS: [&str; 6] = ["ytmusic", "lrcred", "lrclib", "netease", "kugou", "musixmatch"];
const UA: &str = "esp32-music-frame/0.1";
const MAX_BODY: u64 = 4 << 20;

#[derive(Debug, Default)]
pub struct Request {
    pub url: String,
    pub query: Vec<(&'static str, String)>,
    pub headers: Vec<(&'static str, String)>,
    /// POST with this JSON body, else GET
    pub json: Option<Value>,
}

impl Request {
    fn get(url: impl Into<String>) -> Self {
        Request { url: url.into(), ..Default::default() }
    }
    fn query(mut self, name: &'static str, value: impl ToString) -> Self {
        self.query.push((name, value.to_string()));
        self
    }
    fn header(mut self, name: &'static str, value: impl Into<String>) -> Self {
        self.headers.push((name, value.into()));
        self
    }
}

#[derive(Debug, PartialEq)]
pub enum Fail {
    Status(u16),
    Other(String),
}

/// The network; tests give canned answers instead.
pub type Web = dyn Fn(&Request) -> Result<String, Fail> + Sync;
type Found = Result<Option<Lyrics>, Fail>;

pub fn internet(request: &Request) -> Result<String, Fail> {
    static AGENT: OnceLock<ureq::Agent> = OnceLock::new();
    let agent = AGENT.get_or_init(|| {
        let mut builder = ureq::AgentBuilder::new().timeout(Duration::from_secs(4));
        if let Ok(tls) = native_tls::TlsConnector::new() {
            builder = builder.tls_connector(Arc::new(tls)); // Windows' own TLS: nothing to bundle
        }
        builder.build()
    });
    let method = if request.json.is_some() { "POST" } else { "GET" };
    let mut call = agent.request(method, &request.url).set("User-Agent", UA);
    for (name, value) in &request.query {
        call = call.query(name, value);
    }
    for (name, value) in &request.headers {
        call = call.set(name, value);
    }
    let sent = match &request.json {
        Some(body) => call.send_string(&body.to_string()),
        None => call.call(),
    };
    let reply = sent.map_err(|e| match e {
        ureq::Error::Status(code, _) => Fail::Status(code),
        other => Fail::Other(other.kind().to_string()),
    })?;
    let mut bytes = Vec::new();
    reply.into_reader().take(MAX_BODY).read_to_end(&mut bytes).map_err(|e| Fail::Other(e.to_string()))?;
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

fn get_json(web: &Web, request: Request) -> Result<Value, Fail> {
    serde_json::from_str(&web(&request)?).map_err(|_| Fail::Other("not JSON".into()))
}

fn text<'a>(v: &'a Value, key: &str) -> &'a str {
    v.get(key).and_then(Value::as_str).unwrap_or_default()
}

fn number(v: &Value, key: &str) -> f64 {
    match v.get(key) {
        Some(Value::String(s)) => s.parse().unwrap_or(0.0),
        other => other.and_then(Value::as_f64).unwrap_or(0.0),
    }
}

fn list<'a>(v: Option<&'a Value>) -> &'a [Value] {
    v.and_then(Value::as_array).map_or(&[], Vec::as_slice)
}

/// Can timed lyrics of a recording this long follow the track that is playing?
/// Equal length (+-3 s), or a shorter recording when the track only adds an ending (MV outros, up to ~15 %).
fn fits(candidate_ms: f64, track_ms: i64) -> bool {
    if track_ms <= 0 || candidate_ms == 0.0 {
        return true;
    }
    let extra = track_ms as f64 - candidate_ms;
    -3000.0 <= extra && extra <= (0.15 * track_ms as f64).max(3000.0)
}

/// Items whose length fits, closest first.
fn closest<'a>(items: impl IntoIterator<Item = &'a Value>, length_ms: impl Fn(&Value) -> f64, track_ms: i64) -> Vec<&'a Value> {
    let mut ok: Vec<&Value> = items.into_iter().filter(|i| fits(length_ms(i), track_ms)).collect();
    if track_ms > 0 {
        ok.sort_by(|a, b| (track_ms as f64 - length_ms(a)).abs().total_cmp(&(track_ms as f64 - length_ms(b)).abs()));
    }
    ok
}

fn sheet(lines: Vec<Line>, worded: bool, source: &str, duration_ms: f64) -> Option<Lyrics> {
    (!lines.is_empty()).then(|| Lyrics::new(lines, worded, source, duration_ms as i64))
}

fn same_song(a: &str, b: &str) -> bool {
    let (a, b) = (a.to_lowercase(), b.to_lowercase());
    let (a, b) = (a.trim(), b.trim());
    !a.is_empty() && (a.contains(b) || b.contains(a))
}

fn lrclib(web: &Web, title: &str, artist: &str, track_ms: i64) -> Found {
    let mut request = Request::get("https://lrclib.net/api/get").query("track_name", title).query("artist_name", artist);
    if track_ms > 0 {
        request = request.query("duration", (track_ms as f64 / 1000.0).round());
    }
    let exact = match get_json(web, request) {
        Ok(hit) => Some(hit),
        Err(Fail::Status(_)) => None,
        Err(e) => return Err(e),
    };
    // exact match missing or plain-text only: try other uploads
    let hit = match exact.filter(|h| !text(h, "syncedLyrics").is_empty()) {
        Some(hit) => Some(hit),
        None => {
            let query = format!("{artist} {title}");
            let hits = get_json(web, Request::get("https://lrclib.net/api/search").query("q", query.trim()))?;
            let synced = list(Some(&hits)).iter().filter(|h| !text(h, "syncedLyrics").is_empty());
            closest(synced, |h| number(h, "duration") * 1000.0, track_ms).first().map(|&h| h.clone())
        }
    };
    Ok(hit.and_then(|h| sheet(parse_lrc(text(&h, "syncedLyrics")), false, "lrclib", number(&h, "duration") * 1000.0)))
}

fn netease(web: &Web, title: &str, artist: &str, track_ms: i64) -> Found {
    let search = Request::get("https://music.163.com/api/search/get")
        .query("s", format!("{title} {artist}"))
        .query("type", 1)
        .query("limit", 5);
    let found = get_json(web, search)?;
    let songs = list(found.pointer("/result/songs"));
    let song = if track_ms > 0 {
        closest(songs, |s| number(s, "duration"), track_ms).first().copied()
    } else {
        songs.first()
    };
    let Some(song) = song else { return Ok(None) };
    let Some(id) = song.get("id").and_then(Value::as_i64) else { return Ok(None) };
    let length = number(song, "duration");
    let data = get_json(web, Request::get("https://music.163.com/api/song/lyric").query("id", id).query("lv", 1).query("yv", 1))?;
    let part = |p: &str| data.pointer(p).and_then(Value::as_str).unwrap_or_default();
    let words = parse_yrc(part("/yrc/lyric"));
    if !words.is_empty() {
        return Ok(sheet(words, true, "netease", length));
    }
    Ok(sheet(parse_lrc(part("/lrc/lyric")), false, "netease", length))
}

// Kugou's lyric host times out on https; lyrics are public text, so plain http is acceptable here.
fn kugou(web: &Web, title: &str, artist: &str, track_ms: i64) -> Found {
    let search = Request::get("http://lyrics.kugou.com/search")
        .query("ver", 1)
        .query("man", "yes")
        .query("client", "pc")
        .query("keyword", format!("{artist} - {title}"))
        .query("duration", track_ms);
    let found = get_json(web, search)?;
    // nothing that fits, e.g. only a live version 20 s longer: its timing would lag
    let candidates = closest(list(found.get("candidates")), |c| number(c, "duration"), track_ms);
    let Some(c) = candidates.first() else { return Ok(None) };
    let id = match c.get("id") {
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
        None => return Ok(None),
    };
    let download = Request::get("http://lyrics.kugou.com/download")
        .query("ver", 1)
        .query("client", "pc")
        .query("id", id)
        .query("accesskey", text(c, "accesskey"))
        .query("fmt", "krc")
        .query("charset", "utf8");
    let got = get_json(web, download)?;
    let lines = decode_krc(text(&got, "content")).map(|k| parse_krc(&k)).unwrap_or_default();
    Ok(sheet(lines, true, "kugou", number(c, "duration")))
}

struct Musixmatch {
    token: Option<String>,
    blocked_until: Option<Instant>,
}

static MXM: Mutex<Musixmatch> = Mutex::new(Musixmatch { token: None, blocked_until: None });
const MXM_BACK_OFF: Duration = Duration::from_secs(3600);

impl Musixmatch {
    fn raw(web: &Web, method: &str, params: &[(&'static str, String)]) -> Result<Value, Fail> {
        let mut request = Request::get(format!("https://apic-desktop.musixmatch.com/ws/1.1/{method}"))
            .query("app_id", "web-desktop-app-v1.0")
            .query("format", "json")
            .header("Cookie", "x-mxm-token-guid=");
        request.query.extend_from_slice(params);
        get_json(web, request)
    }

    fn refuse(&mut self) -> Fail {
        self.token = None;
        self.blocked_until = Some(Instant::now() + MXM_BACK_OFF);
        Fail::Other("musixmatch refused".into())
    }

    /// The `body` of an answer.
    fn call(&mut self, web: &Web, method: &str, params: Vec<(&'static str, String)>) -> Result<Value, Fail> {
        let token = match &self.token {
            Some(t) => t.clone(),
            None => {
                let answer = Self::raw(web, "token.get", &[("user_language", "en".into())])?;
                let token = answer.pointer("/message/body/user_token").and_then(Value::as_str).unwrap_or_default();
                if token.chars().all(|c| c == '0') {
                    return Err(self.refuse()); // all-zero token = refused / captcha
                }
                self.token = Some(token.to_string());
                token.to_string()
            }
        };
        let mut all = vec![("usertoken", token)];
        all.extend(params);
        let answer = Self::raw(web, method, &all)?;
        if matches!(answer.pointer("/message/header/status_code").and_then(Value::as_i64), Some(401 | 402)) {
            return Err(self.refuse());
        }
        Ok(answer.pointer("/message/body").cloned().unwrap_or(Value::Null))
    }
}

fn musixmatch(web: &Web, title: &str, artist: &str, track_ms: i64) -> Found {
    let mut mxm = MXM.lock().unwrap_or_else(|e| e.into_inner());
    // refused a moment ago: stay quiet instead of failing on every song
    if mxm.blocked_until.is_some_and(|t| Instant::now() < t) {
        return Ok(None);
    }
    let body = mxm.call(web, "matcher.track.get", vec![("q_track", title.into()), ("q_artist", artist.into())])?;
    let Some(track) = body.get("track") else { return Ok(None) };
    let length = number(track, "track_length") * 1000.0;
    if !fits(length, track_ms) {
        return Ok(None);
    }
    let id = vec![("track_id", (number(track, "track_id") as i64).to_string())];
    if number(track, "has_richsync") != 0.0 {
        let body = mxm.call(web, "track.richsync.get", id.clone())?;
        if let Some(raw) = body.pointer("/richsync/richsync_body").and_then(Value::as_str) {
            if let Some(found) = sheet(parse_richsync(raw), true, "musixmatch", length) {
                return Ok(Some(found));
            }
        }
    }
    if number(track, "has_subtitles") != 0.0 {
        let mut params = id;
        params.push(("subtitle_format", "lrc".into()));
        let body = mxm.call(web, "track.subtitle.get", params)?;
        if let Some(raw) = body.pointer("/subtitle/subtitle_body").and_then(Value::as_str) {
            return Ok(sheet(parse_lrc(raw), false, "musixmatch", length));
        }
    }
    Ok(None)
}

/// Every value stored under `key`, anywhere in the answer, in document order.
fn find_all<'a>(v: &'a Value, key: &str, out: &mut Vec<&'a Value>) {
    match v {
        Value::Object(map) => {
            for (k, child) in map {
                if k == key {
                    out.push(child);
                }
                find_all(child, key, out);
            }
        }
        Value::Array(items) => items.iter().for_each(|i| find_all(i, key, out)),
        _ => {}
    }
}

fn youtube(web: &Web, endpoint: &str, mobile: bool, visitor: &str, mut body: Value) -> Result<Value, Fail> {
    // SAFETY: no arguments, returns a plain struct
    let today = unsafe { windows::Win32::System::SystemInformation::GetLocalTime() };
    let (name, version) = if mobile {
        ("ANDROID_MUSIC", "7.21.50".to_string())
    } else {
        ("WEB_REMIX", format!("1.{:04}{:02}{:02}.01.00", today.wYear, today.wMonth, today.wDay))
    };
    body["context"] = json!({"client": {"clientName": name, "clientVersion": version, "hl": "en"}, "user": {}});
    let mut request = Request::get(format!("https://music.youtube.com/youtubei/v1/{endpoint}?alt=json"))
        .header("Content-Type", "application/json")
        .header("Origin", "https://music.youtube.com")
        .header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:88.0) Gecko/20100101 Firefox/88.0")
        .header("Cookie", "SOCS=CAI");
    if !visitor.is_empty() {
        request = request.header("X-Goog-Visitor-Id", visitor);
    }
    request.json = Some(body);
    get_json(web, request)
}

/// The anonymous visitor id the web page hands out; searches work without it too.
fn visitor_id(web: &Web) -> String {
    static ID: Mutex<String> = Mutex::new(String::new());
    let mut id = ID.lock().unwrap_or_else(|e| e.into_inner());
    if id.is_empty() {
        let page = web(&Request::get("https://music.youtube.com").header("Cookie", "SOCS=CAI")).unwrap_or_default();
        let value = page.split_once("\"VISITOR_DATA\":\"").and_then(|(_, rest)| rest.split_once('"'));
        *id = value.map(|v| v.0.to_string()).unwrap_or_default();
    }
    id.clone()
}

/// "3:05" or "1:02:03" in milliseconds.
fn clock_ms(s: &str) -> Option<f64> {
    let parts: Vec<&str> = s.split(':').collect();
    if parts.len() < 2 || parts.len() > 3 {
        return None;
    }
    parts.iter().try_fold(0u64, |total, p| p.parse::<u64>().ok().map(|n| total * 60 + n)).map(|s| s as f64 * 1000.0)
}

/// YouTube Music's own timed lyrics (LyricFind / Musixmatch behind it); no sign-in needed.
fn ytmusic(web: &Web, title: &str, artist: &str, track_ms: i64) -> Found {
    let visitor = visitor_id(web);
    let songs_only = "EgWKAQIIAWoMEA4QChADEAQQCRAF";
    let found = youtube(web, "search", false, &visitor, json!({"query": format!("{title} {artist}"), "params": songs_only}))?;
    let mut items = Vec::new();
    find_all(&found, "musicResponsiveListItemRenderer", &mut items);
    let songs: Vec<Value> = items
        .iter()
        .take(8)
        .filter_map(|item| {
            let id = item.pointer("/playlistItemData/videoId")?.as_str()?;
            let column = |i: usize| list(item.pointer(&format!("/flexColumns/{i}/musicResponsiveListItemFlexColumnRenderer/text/runs")));
            let name = column(0).first().map_or("", |r| text(r, "text"));
            let length = column(1).iter().find_map(|r| clock_ms(text(r, "text"))).unwrap_or(0.0);
            same_song(title, name).then(|| json!({"id": id, "ms": length}))
        })
        .collect();
    for song in closest(&songs, |s| number(s, "ms"), track_ms).into_iter().take(2) {
        let id = text(song, "id");
        let watch = youtube(
            web,
            "next",
            false,
            &visitor,
            json!({
                "videoId": id, "playlistId": format!("RDAMVM{id}"), "isAudioOnly": true,
                "enablePersistentPlaylistPanel": true, "tunerSettingValue": "AUTOMIX_SETTING_NORMAL",
                "watchEndpointMusicSupportedConfigs": {"watchEndpointMusicConfig": {
                    "hasPersistentPlaylistPanel": true, "musicVideoType": "MUSIC_VIDEO_TYPE_ATV"}},
            }),
        )?;
        let mut ids = Vec::new();
        find_all(&watch, "browseId", &mut ids);
        let Some(page) = ids.iter().filter_map(|v| v.as_str()).find(|s| s.starts_with("MPLY")) else { continue };
        let answer = youtube(web, "browse", true, &visitor, json!({"browseId": page}))?;
        let mut sheets = Vec::new();
        find_all(&answer, "timedLyricsData", &mut sheets);
        let lines: Vec<Line> = list(sheets.first().copied())
            .iter()
            .filter_map(|l| {
                let range = l.get("cueRange")?;
                range.get("startTimeMilliseconds")?;
                Some(Line::new(number(range, "startTimeMilliseconds") as i64, text(l, "lyricLine")))
            })
            .collect();
        if let Some(found) = sheet(lines, false, "ytmusic", number(song, "ms")) {
            return Ok(Some(found));
        }
    }
    Ok(None)
}

/// lrc.red's terms: name the app with a link and a contact email (CONTACT_LINK / CONTACT_EMAIL in .env).
/// Without them only the app name is sent.
fn lrcred_agent(link: &str, email: &str) -> String {
    if link.is_empty() || email.is_empty() {
        UA.to_string()
    } else {
        format!("{UA} (+{link}; {email})")
    }
}

/// Apple Music sheets through lrc.red (TTML, often timed word by word). Its terms ask for a credit, drawn on screen.
fn lrcred(web: &Web, title: &str, artist: &str, track_ms: i64) -> Found {
    let agent = lrcred_agent(&config::get("CONTACT_LINK"), &config::get("CONTACT_EMAIL"));
    let mut request = Request::get("https://lrc.red/api/v1")
        .query("track", title)
        .query("artist", artist)
        .header("User-Agent", agent.clone());
    if track_ms > 0 {
        request = request.query("duration", (track_ms as f64 / 1000.0).round());
    }
    let answer = match get_json(web, request) {
        Err(Fail::Status(404)) => return Ok(None),
        other => other?,
    };
    let usable = list(answer.get("results"))
        .iter()
        .filter(|f| text(f, "lyricsUrl").starts_with("https://") && same_song(title, text(f, "track_name")));
    let mut found = closest(usable, |f| number(f, "duration") * 1000.0, track_ms);
    found.sort_by_key(|f| !matches!(text(f, "timing_type"), "word" | "syllable")); // stable: word-timed first, then closest
    for f in found.into_iter().take(2) {
        let ttml = web(&Request::get(text(f, "lyricsUrl")).header("User-Agent", agent.clone()))?;
        let (lines, worded) = parse_ttml(&ttml);
        if !lines.is_empty() && text(f, "timing_type") != "none" {
            return Ok(sheet(lines, worded, "lrcred", number(f, "duration") * 1000.0));
        }
    }
    Ok(None)
}

fn ask(web: &Web, id: &str, title: &str, artist: &str, track_ms: i64) -> Found {
    match id {
        "ytmusic" => ytmusic(web, title, artist, track_ms),
        "lrcred" => lrcred(web, title, artist, track_ms),
        "lrclib" => lrclib(web, title, artist, track_ms),
        "netease" => netease(web, title, artist, track_ms),
        "kugou" => kugou(web, title, artist, track_ms),
        "musixmatch" => musixmatch(web, title, artist, track_ms),
        _ => Ok(None),
    }
}

/// Kind of lyrics, best first (after Sonora): word-timed (when preferred) > line-timed > cut short.
fn grade(lyrics: &Lyrics, track_ms: i64, prefer_word: bool) -> u8 {
    let last = lyrics.lines.last().map_or(0, |l| l.start_ms);
    if track_ms >= 30_000 && !lyrics.lines.is_empty() && (last as f64) < 0.6 * track_ms as f64 {
        0 // stops well before the song does: probably a partial sheet
    } else if prefer_word && lyrics.word_synced {
        3
    } else {
        2
    }
}

/// Kind first, then the user's source order, then the closest recording length.
fn rank(mut hits: Vec<Lyrics>, order: &[String], track_ms: i64, prefer_word: bool) -> Vec<Lyrics> {
    hits.sort_by_key(|h| {
        let place = order.iter().position(|o| *o == h.source).unwrap_or(order.len());
        let off = if track_ms != 0 && h.duration_ms != 0 { (track_ms - h.duration_ms).abs() } else { 0 };
        (std::cmp::Reverse(grade(h, track_ms, prefer_word)), place, off)
    });
    hits
}

/// Ask every enabled source at once (one slow source does not hold up the rest) and keep the best hit.
pub fn fetch(web: &Web, title: &str, artist: &str, track_ms: i64, order: &[String], prefer_word: bool) -> Option<Lyrics> {
    let hits: Vec<Lyrics> = std::thread::scope(|scope| {
        let asking: Vec<_> = order
            .iter()
            .filter(|id| IDS.contains(&id.as_str()))
            .map(|id| {
                scope.spawn(move || match ask(web, id, title, artist, track_ms) {
                    Ok(found) => found,
                    Err(e) => {
                        log::line(&format!("lyrics: {id} failed ({e:?})")); // unofficial APIs fail; never stop the app
                        None
                    }
                })
            })
            .collect();
        asking.into_iter().filter_map(|t| t.join().ok().flatten()).collect()
    });
    rank(hits, order, track_ms, prefer_word).into_iter().next()
}

#[cfg(test)]
mod tests;
