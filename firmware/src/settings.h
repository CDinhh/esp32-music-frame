#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <vector>

struct Settings {
  String gif;                         // active GIF file name, "" = none
  uint8_t brightness = 80;            // 0-100
  bool musicMode = true;
  int offsetMs = 0;                   // lyric offset, clamped to +-5000
  std::vector<String> sourceOrder;    // all 4 ids, user order
  std::vector<bool> sourceOn;         // parallel to sourceOrder
  bool wordSync = false;
};

Settings& settings();
void loadSettings();
void saveSettings();
void settingsToJson(JsonObject out);
// Partial update; returns false (and changes nothing) if any given field is invalid.
bool settingsFromJson(JsonObjectConst in);
// Enabled source ids in order, for the /frame reply.
void enabledSources(JsonArray out);
