//! Pushes changed parts of the screen over a TCP stream; reads settings over HTTP.
use crate::log;
use crate::render::Rect;
use serde_json::Value;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs};
use std::time::Duration;

const TIMEOUT: Duration = Duration::from_millis(1500);

/// What the screen wants, as sent back after every frame.
#[derive(Debug, Clone, PartialEq)]
pub struct Settings {
    pub offset_ms: i64,
    pub word_sync: bool,
    pub music_mode: bool,
    pub in_music: bool,
    pub stream: u16,
    pub sources: Vec<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            offset_ms: 0,
            word_sync: false,
            music_mode: true,
            in_music: true,
            stream: 81,
            sources: crate::sources::IDS.iter().map(|s| s.to_string()).collect(),
        }
    }
}

/// A stream reply lists source ids; /api/settings lists {id, on}.
fn parse(v: &Value) -> Option<Settings> {
    let sources = v.get("sources")?.as_array()?;
    let flag = |name: &str, missing: bool| v.get(name).and_then(Value::as_bool).unwrap_or(missing);
    Some(Settings {
        offset_ms: v.get("offset_ms")?.as_i64()?,
        word_sync: flag("word_sync", false),
        music_mode: flag("music_mode", true),
        in_music: flag("in_music", true),
        stream: v.get("stream").and_then(Value::as_u64).and_then(|p| u16::try_from(p).ok()).filter(|&p| p > 0).unwrap_or(81),
        sources: sources
            .iter()
            .filter_map(|s| match s {
                Value::String(id) => Some(id.clone()),
                other => other.get("on")?.as_bool()?.then(|| other.get("id")?.as_str().map(String::from))?,
            })
            .collect(),
    })
}

pub struct Client {
    host: String,
    ip: String,
    stream: Option<BufReader<TcpStream>>,
    down: bool,
}

impl Client {
    pub fn new(host: &str) -> Self {
        let mut client = Self { host: host.into(), ip: String::new(), stream: None, down: false };
        client.resolve();
        client
    }

    /// Turn a name like musicframe.local into its current IP once (a .local lookup on Windows can
    /// take seconds, too slow per frame). Called again whenever the screen stops answering, so a
    /// new IP from the router is picked up by itself.
    fn resolve(&mut self) {
        let name = self.host.split(':').next().unwrap_or_default().to_string();
        let found = (name.as_str(), 80).to_socket_addrs().ok().and_then(|mut a| a.find(SocketAddr::is_ipv4));
        let ip = found.map(|a| a.ip().to_string()).unwrap_or_else(|| name.clone());
        if ip != self.ip && name.parse::<IpAddr>().is_err() && found.is_some() {
            log::line(&format!("screen {} is at {ip}", self.host));
        }
        self.ip = ip;
    }

    fn url(&self) -> String {
        match self.host.split_once(':') {
            Some((_, port)) => format!("http://{}:{port}", self.ip),
            None => format!("http://{}", self.ip),
        }
    }

    /// Read once at start, so the first lyric lookup already uses the user's source order.
    pub fn settings(&self) -> Option<Settings> {
        let reply = ureq::get(&format!("{}/api/settings", self.url())).timeout(Duration::from_secs(3)).call().ok()?;
        let mut body = String::new();
        reply.into_reader().take(64 * 1024).read_to_string(&mut body).ok()?;
        let mut v: Value = serde_json::from_str(&body).ok()?;
        v.as_object_mut()?.entry("in_music").or_insert(Value::Bool(false));
        parse(&v)
    }

    fn exchange(&mut self, payload: &[u8], r: Rect, port: u16) -> std::io::Result<Settings> {
        if self.stream.is_none() {
            let address = (self.ip.as_str(), port).to_socket_addrs()?.find(SocketAddr::is_ipv4);
            let address = address.ok_or_else(|| std::io::Error::other("no address"))?;
            let socket = TcpStream::connect_timeout(&address, TIMEOUT)?;
            socket.set_nodelay(true)?;
            socket.set_read_timeout(Some(TIMEOUT))?;
            socket.set_write_timeout(Some(TIMEOUT))?;
            self.stream = Some(BufReader::new(socket));
        }
        let stream = self.stream.as_mut().expect("just connected");
        let mut packet = Vec::with_capacity(14 + payload.len());
        packet.extend_from_slice(b"M6");
        for v in [r.x, r.y, r.w, r.h] {
            packet.extend_from_slice(&(v as u16).to_le_bytes());
        }
        packet.extend_from_slice(&(payload.len() as u32).to_le_bytes());
        packet.extend_from_slice(payload);
        stream.get_mut().write_all(&packet)?;
        let mut line = String::new();
        if stream.take(4096).read_line(&mut line)? == 0 {
            return Err(std::io::Error::other("stream closed"));
        }
        serde_json::from_str(&line).ok().as_ref().and_then(parse).ok_or_else(|| std::io::Error::other("bad reply"))
    }

    /// Send a zlib-compressed RGB565 rectangle; None if the screen did not answer.
    pub fn send(&mut self, payload: &[u8], r: Rect, port: u16) -> Option<Settings> {
        match self.exchange(payload, r, port) {
            Ok(reply) => {
                if self.down {
                    log::line("screen back online");
                }
                self.down = false;
                Some(reply)
            }
            Err(e) => {
                self.stream = None;
                if !self.down {
                    log::line(&format!("stream unreachable at {}:{port} ({e}); retrying", self.ip));
                }
                self.down = true;
                self.resolve();
                None
            }
        }
    }

    #[cfg(test)]
    pub fn is_down(&self) -> bool {
        self.down
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn sends_the_header_and_reads_the_reply() {
        let server = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = server.local_addr().unwrap().port();
        let thread = std::thread::spawn(move || {
            let (mut socket, _) = server.accept().unwrap();
            let mut got = [0u8; 14 + 3];
            socket.read_exact(&mut got).unwrap();
            socket
                .write_all(b"{\"offset_ms\":-120,\"word_sync\":true,\"music_mode\":true,\"in_music\":false,\"stream\":81,\"sources\":[\"kugou\"]}\n")
                .unwrap();
            got
        });
        let mut client = Client::new("127.0.0.1");
        let reply = client.send(&[9, 8, 7], Rect { x: 1, y: 2, w: 240, h: 5 }, port).unwrap();
        assert_eq!((reply.offset_ms, reply.word_sync, reply.in_music), (-120, true, false));
        assert_eq!(reply.sources, ["kugou"]);
        assert_eq!(thread.join().unwrap(), [b'M', b'6', 1, 0, 2, 0, 240, 0, 5, 0, 3, 0, 0, 0, 9, 8, 7]);
        // the screen went away: no panic, just None
        assert!(client.send(&[0], Rect { x: 0, y: 0, w: 1, h: 1 }, port).is_none());
        assert!(client.is_down());
    }

    #[test]
    fn settings_list_keeps_only_enabled_sources() {
        let v: Value = serde_json::from_str(r#"{"offset_ms":0,"sources":[{"id":"a","on":true},{"id":"b","on":false}]}"#).unwrap();
        assert_eq!(parse(&v).unwrap().sources, ["a"]);
        assert!(parse(&Value::Null).is_none());
    }
}
