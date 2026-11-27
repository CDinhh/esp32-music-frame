#pragma once
#include <ArduinoJson.h>

// Keeps the last lines of log in RAM for the web page, and still prints them on the serial port.
// logBegin() also catches ESP-IDF's own messages (WiFi, filesystem errors...).
void logBegin();
void logf(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
// Lines with seq > since: {"lines":[{"seq":n,"ms":t,"text":"..."}],"last":n}
void logToJson(JsonObject out, uint32_t since);
