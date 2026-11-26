#include <Arduino.h>
#include <LittleFS.h>
#include "display.h"
#include "gif_player.h"
#include "settings.h"

void setup() {
  Serial.begin(115200);
  displayBegin();
  loadSettings();
  setBacklight(settings().brightness);
  if (!fsBegin()) Serial.println("fs: mount failed");
  auto list = gifList();
  Serial.printf("gifs: %u, free %u\n", (unsigned)list.size(), (unsigned)fsFree());
  String active = settings().gif;
  if (active.isEmpty() && !list.empty()) active = list[0];
#ifdef SIM_WOKWI
  if (LittleFS.exists("/sim_gif.txt")) active = LittleFS.open("/sim_gif.txt").readString();  // sim-only test hook
  active.trim();
#endif
  Serial.printf("gif: %s -> %s\n", active.c_str(), gifOpen(active) ? "ok" : "fail");
}

void loop() { gifTick(); }
