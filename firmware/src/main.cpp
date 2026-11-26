#include <Arduino.h>
#include "display.h"

void setup() {
  Serial.begin(115200);
  displayBegin();
  setBacklight(80);
  auto& d = display();
  const uint16_t colors[] = {TFT_RED, TFT_GREEN, TFT_BLUE, TFT_WHITE};
  for (int i = 0; i < 4; i++) d.fillRect(i * 60, 0, 60, 180, colors[i]);
  d.setTextColor(TFT_WHITE, TFT_BLACK);
  d.setTextDatum(middle_center);
  d.setFont(&fonts::FreeSansBold12pt7b);
  d.drawString("240x240", 120, 210);
  Serial.printf("PSRAM: %u bytes\n", ESP.getPsramSize());
}

void loop() { delay(1000); }
