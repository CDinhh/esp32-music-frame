// Web API tests: the device runs the server in loop() and calls itself over 127.0.0.1 from a second task.
#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <unity.h>
#include "../../src/display.cpp"
#include "../../src/gif_player.cpp"
#include "../../src/settings.cpp"
#include "../../src/health.cpp"
#include "../../src/log.cpp"
#include "../../src/web.cpp"
#include "frame_jpg.h"

static const char* BASE = "http://127.0.0.1";
// 1x1 GIF89a
static const uint8_t GIF1[] = {0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 1, 0, 1, 0, 0x80, 0, 0, 0, 0, 0, 0xff, 0xff, 0xff,
                               0x21, 0xf9, 4, 0, 0, 0, 0, 0, 0x2c, 0, 0, 0, 0, 1, 0, 1, 0, 0, 2, 2, 0x44, 1, 0, 0x3b};
static const uint8_t PNG[] = {0x89, 'P', 'N', 'G', 13, 10, 26, 10, 0, 0, 0, 0};

struct Resp {
  int code;
  String body;
};

static Resp req(const char* method, const String& path, const uint8_t* body = nullptr, size_t len = 0,
                const String& type = "", const String& hdrName = "", const String& hdrVal = "") {
  HTTPClient http;
  http.begin(String(BASE) + path);
  if (type.length()) http.addHeader("Content-Type", type);
  if (hdrName.length()) http.addHeader(hdrName, hdrVal);
  int code = http.sendRequest(method, (uint8_t*)body, len);
  Resp r{code, http.getString()};
  http.end();
  return r;
}

static Resp multipart(const String& path, const String& field, const String& filename, const uint8_t* data, size_t n,
                      const String& hdrName = "", const String& hdrVal = "") {
  String head = "--XB\r\nContent-Disposition: form-data; name=\"" + field + "\"; filename=\"" + filename +
                "\"\r\nContent-Type: application/octet-stream\r\n\r\n";
  String tail = "\r\n--XB--\r\n";
  std::vector<uint8_t> buf(head.begin(), head.end());
  buf.insert(buf.end(), data, data + n);
  buf.insert(buf.end(), tail.begin(), tail.end());
  return req("POST", path, buf.data(), buf.size(), "multipart/form-data; boundary=XB", hdrName, hdrVal);
}

void setUp() {}
void tearDown() {}

void test_settings_api() {
  Resp r = req("GET", "/api/settings");
  TEST_ASSERT_EQUAL(200, r.code);
  TEST_ASSERT_TRUE(r.body.indexOf("\"sources\"") >= 0);
  const char* bad = "{\"brightness\":101}";
  TEST_ASSERT_EQUAL(400, req("POST", "/api/settings", (const uint8_t*)bad, strlen(bad), "application/json").code);
  const char* ok = "{\"brightness\":40}";
  r = req("POST", "/api/settings", (const uint8_t*)ok, strlen(ok), "application/json");
  TEST_ASSERT_EQUAL(200, r.code);
  TEST_ASSERT_TRUE(r.body.indexOf("\"brightness\":40") >= 0);
}

void test_upload_valid_gif_sanitizes_name() {
  Resp r = multipart("/api/gifs", "file", "../my cat.GIF", GIF1, sizeof(GIF1));
  TEST_ASSERT_EQUAL_MESSAGE(201, r.code, r.body.c_str());
  TEST_ASSERT_TRUE(r.body.indexOf("my_cat.gif") >= 0);
  TEST_ASSERT_TRUE(req("GET", "/api/gifs").body.indexOf("my_cat.gif") >= 0);
  TEST_ASSERT_EQUAL(200, req("GET", "/gifs/my_cat.gif").code);
}

void test_upload_rejects_png_and_leaves_nothing() {
  String before = req("GET", "/api/gifs").body;
  Resp r = multipart("/api/gifs", "file", "x.gif", PNG, sizeof(PNG));
  TEST_ASSERT_EQUAL(400, r.code);
  TEST_ASSERT_TRUE(r.body.indexOf("not a GIF") >= 0);
  TEST_ASSERT_FALSE(LittleFS.exists("/gifs/x.gif"));
  TEST_ASSERT_FALSE(LittleFS.exists("/gifs/.tmp"));
  TEST_ASSERT_EQUAL_STRING(before.c_str(), req("GET", "/api/gifs").body.c_str());
}

void test_raw_body_to_upload_route_is_rejected() {
  TEST_ASSERT_EQUAL(400, req("POST", "/api/gifs", GIF1, sizeof(GIF1), "image/gif").code);
}

void test_select_and_delete_active_falls_back() {
  multipart("/api/gifs", "file", "second.gif", GIF1, sizeof(GIF1));
  const char* sel = "{\"gif\":\"my_cat.gif\"}";
  TEST_ASSERT_EQUAL(200, req("POST", "/api/settings", (const uint8_t*)sel, strlen(sel), "application/json").code);
  const char* missing = "{\"gif\":\"nope.gif\"}";
  TEST_ASSERT_EQUAL(404, req("POST", "/api/settings", (const uint8_t*)missing, strlen(missing), "application/json").code);
  Resp r = req("DELETE", "/api/gifs?name=my_cat.gif");
  TEST_ASSERT_EQUAL(200, r.code);
  TEST_ASSERT_TRUE(r.body.indexOf("my_cat.gif") < 0);
  TEST_ASSERT_TRUE(settings().gif.length() > 0);  // switched to a remaining GIF
  TEST_ASSERT_EQUAL(400, req("DELETE", "/api/gifs?name=../x.gif").code);
  TEST_ASSERT_EQUAL(404, req("DELETE", "/api/gifs?name=nope.gif").code);
}

void test_frame_endpoint() {
  TEST_ASSERT_EQUAL(400, req("POST", "/frame", PNG, sizeof(PNG), "image/jpeg").code);
  static uint8_t big[FRAME_MAX_BYTES + 10];
  big[0] = 0xFF;
  big[1] = 0xD8;
  TEST_ASSERT_EQUAL(413, req("POST", "/frame", big, sizeof(big), "image/jpeg").code);
  Resp r = req("POST", "/frame", big, 1000, "image/jpeg");  // valid header, junk body: drawn as garbage, never a crash
  TEST_ASSERT_EQUAL(200, r.code);
  TEST_ASSERT_TRUE(r.body.indexOf("\"offset_ms\"") >= 0);
  TEST_ASSERT_TRUE(r.body.indexOf("\"sources\":[") >= 0);
  TEST_ASSERT_TRUE(musicActive());
}

void test_health_api() {
  Resp r = req("GET", "/api/health");
  TEST_ASSERT_EQUAL(200, r.code);
  JsonDocument d;
  TEST_ASSERT_FALSE(deserializeJson(d, r.body));
  TEST_ASSERT_TRUE(d["temp_c"].is<float>());
  TEST_ASSERT_TRUE(d["heap_free"].as<uint32_t>() > 0);
  TEST_ASSERT_TRUE(d["uptime_s"].is<uint32_t>());
  TEST_ASSERT_TRUE(d["reset_reason"].is<const char*>());
  TEST_ASSERT_TRUE(d["warnings"].is<JsonArray>());
  TEST_ASSERT_EQUAL(d["ok"].as<bool>(), d["warnings"].size() == 0);
}

void test_log_api_returns_new_lines_only() {
  logf("test: marker %d", 42);
  Resp r = req("GET", "/api/log?since=0");
  TEST_ASSERT_EQUAL(200, r.code);
  TEST_ASSERT_TRUE(r.body.indexOf("test: marker 42") >= 0);
  JsonDocument d;
  deserializeJson(d, r.body);
  uint32_t last = d["last"];
  TEST_ASSERT_TRUE(last > 0);
  Resp again = req("GET", String("/api/log?since=") + last);
  TEST_ASSERT_TRUE(again.body.indexOf("test: marker 42") < 0);  // only lines after `last`
}

void test_real_frame_from_pc_app() {  // left on screen for the screenshot check
  Resp r = req("POST", "/frame", FRAME_JPG, sizeof(FRAME_JPG), "image/jpeg");
  TEST_ASSERT_EQUAL(200, r.code);
}

void test_ota_needs_password() {
  uint8_t junk[64] = {0xE9};
  TEST_ASSERT_EQUAL(401, multipart("/update", "firmware", "fw.bin", junk, sizeof(junk), "X-OTA-Password", "wrong").code);
  TEST_ASSERT_EQUAL(401, multipart("/update", "firmware", "fw.bin", junk, sizeof(junk)).code);
  // right password, invalid image: rejected, device keeps running
  TEST_ASSERT_EQUAL(500, multipart("/update", "firmware", "fw.bin", junk, sizeof(junk), "X-OTA-Password", "sim").code);
}

static void runTests(void*) {
  delay(1000);
  UNITY_BEGIN();
  RUN_TEST(test_settings_api);
  RUN_TEST(test_upload_valid_gif_sanitizes_name);
  RUN_TEST(test_upload_rejects_png_and_leaves_nothing);
  RUN_TEST(test_raw_body_to_upload_route_is_rejected);
  RUN_TEST(test_select_and_delete_active_falls_back);
  RUN_TEST(test_frame_endpoint);
  RUN_TEST(test_ota_needs_password);
  RUN_TEST(test_health_api);
  RUN_TEST(test_log_api_returns_new_lines_only);
  RUN_TEST(test_real_frame_from_pc_app);
  UNITY_END();
  vTaskDelete(nullptr);
}

void setup() {
  delay(300);
  logBegin();
  healthBegin();
  displayBegin();
  loadSettings();
  TEST_ASSERT_TRUE(fsBegin());
  WiFi.mode(WIFI_STA);
  WiFi.begin("Wokwi-GUEST", "");
  for (int i = 0; i < 100 && WiFi.status() != WL_CONNECTED; i++) delay(100);
  webBegin();
  xTaskCreatePinnedToCore(runTests, "tests", 16384, nullptr, 1, nullptr, 0);
}

void loop() { webLoop(); }
