#include "web.h"
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <Update.h>
#include <WebServer.h>
#include "display.h"
#include "gif_player.h"
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
  if (up.error) return sendError(up.error, up.msg);
  if (up.size == 0) return sendError(400, "no file");
  JsonDocument d;
  d["name"] = up.name;
  sendJson(201, d);
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
    if (!otaAuthed) return;
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
    Update.abort();  // keep running the current app
    gifChanged = true;
    return sendError(500, otaError.c_str());
  }
  server.send(200, "application/json", "{\"ok\":true}");
  delay(300);
  ESP.restart();
}

// ---------- music frames ----------
uint8_t* frameBuf = nullptr;
size_t frameLen = 0;
bool frameTooBig = false;

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
  if (settings().musicMode) {
    if (!inMusic) gifClose();
    inMusic = true;
    lastFrameMs = millis();
    display().drawJpg(frameBuf, frameLen, 0, 0);
  }
  JsonDocument d;
  d["offset_ms"] = settings().offsetMs;
  d["word_sync"] = settings().wordSync;
  d["music_mode"] = settings().musicMode;
  enabledSources(d["sources"].to<JsonArray>());
  sendJson(200, d);
}
}  // namespace

void webBegin() {
  frameBuf = (uint8_t*)ps_malloc(FRAME_MAX_BYTES);
  const char* headers[] = {"X-OTA-Password", "Content-Type"};
  server.collectHeaders(headers, 2);
  server.on("/", HTTP_GET, [] { server.send(200, "text/html; charset=utf-8", index_html_start); });
  server.on("/api/gifs", HTTP_GET, onGifList);
  server.on("/api/gifs", HTTP_POST, onGifUploadDone, onGifUpload);
  server.on("/api/gifs", HTTP_DELETE, onGifDelete);
  server.on("/api/settings", HTTP_GET, onSettingsGet);
  server.on("/api/settings", HTTP_POST, onSettingsPost);
  server.on("/update", HTTP_POST, onUpdateDone, onUpdateUpload);
  server.on("/frame", HTTP_POST, onFrameDone, onFrameRaw);
  server.serveStatic("/gifs/", LittleFS, "/gifs/", "max-age=60");
  server.onNotFound([] { sendError(404, "not found"); });
  server.begin();
}

void webLoop() {
  server.handleClient();
  if (inMusic && millis() - lastFrameMs > 5000) {
    inMusic = false;
    gifChanged = true;
  }
}

bool musicActive() { return inMusic; }

bool takeGifChanged() {
  bool c = gifChanged;
  gifChanged = false;
  return c;
}
