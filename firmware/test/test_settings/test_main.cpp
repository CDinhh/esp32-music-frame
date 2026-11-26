#include <Arduino.h>
#include <unity.h>
#include "../../src/settings.cpp"  // tests do not build src/ (it has setup/loop)

void setUp() {}
void tearDown() {}

static bool apply(const char* json) {
  JsonDocument d;
  deserializeJson(d, json);
  return settingsFromJson(d.as<JsonObjectConst>());
}

void test_defaults_after_empty_nvs() {
  loadSettings();
  TEST_ASSERT_EQUAL(4, (int)settings().sourceOrder.size());
}

void test_partial_update_and_clamp() {
  TEST_ASSERT_TRUE(apply(R"({"brightness":30,"offset_ms":99999})"));
  TEST_ASSERT_EQUAL(30, settings().brightness);
  TEST_ASSERT_EQUAL(5000, settings().offsetMs);
}

void test_rejects_bad_input_atomically() {
  apply(R"({"brightness":50})");
  TEST_ASSERT_FALSE(apply(R"({"brightness":20,"sources":[{"id":"spotify","on":true}]})"));
  TEST_ASSERT_EQUAL(50, settings().brightness);  // nothing changed
  TEST_ASSERT_FALSE(apply(R"({"brightness":101})"));
  TEST_ASSERT_FALSE(apply(R"({"gif":"../x.gif"})"));
}

void test_sources_roundtrip_through_nvs() {
  TEST_ASSERT_TRUE(apply(R"({"sources":[{"id":"kugou","on":true},{"id":"lrclib","on":false},{"id":"netease","on":true},{"id":"musixmatch","on":true}]})"));
  saveSettings();
  settings().sourceOrder.clear();
  loadSettings();
  JsonDocument d;
  enabledSources(d.to<JsonArray>());
  String out;
  serializeJson(d, out);
  TEST_ASSERT_EQUAL_STRING(R"(["kugou","netease","musixmatch"])", out.c_str());
}

void setup() {
  delay(500);
  UNITY_BEGIN();
  RUN_TEST(test_defaults_after_empty_nvs);
  RUN_TEST(test_partial_update_and_clamp);
  RUN_TEST(test_rejects_bad_input_atomically);
  RUN_TEST(test_sources_roundtrip_through_nvs);
  UNITY_END();
}

void loop() {}
