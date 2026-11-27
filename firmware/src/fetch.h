#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <vector>

// Downloads a GIF from the web in a background task, so playback and music frames keep running.
// `urls` are tried in order (e.g. several Giphy renditions, biggest first) until one passes the
// same checks as an upload: a GIF, <= 2 MB, <= 480 px. Returns false if a download is running.
bool fetchStart(const std::vector<String>& urls, const String& name);
// {"state":"idle|downloading|done|error","received":n,"total":n,"name":"x.gif","error":"..."}
void fetchToJson(JsonObject out);
