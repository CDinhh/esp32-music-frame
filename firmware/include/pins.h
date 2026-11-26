#pragma once
// TFT 1.54" ST7789 240x240. GPIO 4/5/6 reserved for future I2S audio; 35-37 used by octal PSRAM.
constexpr int PIN_TFT_SCLK = 12;  // board label SCL
constexpr int PIN_TFT_MOSI = 11;  // board label SDA
constexpr int PIN_TFT_RST = 8;
constexpr int PIN_TFT_DC = 9;
constexpr int PIN_TFT_CS = 10;
constexpr int PIN_TFT_BL = 7;
