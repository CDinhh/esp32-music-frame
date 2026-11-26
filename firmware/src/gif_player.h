#pragma once
#include <Arduino.h>
#include <vector>

bool fsBegin();
bool gifOpen(const String& name);  // name inside /gifs; false (black screen) if missing or broken
void gifTick();                    // non-blocking: draws the next frame when its delay is up
void gifClose();
bool gifIsOpen();
std::vector<String> gifList();
size_t fsFree();
