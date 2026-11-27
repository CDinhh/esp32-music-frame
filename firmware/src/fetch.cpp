#include "fetch.h"
#include <HTTPClient.h>
#include <LittleFS.h>
#include <NetworkClientSecure.h>
#include <WiFi.h>
#include "gif_player.h"
#include "log.h"
#include "validate.h"

namespace {
enum State { IDLE, DOWNLOADING, DONE, FAILED };
volatile State state = IDLE;
volatile size_t received = 0, total = 0;
String resultName, error;  // written by the task, read only when state is DONE/FAILED
std::vector<String> pendingUrls;
String pendingName;
const char* TMP = "/gifs/.dl";

String uniqueName(const String& wanted) {
  String base = wanted.substring(0, wanted.length() - 4), name = wanted;
  for (int i = 2; LittleFS.exists("/gifs/" + name) && i < 100; i++) name = base + "-" + i + ".gif";
  return name;
}

// One attempt; on failure sets `why` and returns false.
bool download(const String& url, String& why) {
  std::unique_ptr<NetworkClient> client;
  if (url.startsWith("https://")) {
    auto* tls = new NetworkClientSecure();
    tls->useBuiltinCACertBundle();  // verify the server against the bundled root CAs
    client.reset(tls);
  } else {
    client.reset(new NetworkClient());
  }
  HTTPClient http;
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.setTimeout(10000);
  http.setUserAgent("esp32-music-frame/0.1");
  if (!http.begin(*client, url)) return why = "bad link", false;
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    why = code > 0 ? "server answered " + String(code) : "cannot connect (" + http.errorToString(code) + ")";
    http.end();
    return false;
  }
  int len = http.getSize();  // -1 when the server does not say
  if (len > (int)GIF_MAX_BYTES) return why = "GIF larger than 2 MB", http.end(), false;
  total = len > 0 ? len : 0;
  received = 0;
  // Whole file into PSRAM first: writing flash every few KB stalls both cores and slows the download to a crawl.
  size_t cap = len > 0 ? len : GIF_MAX_BYTES;
  std::unique_ptr<uint8_t, decltype(&free)> buf((uint8_t*)ps_malloc(cap), &free);
  if (!buf) return why = "out of memory", http.end(), false;
  NetworkClient* s = http.getStreamPtr();
  bool ok = true;
  uint32_t lastData = millis();
  while (http.connected() || s->available()) {
    if (len > 0 && received >= (size_t)len) break;
    size_t avail = s->available();
    if (!avail) {
      if (millis() - lastData > 10000) { why = "download stalled"; ok = false; break; }
      delay(2);
      continue;
    }
    if (received + avail > cap) { why = "GIF larger than 2 MB"; ok = false; break; }
    int n = s->readBytes(buf.get() + received, avail);
    if (n <= 0) continue;
    if (received == 0 && n >= 10 && (!isGifHeader(buf.get(), n) || !gifDimsOk(buf.get(), n))) {
      why = isGifHeader(buf.get(), n) ? "GIF larger than 480 px" : "not a GIF";
      ok = false;
      break;
    }
    received += n;
    lastData = millis();
  }
  http.end();
  if (ok && (received < 10 || (len > 0 && received < (size_t)len))) { why = "download cut short"; ok = false; }
  if (ok && (!isGifHeader(buf.get(), received) || !gifDimsOk(buf.get(), received))) { why = "not a GIF"; ok = false; }
  if (ok && fsFree() < received + 16384) { why = "storage full"; ok = false; }
  if (ok) {  // one flash write at the end
    File f = LittleFS.open(TMP, "w");
    ok = f && f.write(buf.get(), received) == received;
    if (f) f.close();
    if (!ok) { why = "cannot write"; LittleFS.remove(TMP); }
  }
  return ok;
}

void task(void*) {
  String why = "no link";
  bool ok = false;
  for (auto& url : pendingUrls) {
    logf("fetch: trying %s", url.c_str());
    if ((ok = download(url, why))) break;
    logf("fetch: %s", why.c_str());
  }
  if (ok) {
    String name = uniqueName(pendingName);
    if (LittleFS.rename(TMP, "/gifs/" + name)) {
      resultName = name;
      logf("fetch: saved %s (%u KB)", name.c_str(), (unsigned)(received / 1024));
      state = DONE;
    } else {
      LittleFS.remove(TMP);
      error = "cannot save";
      state = FAILED;
    }
  } else {
    error = why;
    state = FAILED;
  }
  vTaskDelete(nullptr);
}
}  // namespace

bool fetchStart(const std::vector<String>& urls, const String& name) {
  if (state == DOWNLOADING) return false;
  pendingUrls = urls;
  pendingName = name;
  resultName = error = "";
  received = total = 0;
  state = DOWNLOADING;
  // TLS needs a deep stack; pinned to core 0 so the display loop on core 1 keeps its pace
  if (xTaskCreatePinnedToCore(task, "fetch", 12288, nullptr, 1, nullptr, 0) != pdPASS) {
    state = FAILED;
    error = "out of memory";
  }
  return true;
}

void fetchToJson(JsonObject out) {
  static const char* names[] = {"idle", "downloading", "done", "error"};
  State s = state;
  out["state"] = names[s];
  out["received"] = (uint32_t)received;
  out["total"] = (uint32_t)total;
  if (s == DONE) out["name"] = resultName;
  if (s == FAILED) out["error"] = error;
}
