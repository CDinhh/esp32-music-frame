#include "settings.h"
#include <Preferences.h>
#include "validate.h"

namespace {
Settings s;
Preferences prefs;

void defaultSources(Settings& t) {
  t.sourceOrder.clear();
  t.sourceOn.clear();
  for (auto id : SOURCE_IDS) {
    t.sourceOrder.push_back(id);
    t.sourceOn.push_back(true);
  }
}

// "lrclib:1,netease:0,..." -> order + flags; falls back to defaults if malformed or incomplete.
void parseSources(const String& str, Settings& t) {
  Settings tmp;
  std::vector<std::string> ids;
  int start = 0;
  while (start < (int)str.length()) {
    int comma = str.indexOf(',', start);
    if (comma < 0) comma = str.length();
    String item = str.substring(start, comma);
    int colon = item.indexOf(':');
    if (colon > 0) {
      tmp.sourceOrder.push_back(item.substring(0, colon));
      tmp.sourceOn.push_back(item.substring(colon + 1) == "1");
      ids.push_back(item.substring(0, colon).c_str());
    }
    start = comma + 1;
  }
  if (ids.size() == 4 && validSources(ids)) {
    t.sourceOrder = tmp.sourceOrder;
    t.sourceOn = tmp.sourceOn;
  } else {
    defaultSources(t);
  }
}

String joinSources(const Settings& t) {
  String out;
  for (size_t i = 0; i < t.sourceOrder.size(); i++) {
    if (i) out += ',';
    out += t.sourceOrder[i] + ':' + (t.sourceOn[i] ? "1" : "0");
  }
  return out;
}
}  // namespace

Settings& settings() { return s; }

void loadSettings() {
  prefs.begin("mf", true);
  s.gif = prefs.getString("gif", "");
  s.brightness = prefs.getUChar("bright", 80);
  s.musicMode = prefs.getBool("music", true);
  s.offsetMs = prefs.getInt("offset", 0);
  s.wordSync = prefs.getBool("word", false);
  parseSources(prefs.getString("src", ""), s);
  prefs.end();
}

void saveSettings() {
  prefs.begin("mf", false);
  prefs.putString("gif", s.gif);
  prefs.putUChar("bright", s.brightness);
  prefs.putBool("music", s.musicMode);
  prefs.putInt("offset", s.offsetMs);
  prefs.putBool("word", s.wordSync);
  prefs.putString("src", joinSources(s));
  prefs.end();
}

void settingsToJson(JsonObject out) {
  out["gif"] = s.gif;
  out["brightness"] = s.brightness;
  out["music_mode"] = s.musicMode;
  out["offset_ms"] = s.offsetMs;
  out["word_sync"] = s.wordSync;
  JsonArray src = out["sources"].to<JsonArray>();
  for (size_t i = 0; i < s.sourceOrder.size(); i++) {
    JsonObject o = src.add<JsonObject>();
    o["id"] = s.sourceOrder[i];
    o["on"] = (bool)s.sourceOn[i];
  }
}

bool settingsFromJson(JsonObjectConst in) {
  Settings t = s;
  if (in["brightness"].is<int>()) {
    int b = in["brightness"];
    if (b < 0 || b > 100) return false;
    t.brightness = b;
  }
  if (in["music_mode"].is<bool>()) t.musicMode = in["music_mode"];
  if (in["word_sync"].is<bool>()) t.wordSync = in["word_sync"];
  if (in["offset_ms"].is<int>()) t.offsetMs = constrain((int)in["offset_ms"], -5000, 5000);
  if (in["gif"].is<const char*>()) {
    std::string g = in["gif"].as<const char*>();
    if (!g.empty() && sanitizeName(g) != g) return false;
    t.gif = g.c_str();
  }
  if (in["sources"].is<JsonArrayConst>()) {
    JsonArrayConst arr = in["sources"];
    std::vector<std::string> ids;
    t.sourceOrder.clear();
    t.sourceOn.clear();
    for (JsonObjectConst o : arr) {
      const char* id = o["id"] | "";
      ids.push_back(id);
      t.sourceOrder.push_back(id);
      t.sourceOn.push_back(o["on"] | true);
    }
    if (ids.size() != 4 || !validSources(ids)) return false;
  }
  s = t;
  return true;
}

void enabledSources(JsonArray out) {
  for (size_t i = 0; i < s.sourceOrder.size(); i++)
    if (s.sourceOn[i]) out.add(s.sourceOrder[i]);
}
