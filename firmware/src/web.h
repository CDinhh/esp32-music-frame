#pragma once
#include <Arduino.h>

void webBegin();
void webLoop();
// Music frames: true while frames keep arriving (5 s timeout).
bool musicActive();
// Set when the active GIF must be (re)opened by the main loop.
bool takeGifChanged();
