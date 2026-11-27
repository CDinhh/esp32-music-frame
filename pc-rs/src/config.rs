//! Settings from an optional .env next to the .exe: screen address and lrc.red contact.
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Folder of the running .exe: where .env and the log live.
pub fn exe_dir() -> PathBuf {
    std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf)).unwrap_or_else(|| ".".into())
}

/// KEY=VALUE lines; '#' comments and surrounding quotes ignored.
pub fn parse(text: &str) -> HashMap<String, String> {
    text.trim_start_matches('\u{feff}')
        .lines()
        .map(str::trim)
        .filter(|l| !l.starts_with('#'))
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.trim().to_string(), v.trim().trim_matches('"').trim_matches('\'').to_string()))
        .collect()
}

/// A value from the .env beside the exe (read once); empty if absent.
pub fn get(key: &str) -> String {
    static VALUES: OnceLock<HashMap<String, String>> = OnceLock::new();
    VALUES
        .get_or_init(|| std::fs::read_to_string(exe_dir().join(".env")).map(|t| parse(&t)).unwrap_or_default())
        .get(key)
        .cloned()
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    #[test]
    fn reads_env_lines() {
        let c = super::parse("\u{feff}# comment\nSCREEN_HOST=192.168.1.5\nWIFI_PASS=\"p=a#ss\"\n\nBROKEN\n");
        assert_eq!(c["SCREEN_HOST"], "192.168.1.5");
        assert_eq!(c["WIFI_PASS"], "p=a#ss");
        assert!(!c.contains_key("BROKEN"));
    }
}
