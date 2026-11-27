#include "display.h"
#include "pins.h"

namespace {
class LGFX : public lgfx::LGFX_Device {
  lgfx::Panel_ST7789 panel;  // also drives Wokwi's ILI9341 part (same command set)
  lgfx::Bus_SPI bus;
  lgfx::Light_PWM light;

 public:
  LGFX() {
    auto b = bus.config();
    b.spi_host = SPI2_HOST;
    b.spi_mode = 0;
    b.freq_write = 80000000;  // faster pushes shrink the tear line (the module has no TE pin to sync on)
    b.dma_channel = SPI_DMA_CH_AUTO;
    b.pin_sclk = PIN_TFT_SCLK;
    b.pin_mosi = PIN_TFT_MOSI;
    b.pin_miso = -1;
    b.pin_dc = PIN_TFT_DC;
    bus.config(b);
    panel.setBus(&bus);

    auto p = panel.config();
    p.pin_cs = PIN_TFT_CS;
    p.pin_rst = PIN_TFT_RST;
    p.panel_width = SCREEN;
    p.panel_height = SCREEN;
#ifdef SIM_WOKWI
    p.memory_height = 320;
    p.invert = false;
#else
    p.invert = true;  // most 1.54" ST7789 modules; flip if colors look negative
#endif
    p.readable = false;
    panel.config(p);

    auto l = light.config();
    l.pin_bl = PIN_TFT_BL;
    l.freq = 12000;
    l.pwm_channel = 7;
    light.config(l);
    panel.setLight(&light);

    setPanel(&panel);
  }
};

LGFX lcd;
}  // namespace

lgfx::LGFX_Device& display() { return lcd; }

void displayBegin() {
  lcd.init();
  lcd.setRotation(0);  // Wokwi mirroring is fixed in diagram.json (flipHorizontal)
  lcd.fillScreen(TFT_BLACK);
}

void setBacklight(uint8_t pct) { lcd.setBrightness(pct > 100 ? 255 : pct * 255 / 100); }
