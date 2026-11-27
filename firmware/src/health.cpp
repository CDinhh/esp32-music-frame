#include "health.h"
#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_system.h>
#include "gif_player.h"
#include "log.h"

namespace {
esp_reset_reason_t bootReason = ESP_RST_UNKNOWN;
uint32_t brownouts = 0;

const char* reasonId(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON: return "power_on";
    case ESP_RST_SW: return "restart";  // OTA update or restart from the web
    case ESP_RST_PANIC: return "crash";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout";  // supply voltage dipped
    case ESP_RST_DEEPSLEEP: return "deep_sleep";
    case ESP_RST_EXT: return "reset_pin";
    case ESP_RST_USB: return "usb";
    default: return "unknown";
  }
}
}  // namespace

void healthBegin() {
  bootReason = esp_reset_reason();
  Preferences p;
  p.begin("health", false);
  brownouts = p.getUInt("brownouts", 0);
  if (bootReason == ESP_RST_BROWNOUT) p.putUInt("brownouts", ++brownouts);
  p.end();
  logf("boot: reason %s, power dips so far %u", reasonId(bootReason), (unsigned)brownouts);
}

void healthToJson(JsonObject out) {
  float temp = temperatureRead();
  int rssi = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0;
  out["uptime_s"] = millis() / 1000;
  out["temp_c"] = round(temp * 10) / 10.0;
  out["heap_free"] = ESP.getFreeHeap();
  out["heap_min"] = ESP.getMinFreeHeap();  // lowest since boot: a slow fall means a leak
  out["psram_free"] = ESP.getFreePsram();
  out["fs_free"] = fsFree();
  out["rssi"] = rssi;
  out["reset_reason"] = reasonId(bootReason);
  out["brownouts"] = brownouts;
  out["cpu_mhz"] = getCpuFrequencyMhz();

  JsonArray warn = out["warnings"].to<JsonArray>();
  if (temp > 70) warn.add("hot");
  if (bootReason == ESP_RST_BROWNOUT) warn.add("brownout");
  if (bootReason == ESP_RST_PANIC || bootReason == ESP_RST_TASK_WDT || bootReason == ESP_RST_INT_WDT || bootReason == ESP_RST_WDT)
    warn.add("crashed");
  if (rssi && rssi < -80) warn.add("weak_wifi");
  if (ESP.getMinFreeHeap() < 30000) warn.add("low_memory");
  out["ok"] = warn.size() == 0;
}
