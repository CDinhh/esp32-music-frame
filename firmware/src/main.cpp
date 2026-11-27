#include <Arduino.h>
#include <ESPmDNS.h>
#include <LittleFS.h>
#include <WiFi.h>
#include "display.h"
#include "log.h"
#include "gif_player.h"
#include "health.h"
#include "settings.h"
#include "web.h"

#ifdef SIM_WOKWI
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASS ""
#else
#ifndef WIFI_SSID
#error "WiFi settings come from .env at the repo root (see .env.example)"
#endif
#endif

namespace {
String activeGif() {
#ifdef SIM_WOKWI
  if (settings().gif.isEmpty() && LittleFS.exists("/sim_gif.txt")) {  // sim-only test hook
    String g = LittleFS.open("/sim_gif.txt").readString();
    g.trim();
    return g;
  }
#endif
  if (!settings().gif.isEmpty()) return settings().gif;
  for (auto& n : gifList())
    if (!n.startsWith(".")) return n;
  return "";
}

void showStatus(const String& a, const String& b) {
  auto& d = display();
  d.fillScreen(TFT_BLACK);
  d.setTextDatum(middle_center);
  d.setTextColor(TFT_WHITE, TFT_BLACK);
  d.setFont(&fonts::FreeSansBold12pt7b);
  d.drawString(a, SCREEN / 2, SCREEN / 2 - 16);
  d.setFont(&fonts::FreeSans9pt7b);
  d.drawString(b, SCREEN / 2, SCREEN / 2 + 16);
}
}  // namespace

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // never block when no console is reading
#endif
  logBegin();
  healthBegin();
  displayBegin();
  loadSettings();
  setBacklight(settings().brightness);
  if (!fsBegin()) logf("fs: mount failed");

  showStatus("Music Frame", "connecting WiFi...");
  WiFi.onEvent([](WiFiEvent_t e, WiFiEventInfo_t info) {
    if (e == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
      logf("wifi: disconnected (reason %d)", info.wifi_sta_disconnected.reason);
    else if (e == ARDUINO_EVENT_WIFI_STA_GOT_IP)
      logf("wifi: got IP %s", IPAddress(info.got_ip.ip_info.ip.addr).toString().c_str());
  });
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("musicframe");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  WiFi.setSleep(false);  // modem sleep stalls the frame stream for 100-300 ms at a time
  for (int i = 0; i < 100 && WiFi.status() != WL_CONNECTED; i++) delay(100);
  if (WiFi.status() == WL_CONNECTED) {
    MDNS.begin("musicframe");
    MDNS.addService("http", "tcp", 80);
    logf("wifi: connected, %s, %d dBm", WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
    showStatus("musicframe.local", WiFi.localIP().toString());
    delay(3000);
  } else {
    logf("wifi: not connected, retrying in background");
    WiFi.setAutoReconnect(true);
  }
  webBegin();
  String g = activeGif();
  bool playing = gifOpen(g);
  logf("gif: %s", playing ? g.c_str() : "none to play");
}

void loop() {
  webLoop();
  if (takeGifChanged() && !musicActive()) gifOpen(activeGif());
  if (!musicActive()) gifTick();
}
