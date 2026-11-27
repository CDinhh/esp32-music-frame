//! Strip YouTube-style noise so lyric searches hit.
use regex_lite::Regex;
use std::sync::OnceLock;

struct Patterns {
    brackets: Regex,
    feat: Regex,
    cover_tag: Regex,
    cover_tail: Regex,
    topic: Regex,
    channel: Regex,
}

fn patterns() -> &'static Patterns {
    static P: OnceLock<Patterns> = OnceLock::new();
    P.get_or_init(|| {
        let r = |s: &str| Regex::new(s).expect("pattern");
        let noise = r"official|m/?v|music video|video|lyrics?|lyric video|audio|visuali[sz]er|hd|4k|mv|vietsub|engsub|karaoke";
        Patterns {
            brackets: r(&format!(r"(?i)\s*[\(\[【「（][^\)\]】」）]*(?:{noise})[^\)\]】」）]*[\)\]】」）]")),
            feat: r(r"(?i)\s*[\(\[]?\s*\b(?:ft|feat|featuring)\.?\s.*$"),
            // "(cover)", "/ Acoustic Cover", "- Female Cover by X"
            cover_tag: r(r"(?i)\s*[\(\[【「（][^\)\]】」）]*\bcover(?:ed)?\b[^\)\]】」）]*[\)\]】」）]|\s*(?:/|~|\s[-–—])\s*(?:\w+\s+){0,2}cover(?:\s+by\s.*)?$"),
            // a bare trailing "cover" after "Artist - Song"
            cover_tail: r(r"(?i)\s+(?:(?:acoustic|piano|guitar)\s+)?cover(?:\s+by\s.*)?$"),
            topic: r(r"(?i)\s*-\s*Topic$"),
            channel: r(r"(?i)(VEVO|Official)$"),
        }
    })
}

/// "(Official MV)", "| Label", "feat. X", "X - Topic", cover uploads -> (title, artist).
pub fn clean_title(title: &str, artist: &str) -> (String, String) {
    let p = patterns();
    let t = p.brackets.replace_all(title, "").into_owned();
    let t = t.split(" | ").next().unwrap_or("").split('｜').next().unwrap_or("");
    let mut t = p.feat.replace_all(t, "").trim().to_string();
    let a = p.topic.replace_all(artist, "").into_owned();
    let a = p.channel.replace_all(&a, "").trim().to_string();
    let mut a = p.feat.replace_all(&a, "").trim().to_string();

    let mut original = p.cover_tag.replace_all(&t, "").trim().to_string();
    if original.contains(" - ") {
        original = p.cover_tail.replace_all(&original, "").trim().to_string();
    }
    let covered = !original.is_empty() && original != t;
    if covered {
        t = original;
    }
    if let Some((left, right)) = t.split_once(" - ") {
        let (left, right) = (left.trim().to_string(), right.trim().to_string());
        // on a cover the channel is the singer, not the song's artist the lyrics are filed under
        let al = a.to_lowercase();
        if covered || a.is_empty() || left.to_lowercase() == al || left.to_lowercase().contains(&al) {
            a = left;
            t = right;
        }
    }
    (t.trim().to_string(), a.trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::clean_title;

    fn check(title: &str, artist: &str, want_title: &str, want_artist: &str) {
        assert_eq!(clean_title(title, artist), (want_title.to_string(), want_artist.to_string()), "{title} / {artist}");
    }

    #[test]
    fn strips_noise() {
        check("Song (Official MV) | Label", "Artist", "Song", "Artist");
        check("Artist - Song [Lyrics]", "ArtistVEVO - Topic", "Song", "Artist");
        check("Song ft. Other", "A", "Song", "A");
        check("心做し", "majiko", "心做し", "majiko");
        check("Tình yêu (Official MV)", "Trang ft. BigDaddy", "Tình yêu", "Trang");
    }

    #[test]
    fn finds_the_original_of_a_cover() {
        check("Lana Del Rey - Say Yes To Heaven cover", "Mina", "Say Yes To Heaven", "Lana Del Rey");
        check("Djo - End of Beginning / Acoustic Cover", "Lime", "End of Beginning", "Djo");
        check("Conan gray - Heather (cover)", "someone", "Heather", "Conan gray");
        check("Heather (Cover by Mina)", "Mina", "Heather", "Mina");
        check("Djo - Under Cover of Darkness", "Djo", "Under Cover of Darkness", "Djo");
        check("Cover Me", "Artist", "Cover Me", "Artist");
        check("Undercover", "Artist", "Undercover", "Artist");
    }
}
