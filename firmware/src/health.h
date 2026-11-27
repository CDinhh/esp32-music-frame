#pragma once
#include <ArduinoJson.h>

// Call once at boot: records why the chip restarted and counts power dips (brownouts) in NVS.
void healthBegin();
// Live health for /api/health: temperature, memory, WiFi, uptime, last reset, power dips, and warnings.
void healthToJson(JsonObject out);
