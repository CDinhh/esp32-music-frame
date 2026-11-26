#pragma once
#define LGFX_USE_V1
#include <LovyanGFX.hpp>

constexpr int SCREEN = 240;

lgfx::LGFX_Device& display();
void displayBegin();
void setBacklight(uint8_t pct);  // 0-100
