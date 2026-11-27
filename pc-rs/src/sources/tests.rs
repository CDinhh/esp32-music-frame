use super::*;
use crate::model::Word;

fn order(ids: &[&str]) -> Vec<String> {
    ids.iter().map(|s| s.to_string()).collect()
}

#[test]
fn a_longer_track_with_an_outro_still_fits() {
    assert!(fits(317_000.0, 350_621)); // MV with an extra ending
    assert!(fits(351_000.0, 350_621));
    assert!(!fits(360_000.0, 350_621)); // lyrics recording longer than the track
    assert!(!fits(200_000.0, 350_621)); // a different edit
    assert!(fits(123.0, 0));
}

#[test]
fn ranks_like_sonora() {
    let full: Vec<Line> = (0..25).map(|i| Line::new(i * 10_000, "x")).collect();
    let word = Lyrics::new(full.clone(), true, "kugou", 262_000);
    let exact = Lyrics::new(full.clone(), false, "ytmusic", 262_000);
    let far = Lyrics::new(full.clone(), false, "ytmusic", 250_000);
    let cut = Lyrics::new(full[..5].to_vec(), true, "lrcred", 262_000);
    let o = order(&["ytmusic", "lrcred", "kugou"]);
    let first = |hits: &[&Lyrics], prefer| rank(hits.iter().map(|&h| h.clone()).collect(), &o, 262_000, prefer)[0].id;
    assert_eq!(first(&[&exact, &word, &cut], true), word.id);
    assert_eq!(first(&[&word, &exact], false), exact.id);
    assert_eq!(first(&[&far, &exact], false), exact.id);
    assert_eq!(first(&[&cut, &exact], true), exact.id);
    let _ = Word::new(0, 1, "x");
}

fn canned(request: &Request) -> Result<String, Fail> {
    let url = request.url.as_str();
    let has = |name: &str, value: &str| request.query.iter().any(|q| q.0 == name && q.1 == value);
    Ok(match url {
        "https://lrclib.net/api/get" => return Err(Fail::Status(404)),
        "https://lrclib.net/api/search" => json!([
            {"duration": 500, "syncedLyrics": "[00:01.00]wrong"},
            {"duration": 199, "syncedLyrics": "[00:01.00]lrclib line"}])
        .to_string(),
        "https://music.163.com/api/search/get" => json!({"result": {"songs": [{"id": 7, "duration": 200000}]}}).to_string(),
        "https://music.163.com/api/song/lyric" => {
            assert!(has("id", "7"));
            json!({"yrc": {"lyric": "[1000,500](1000,500,0)net"}}).to_string()
        }
        "https://lrc.red/api/v1" => json!({"results": [
            {"lyricsUrl": "https://lrc.red/x.ttml", "track_name": "Song", "duration": 200, "timing_type": "word"}]})
        .to_string(),
        "https://lrc.red/x.ttml" => crate::parse::tests::TTML.to_string(),
        "http://lyrics.kugou.com/search" => json!({"candidates": [{"id": "1", "accesskey": "k", "duration": 900000}]}).to_string(),
        "https://music.youtube.com" => r#"ytcfg.set({"VISITOR_DATA":"abc"});"#.to_string(),
        "https://music.youtube.com/youtubei/v1/search?alt=json" => {
            let column = |t: &str| json!({"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": t}]}}});
            json!({"contents": [{"musicResponsiveListItemRenderer": {
                "playlistItemData": {"videoId": "vid"}, "flexColumns": [column("Song"), column("3:20")]}}]})
            .to_string()
        }
        "https://music.youtube.com/youtubei/v1/next?alt=json" => json!({"tabs": [{"browseId": "MPLYt_1"}]}).to_string(),
        "https://music.youtube.com/youtubei/v1/browse?alt=json" => json!({"timedLyricsData": [
            {"lyricLine": "yt line", "cueRange": {"startTimeMilliseconds": "1200"}}]})
        .to_string(),
        _ if url.contains("musixmatch") => json!({"message": {"body": {"user_token": "00000"}}}).to_string(),
        other => return Err(Fail::Other(format!("unexpected {other}"))),
    })
}

#[test]
fn each_source_reads_its_answer() {
    let one = |id| ask(&canned, id, "Song", "Artist", 200_000);
    let first = |id| one(id).unwrap().unwrap().lines[0].text.clone();
    assert_eq!(first("lrclib"), "lrclib line");
    assert_eq!(first("netease"), "net");
    assert!(!first("lrcred").is_empty());
    let yt = one("ytmusic").unwrap().unwrap();
    assert_eq!((yt.lines[0].start_ms, yt.lines[0].text.as_str(), yt.duration_ms), (1200, "yt line", 200_000));
    assert!(one("kugou").unwrap().is_none(), "a far longer recording is refused");
    assert!(one("musixmatch").is_err(), "refused token");
    assert!(one("musixmatch").unwrap().is_none(), "then stays quiet");
}

#[test]
fn a_broken_source_does_not_stop_the_others() {
    let web = |request: &Request| match request.url.as_str() {
        u if u.contains("163.com") => Ok("<html>not json".to_string()),
        _ => canned(request),
    };
    let best = fetch(&web, "Song", "Artist", 0, &order(&["netease", "nonsense", "lrclib"]), true).unwrap();
    assert_eq!(best.source, "lrclib");
    assert!(fetch(&web, "Song", "Artist", 0, &[], true).is_none());
}

#[test]
fn lrcred_gets_contact_only_when_both_are_set() {
    assert_eq!(lrcred_agent("", "a@b.c"), UA);
    assert_eq!(lrcred_agent("https://x", "a@b.c"), "esp32-music-frame/0.1 (+https://x; a@b.c)");
}
