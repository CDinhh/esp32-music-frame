#include "gif_player.h"
#include <AnimatedGIF.h>
#include <LittleFS.h>
#include "display.h"

namespace {
AnimatedGIF gif;
File file;
bool open_ = false;
uint32_t nextAt = 0;
int cw = 0, ch = 0;            // GIF canvas size
uint16_t* canvas = nullptr;    // cw*ch RGB565 in PSRAM
uint16_t* line = nullptr;      // one output line
int outW = 0, outH = 0, outX = 0, outY = 0;

void* cbOpen(const char* path, int32_t* size) {
  file = LittleFS.open(path, "r");
  if (!file) return nullptr;
  *size = file.size();
  return &file;
}
void cbClose(void*) { file.close(); }
int32_t cbRead(GIFFILE* f, uint8_t* buf, int32_t len) {
  int32_t n = min(len, f->iSize - f->iPos);
  if (n <= 0) return 0;
  n = file.read(buf, n);
  f->iPos = file.position();
  return n;
}
int32_t cbSeek(GIFFILE* f, int32_t pos) {
  file.seek(pos);
  f->iPos = file.position();
  return f->iPos;
}

// Composite each decoded line into the full canvas; transparent pixels keep what was there.
void cbDraw(GIFDRAW* d) {
  int y = d->iY + d->y;
  if (y < 0 || y >= ch) return;
  uint16_t* row = canvas + y * cw;
  for (int x = 0; x < d->iWidth; x++) {
    int cx = d->iX + x;
    if (cx < 0 || cx >= cw) continue;
    uint8_t p = d->pPixels[x];
    if (d->ucHasTransparency && p == d->ucTransparent) continue;
    row[cx] = d->pPalette[p];
  }
}

// Nearest-neighbour scale the canvas into the centered output box.
void present() {
  auto& lcd = display();
  lcd.startWrite();
  for (int oy = 0; oy < outH; oy++) {
    const uint16_t* src = canvas + (oy * ch / outH) * cw;
    for (int ox = 0; ox < outW; ox++) line[ox] = src[ox * cw / outW];
    lcd.pushImage(outX, outY + oy, outW, 1, (lgfx::swap565_t*)line);
  }
  lcd.endWrite();
}
}  // namespace

bool fsBegin() {
  if (!LittleFS.begin(true)) return false;
  if (!LittleFS.exists("/gifs")) LittleFS.mkdir("/gifs");
  gif.begin(GIF_PALETTE_RGB565_BE);
  return true;
}

bool gifIsOpen() { return open_; }

void gifClose() {
  if (open_) gif.close();
  open_ = false;
  free(canvas);
  canvas = nullptr;
  free(line);
  line = nullptr;
}

bool gifOpen(const String& name) {
  gifClose();
  display().fillScreen(TFT_BLACK);
  if (name.isEmpty()) return false;
  String path = "/gifs/" + name;
  if (!LittleFS.exists(path) || !gif.open(path.c_str(), cbOpen, cbClose, cbRead, cbSeek, cbDraw)) {
    Serial.printf("gif: cannot open %s\n", path.c_str());
    return false;
  }
  cw = gif.getCanvasWidth();
  ch = gif.getCanvasHeight();
  if (cw <= 0 || ch <= 0 || cw > 480 || ch > 480) {
    gif.close();
    return false;
  }
  if (cw > SCREEN || ch > SCREEN) {  // shrink to fit, keep aspect
    int m = max(cw, ch);
    outW = cw * SCREEN / m;
    outH = ch * SCREEN / m;
  } else {
    outW = cw;
    outH = ch;
  }
  outX = (SCREEN - outW) / 2;
  outY = (SCREEN - outH) / 2;
  canvas = (uint16_t*)ps_calloc(cw * ch, 2);
  line = (uint16_t*)malloc(outW * 2);
  if (!canvas || !line) {
    gif.close();
    gifClose();
    return false;
  }
  open_ = true;
  nextAt = 0;
  return true;
}

void gifTick() {
  if (!open_ || millis() < nextAt) return;
  int delayMs = 0;
  int r = gif.playFrame(false, &delayMs);
  if (r < 0 || gif.getLastError() > GIF_EMPTY_FRAME) {  // corrupt data: stop, keep device alive
    Serial.printf("gif: decode error %d\n", gif.getLastError());
    gifClose();
    display().fillScreen(TFT_BLACK);
    return;
  }
  present();
  if (r == 0) gif.reset();  // last frame -> loop
  nextAt = millis() + max(delayMs, 20);
}

std::vector<String> gifList() {
  std::vector<String> out;
  File dir = LittleFS.open("/gifs");
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) out.push_back(f.name());
  return out;
}

size_t fsFree() { return LittleFS.totalBytes() - LittleFS.usedBytes(); }
