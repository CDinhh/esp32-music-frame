#include "gif_player.h"
#include <AnimatedGIF.h>
#include <LittleFS.h>
#include "display.h"
#include "log.h"

namespace {
AnimatedGIF gif;
bool open_ = false;
uint32_t nextAt = 0;
int cw = 0, ch = 0;            // GIF canvas size
uint16_t* canvas = nullptr;    // cw*ch RGB565 in PSRAM
uint16_t* frame = nullptr;     // outW*outH scaled frame, pushed in one go
uint8_t* fileBuf = nullptr;    // whole GIF in PSRAM: decoding from RAM is far faster than from flash
uint16_t* xmap = nullptr;      // output column -> canvas column, computed once per GIF
int outW = 0, outH = 0, outX = 0, outY = 0;

struct Stats {  // playback speed, for the log
  uint32_t frames = 0, decodeUs = 0, drawUs = 0, wantMs = 0, since = 0, lastReport = 0;
  bool reported = false;
} st;

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
  lcd.waitDMA();  // the previous push may still be reading `frame`
  uint16_t* dst = frame;
  for (int oy = 0; oy < outH; oy++) {
    const uint16_t* src = canvas + (oy * ch / outH) * cw;
    for (int ox = 0; ox < outW; ox++) *dst++ = src[xmap[ox]];
  }
  lcd.pushImageDMA(outX, outY, outW, outH, (lgfx::swap565_t*)frame);
}
}  // namespace

bool fsBegin() {
  if (!LittleFS.begin(true)) return false;
  if (!LittleFS.exists("/gifs")) LittleFS.mkdir("/gifs");
  if (!LittleFS.exists("/thumbs")) LittleFS.mkdir("/thumbs");
  LittleFS.remove("/gifs/.tmp");  // leftovers of an upload or download cut by a restart
  LittleFS.remove("/gifs/.dl");
  gif.begin(GIF_PALETTE_RGB565_BE);
  return true;
}

bool gifIsOpen() { return open_; }

void gifClose() {
  display().waitDMA();  // never free a buffer the SPI DMA is still sending
  if (open_) gif.close();
  open_ = false;
  free(canvas);
  canvas = nullptr;
  free(frame);
  frame = nullptr;
  free(fileBuf);
  fileBuf = nullptr;
  free(xmap);
  xmap = nullptr;
}

bool gifOpen(const String& name) {
  gifClose();
  display().fillScreen(TFT_BLACK);
  if (name.isEmpty()) return false;
  String path = "/gifs/" + name;
  File f = LittleFS.open(path, "r");
  size_t size = f ? f.size() : 0;
  fileBuf = size ? (uint8_t*)ps_malloc(size) : nullptr;
  bool loaded = fileBuf && f.read(fileBuf, size) == size;
  if (f) f.close();
  if (!loaded || !gif.open(fileBuf, size, cbDraw)) {
    logf("gif: cannot open %s", path.c_str());
    gifClose();
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
  // internal DMA-capable RAM when it fits: the SPI DMA reads it far faster than PSRAM
  frame = (uint16_t*)heap_caps_malloc(outW * outH * 2, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
  if (!frame) frame = (uint16_t*)ps_malloc(outW * outH * 2);
  xmap = (uint16_t*)malloc(outW * 2);
  if (!canvas || !frame || !xmap) {
    gif.close();
    gifClose();
    return false;
  }
  for (int ox = 0; ox < outW; ox++) xmap[ox] = ox * cw / outW;
  open_ = true;
  nextAt = 0;
  st = Stats{};
  st.since = millis();
  return true;
}

void gifTick() {
  if (!open_ || millis() < nextAt) return;
  uint32_t t0 = micros(), startMs = millis();
  int delayMs = 0;
  int r = gif.playFrame(false, &delayMs);
  uint32_t t1 = micros();
  if (r < 0 || gif.getLastError() > GIF_EMPTY_FRAME) {  // corrupt data: stop, keep device alive
    logf("gif: decode error %d, stopped", gif.getLastError());
    gifClose();
    display().fillScreen(TFT_BLACK);
    return;
  }
  present();
  st.decodeUs += t1 - t0;
  st.drawUs += micros() - t1;
  st.wantMs += max(delayMs, 20);
  if (++st.frames == 50) {  // speed report in the web log, once after opening then every 10 min
    uint32_t now = millis();
    if (!st.reported || now - st.lastReport > 600000) {
      logf("gif: %.1f fps (GIF wants %.1f), decode %u ms, draw %u ms per frame", st.frames * 1000.0f / (now - st.since),
           st.frames * 1000.0f / st.wantMs, (unsigned)(st.decodeUs / st.frames / 1000), (unsigned)(st.drawUs / st.frames / 1000));
      st.reported = true;
      st.lastReport = now;
    }
    st.frames = st.decodeUs = st.drawUs = st.wantMs = 0;
    st.since = now;
  }
  if (r == 0) gif.reset();  // last frame -> loop
  // the frame's delay counts from when it started, so decode + draw time is not added on top
  uint32_t due = startMs + max(delayMs, 20);
  nextAt = (int32_t)(due - millis()) > 0 ? due : millis();
}

std::vector<String> gifList() {
  std::vector<String> out;
  File dir = LittleFS.open("/gifs");
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) out.push_back(f.name());
  return out;
}

size_t fsFree() { return LittleFS.totalBytes() - LittleFS.usedBytes(); }
