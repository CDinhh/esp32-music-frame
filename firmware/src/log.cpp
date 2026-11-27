#include "log.h"
#include <Arduino.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <deque>

namespace {
constexpr size_t MAX_LINES = 200;
constexpr size_t MAX_LEN = 160;

struct Entry {
  uint32_t seq, ms;
  String text;
};
std::deque<Entry> lines;
uint32_t seq = 0;
SemaphoreHandle_t lock = nullptr;  // ESP-IDF logs from WiFi/system tasks, the web reads from loop()
vprintf_like_t idfPrint = nullptr;
String partial;  // ESP-IDF may print one line in several calls

void push(const char* text) {
  if (!lock) return;
  if (xSemaphoreTake(lock, pdMS_TO_TICKS(20)) != pdTRUE) return;  // never stall a caller over a log line
  String t(text);
  t.trim();
  if (t.length()) {
    if (t.length() > MAX_LEN) t = t.substring(0, MAX_LEN) + "…";
    lines.push_back({++seq, (uint32_t)millis(), t});
    if (lines.size() > MAX_LINES) lines.pop_front();
  }
  xSemaphoreGive(lock);
}

int idfHook(const char* fmt, va_list args) {
  char buf[MAX_LEN + 1];
  va_list copy;
  va_copy(copy, args);
  vsnprintf(buf, sizeof(buf), fmt, copy);
  va_end(copy);
  partial += buf;
  int nl;
  while ((nl = partial.indexOf('\n')) >= 0) {
    push(partial.substring(0, nl).c_str());
    partial.remove(0, nl + 1);
  }
  if (partial.length() > MAX_LEN) {
    push(partial.c_str());
    partial = "";
  }
  return idfPrint ? idfPrint(fmt, args) : 0;
}
}  // namespace

void logBegin() {
  if (lock) return;
  lock = xSemaphoreCreateMutex();
  idfPrint = esp_log_set_vprintf(idfHook);
}

void logf(const char* fmt, ...) {
  char buf[MAX_LEN + 1];
  va_list args;
  va_start(args, fmt);
  vsnprintf(buf, sizeof(buf), fmt, args);
  va_end(args);
  Serial.println(buf);
  push(buf);
}

void logToJson(JsonObject out, uint32_t since) {
  JsonArray arr = out["lines"].to<JsonArray>();
  out["last"] = seq;
  if (!lock || xSemaphoreTake(lock, pdMS_TO_TICKS(100)) != pdTRUE) return;
  for (auto& e : lines) {
    if (e.seq <= since) continue;
    JsonObject o = arr.add<JsonObject>();
    o["seq"] = e.seq;
    o["ms"] = e.ms;
    o["text"] = e.text;
  }
  xSemaphoreGive(lock);
}
