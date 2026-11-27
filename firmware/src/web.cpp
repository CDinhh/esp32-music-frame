#include "web.h"
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <Update.h>
#include <WebServer.h>
#include <WiFi.h>
#include <miniz.h>
#include "display.h"
#include "gif_player.h"
#include "fetch.h"
#include "health.h"
#include "log.h"
#include "settings.h"
#include "validate.h"

#ifdef SIM_WOKWI
#define OTA_PASSWORD "sim"
#else
#ifndef OTA_PASSWORD
#error "OTA_PASSWORD comes from .env at the repo root (see .env.example)"
#endif
#endif

extern const char index_html_start[] asm("_binary_src_index_html_start");

namespace {
WebServer server(80);
bool gifChanged = false;
uint32_t lastFrameMs = 0;
bool inMusic = false;

void sendJson(int code, JsonDocument& doc) {
  String out;
  serializeJson(doc, out);
  server.send(code, "application/json", out);
}

void sendError(int code, const char* msg) {
  JsonDocument d;
  d["error"] = msg;
  sendJson(code, d);
}

// ---------- GIF upload ----------
struct Upload {
  String name;
  File f;
  size_t size = 0;
  int error = 0;  // http code, 0 = ok
  const char* msg = "";
} up;

void failUpload(int code, const char* msg) {
  if (!up.error) {
    up.error = code;
    up.msg = msg;
  }
  if (up.f) up.f.close();
  LittleFS.remove("/gifs/.tmp");
}

// WebServer calls the same callback for raw bodies; server.upload() is only valid for multipart.
bool isMultipart() { return server.header("Content-Type").startsWith("multipart/form-data"); }

void onGifUpload() {
  if (!isMultipart()) return failUpload(400, "expected multipart upload");
  HTTPUpload& u = server.upload();
  if (u.status == UPLOAD_FILE_START) {
    up = Upload();
    up.name = sanitizeName(u.filename.c_str()).c_str();
    if (up.name.isEmpty()) return failUpload(400, "bad file name");
    up.f = LittleFS.open("/gifs/.tmp", "w");
    if (!up.f) failUpload(500, "cannot write");
  } else if (u.status == UPLOAD_FILE_WRITE) {
    if (up.error) return;
    if (up.size == 0) {
      if (!isGifHeader(u.buf, u.currentSize)) return failUpload(400, "not a GIF");
      if (!gifDimsOk(u.buf, u.currentSize)) return failUpload(400, "GIF larger than 480 px");
    }
    up.size += u.currentSize;
    if (up.size > GIF_MAX_BYTES) return failUpload(413, "GIF larger than 2 MB");
    if (fsFree() < u.currentSize + 16384 || up.f.write(u.buf, u.currentSize) != u.currentSize)
      return failUpload(507, "storage full");
  } else if (u.status == UPLOAD_FILE_END) {
    if (up.error) return;
    up.f.close();
    String path = "/gifs/" + up.name;
    LittleFS.remove(path);
    if (!LittleFS.rename("/gifs/.tmp", path)) failUpload(500, "cannot save");
  } else if (u.status == UPLOAD_FILE_ABORTED) {
    failUpload(400, "upload aborted");
  }
}

void onGifUploadDone() {
  if (!isMultipart()) return sendError(400, "expected multipart upload");
  if (up.error) {
    logf("upload: rejected (%s)", up.msg);
    return sendError(up.error, up.msg);
  }
  if (up.size == 0) return sendError(400, "no file");
  logf("upload: saved %s (%u KB)", up.name.c_str(), (unsigned)(up.size / 1024));
  JsonDocument d;
  d["name"] = up.name;
  sendJson(201, d);
}

// ---------- GIF from a link ----------
void onGifFromUrl() {
  JsonDocument in;
  if (deserializeJson(in, server.arg("plain"))) return sendError(400, "invalid JSON");
  JsonArrayConst list = in["urls"];
  std::vector<String> urls;
  for (JsonVariantConst u : list) {
    const char* s = u | "";
    if (!validUrl(s)) return sendError(400, "bad link");
    urls.push_back(s);
  }
  if (urls.empty() || urls.size() > 6) return sendError(400, "bad link");
  std::string wanted = in["name"] | "";
  std::string name = sanitizeName(wanted.empty() ? urls[0].substring(urls[0].lastIndexOf('/') + 1).c_str() : wanted);
  if (name.empty()) name = "gif.gif";
  if (WiFi.status() != WL_CONNECTED) return sendError(503, "screen is offline");
  if (!fetchStart(urls, name.c_str())) return sendError(409, "another download is running");
  server.send(202, "application/json", "{\"state\":\"downloading\"}");
}

void onGifFromUrlStatus() {
  JsonDocument d;
  fetchToJson(d.to<JsonObject>());
  sendJson(200, d);
}

void onGifList() {
  JsonDocument d;
  JsonArray arr = d["gifs"].to<JsonArray>();
  for (auto& n : gifList())
    if (!n.startsWith(".")) arr.add(n);
  d["active"] = settings().gif;
  d["free"] = fsFree();
  sendJson(200, d);
}

void onGifDelete() {
  std::string name = server.arg("name").c_str();
  if (name.empty() || sanitizeName(name) != name) return sendError(400, "bad file name");
  String path = String("/gifs/") + name.c_str();
  if (!LittleFS.exists(path)) return sendError(404, "not found");
  bool wasActive = settings().gif == name.c_str();
  if (wasActive) gifClose();
  LittleFS.remove(path);
  logf("gif: deleted %s", name.c_str());
  if (wasActive) {
    auto list = gifList();
    settings().gif = "";
    for (auto& n : list)
      if (!n.startsWith(".")) { settings().gif = n; break; }
    saveSettings();
    gifChanged = true;
  }
  onGifList();
}

// ---------- settings ----------
void onSettingsGet() {
  JsonDocument d;
  settingsToJson(d.to<JsonObject>());
  d["partial"] = true;
  d["in_music"] = inMusic;
  d["stream"] = 81;
  sendJson(200, d);
}

void onSettingsPost() {
  JsonDocument in;
  if (deserializeJson(in, server.arg("plain"))) return sendError(400, "invalid JSON");
  String oldGif = settings().gif;
  if (!settingsFromJson(in.as<JsonObjectConst>())) return sendError(400, "invalid settings");
  if (!settings().gif.isEmpty() && !LittleFS.exists("/gifs/" + settings().gif)) {
    settings().gif = oldGif;
    return sendError(404, "GIF not found");
  }
  saveSettings();
  setBacklight(settings().brightness);
  if (settings().gif != oldGif) gifChanged = true;
  if (!settings().musicMode && inMusic) {  // turned off while showing music: back to GIF now
    inMusic = false;
    gifChanged = true;
  }
  onSettingsGet();
}

// ---------- OTA ----------
bool otaAuthed = false;
String otaError;
WiFiServer streamServer(81, 1);
WiFiClient streamClient;

void onUpdateUpload() {
  if (!isMultipart()) {
    otaAuthed = server.header("X-OTA-Password") == OTA_PASSWORD;
    otaError = "expected multipart upload";
    return;
  }
  HTTPUpload& u = server.upload();
  if (u.status == UPLOAD_FILE_START) {
    otaError = "";
    otaAuthed = server.header("X-OTA-Password") == OTA_PASSWORD;
    if (!otaAuthed) {
      logf("ota: wrong password, ignored");
      return;
    }
    logf("ota: receiving firmware");
    if (streamClient) streamClient.stop();
    gifClose();
    if (!Update.begin(UPDATE_SIZE_UNKNOWN, U_FLASH)) otaError = Update.errorString();
  } else if (u.status == UPLOAD_FILE_WRITE) {
    if (otaAuthed && otaError.isEmpty() && Update.write(u.buf, u.currentSize) != u.currentSize)
      otaError = Update.errorString();
  } else if (u.status == UPLOAD_FILE_END) {
    if (otaAuthed && otaError.isEmpty() && !Update.end(true)) otaError = Update.errorString();
  } else if (u.status == UPLOAD_FILE_ABORTED) {
    if (otaAuthed) Update.abort();
    otaError = "upload aborted";
  }
}

void onUpdateDone() {
  if (!otaAuthed) return sendError(401, "wrong password");
  if (!otaError.isEmpty()) {
    logf("ota: failed (%s), keeping current firmware", otaError.c_str());
    Update.abort();  // keep running the current app
    gifChanged = true;
    return sendError(500, otaError.c_str());
  }
  logf("ota: done, restarting");
  server.send(200, "application/json", "{\"ok\":true}");
  delay(300);
  ESP.restart();
}

// ---------- music frames ----------
constexpr size_t STREAM_MAX_BYTES = SCREEN * SCREEN * 3 + 1024;
uint8_t* frameBuf = nullptr;
uint8_t* streamBuf = nullptr;
uint8_t* rawBuf = nullptr;
uint16_t* dmaBuf = nullptr;
tinfl_decompressor* decomp = nullptr;
size_t frameLen = 0;
bool frameTooBig = false;

void fillFrameReply(JsonDocument& d) {
  d["offset_ms"] = settings().offsetMs;
  d["word_sync"] = settings().wordSync;
  d["music_mode"] = settings().musicMode;
  d["partial"] = true;
  d["in_music"] = inMusic;
  d["stream"] = 81;
  enabledSources(d["sources"].to<JsonArray>());
}

bool readExact(WiFiClient& c, uint8_t* dst, size_t n, uint32_t timeoutMs) {
  size_t got = 0;
  uint32_t start = millis();
  while (got < n) {
    int avail = c.available();
    if (avail > 0) {
      int r = c.read(dst + got, min((size_t)avail, n - got));
      if (r > 0) {
        got += (size_t)r;
        start = millis();
        continue;
      }
    }
    if (!c.connected() || millis() - start > timeoutMs) return false;
    delay(1);
  }
  return true;
}

void handleStream() {
  if (!streamBuf || !rawBuf || !dmaBuf || !decomp) return;
  if (streamServer.hasClient()) {
    if (streamClient) streamClient.stop();
    streamClient = streamServer.accept();
    if (streamClient) streamClient.setNoDelay(true);
  }
  if (!streamClient || !streamClient.connected() || streamClient.available() < 14) return;

  uint8_t hdr[14];
  if (!readExact(streamClient, hdr, 14, 200)) { streamClient.stop(); return; }
  if (hdr[0] != 'M' || hdr[1] != 'F') {
    logf("stream: bad magic");
    streamClient.stop();
    return;
  }
  uint16_t x = hdr[2] | (hdr[3] << 8);
  uint16_t y = hdr[4] | (hdr[5] << 8);
  uint16_t w = hdr[6] | (hdr[7] << 8);
  uint16_t h = hdr[8] | (hdr[9] << 8);
  uint32_t len = (uint32_t)hdr[10] | ((uint32_t)hdr[11] << 8) | ((uint32_t)hdr[12] << 16) | ((uint32_t)hdr[13] << 24);
  if (w == 0 || h == 0 || x + w > SCREEN || y + h > SCREEN || len == 0 || len > STREAM_MAX_BYTES) {
    logf("stream: bad rect %ux%u+%u+%u len=%u", w, h, x, y, (unsigned)len);
    streamClient.stop();
    return;
  }
  if (!readExact(streamClient, streamBuf, len, 1000)) {
    logf("stream: short read");
    streamClient.stop();
    return;
  }

  size_t want = (size_t)w * h * 3;
  size_t inLen = len;
  size_t outLen = want;
  tinfl_init(decomp);
  tinfl_status st = tinfl_decompress(
      decomp, streamBuf, &inLen, rawBuf, rawBuf, &outLen,
      TINFL_FLAG_PARSE_ZLIB_HEADER | TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF);
  if (st != TINFL_STATUS_DONE || outLen != want) {
    logf("stream: inflate err=%d (%u/%u)", (int)st, (unsigned)outLen, (unsigned)want);
    streamClient.stop();
    return;
  }

  if (settings().musicMode && (inMusic || (x == 0 && y == 0))) {
    if (!inMusic) {
      gifClose();
      logf("music: showing now playing");
    }
    inMusic = true;
    lastFrameMs = millis();
    display().waitDMA();
    const uint8_t* src = rawBuf;
    size_t px = (size_t)w * h;
    for (size_t i = 0; i < px; i++) {
      uint16_t c = ((src[0] & 0xF8) << 8) | ((src[1] & 0xFC) << 3) | (src[2] >> 3);
      dmaBuf[i] = (uint16_t)((c >> 8) | (c << 8));
      src += 3;
    }
    display().pushImageDMA(x, y, w, h, (lgfx::swap565_t*)dmaBuf);
  }
  JsonDocument d;
  fillFrameReply(d);
  String out;
  serializeJson(d, out);
  out += '\n';
  streamClient.write((const uint8_t*)out.c_str(), out.length());
}

void onFrameRaw() {
  HTTPRaw& r = server.raw();
  if (r.status == RAW_START) {
    frameLen = 0;
    frameTooBig = false;
  } else if (r.status == RAW_WRITE) {
    if (frameLen + r.currentSize > FRAME_MAX_BYTES) {
      frameTooBig = true;
      return;
    }
    memcpy(frameBuf + frameLen, r.buf, r.currentSize);
    frameLen += r.currentSize;
  }
}

void onFrameDone() {
  if (frameTooBig) return sendError(413, "frame larger than 100 KB");
  if (!isJpeg(frameBuf, frameLen)) return sendError(400, "not a JPEG");
  int x = server.hasHeader("X-Frame-X") ? server.header("X-Frame-X").toInt() : 0;
  int y = server.hasHeader("X-Frame-Y") ? server.header("X-Frame-Y").toInt() : 0;
  if (x < 0 || x >= SCREEN || y < 0 || y >= SCREEN) return sendError(400, "bad frame rect");
  if (settings().musicMode && (inMusic || (x == 0 && y == 0))) {
    if (!inMusic) {
      gifClose();
      logf("music: showing now playing");
    }
    inMusic = true;
    lastFrameMs = millis();
    display().waitDMA();
    display().drawJpg(frameBuf, frameLen, x, y);
  }
  JsonDocument d;
  fillFrameReply(d);
  sendJson(200, d);
}
}  // namespace

void webBegin() {
  frameBuf = (uint8_t*)ps_malloc(FRAME_MAX_BYTES);
  streamBuf = (uint8_t*)ps_malloc(STREAM_MAX_BYTES);
  rawBuf = (uint8_t*)ps_malloc(SCREEN * SCREEN * 3);
  dmaBuf = (uint16_t*)ps_malloc(SCREEN * SCREEN * 2);
  decomp = (tinfl_decompressor*)malloc(sizeof(tinfl_decompressor));
  if (!frameBuf || !streamBuf || !rawBuf || !dmaBuf || !decomp)
    logf("mem: stream alloc failed");
  const char* headers[] = {"X-OTA-Password", "Content-Type", "X-Frame-X", "X-Frame-Y"};
  server.collectHeaders(headers, 4);
  server.on("/", HTTP_GET, [] { server.send(200, "text/html; charset=utf-8", index_html_start); });
  server.on("/api/gifs", HTTP_GET, onGifList);
  server.on("/api/gifs", HTTP_POST, onGifUploadDone, onGifUpload);
  server.on("/api/gifs", HTTP_DELETE, onGifDelete);
  server.on("/api/gifs/url", HTTP_POST, onGifFromUrl);
  server.on("/api/gifs/url", HTTP_GET, onGifFromUrlStatus);
  server.on("/api/settings", HTTP_GET, onSettingsGet);
  server.on("/api/settings", HTTP_POST, onSettingsPost);
  server.on("/api/health", HTTP_GET, [] {
    JsonDocument d;
    healthToJson(d.to<JsonObject>());
    sendJson(200, d);
  });
  server.on("/api/log", HTTP_GET, [] {
    JsonDocument d;
    logToJson(d.to<JsonObject>(), server.arg("since").toInt());
    sendJson(200, d);
  });
  server.on("/update", HTTP_POST, onUpdateDone, onUpdateUpload);
  server.on("/frame", HTTP_POST, onFrameDone, onFrameRaw);
  server.serveStatic("/gifs/", LittleFS, "/gifs/", "max-age=60");
  server.onNotFound([] { sendError(404, "not found"); });
  server.begin();
  streamServer.begin();
  streamServer.setNoDelay(true);
}

void webLoop() {
  server.handleClient();
  handleStream();
  if (inMusic && millis() - lastFrameMs > 5000) {
    inMusic = false;
    gifChanged = true;
    logf("music: no frames for 5 s, back to GIF");
  }
}

bool musicActive() { return inMusic; }

bool takeGifChanged() {
  bool c = gifChanged;
  gifChanged = false;
  return c;
}
