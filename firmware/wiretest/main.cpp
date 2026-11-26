// Wiring check for the TFT. Only weak internal pull resistors are used to look for shorts,
// so a wrong wire cannot be damaged by this test. Results go to both serial ports (115200) and the screen.
#include <Arduino.h>
#include "display.h"
#include "pins.h"

// Print to both the native USB port and the UART port, whichever one is plugged in.
String fullLog;  // reprinted every 5 s so a console opened late still sees the result

void logln(const String& s) {
  fullLog += s + '\n';
  Serial.println(s);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.println(s);
#endif
}

struct Pin {
  const char* name;
  int gpio;
};
const Pin PINS[] = {{"BL", PIN_TFT_BL},   {"CS", PIN_TFT_CS},     {"DC", PIN_TFT_DC},
                    {"RST", PIN_TFT_RST}, {"SDA", PIN_TFT_MOSI}, {"SCL", PIN_TFT_SCLK}};
constexpr int N = sizeof(PINS) / sizeof(PINS[0]);

bool toGnd[N], toVcc[N];
int problems = 0;
String report;

void note(const String& line, bool bad) {
  logln(String(bad ? "[LOI] " : "[ok]  ") + line);
  if (bad) {
    problems++;
    report += line + "\n";
  }
}

bool readWith(int gpio, int mode) {
  pinMode(gpio, mode);
  delayMicroseconds(200);
  return digitalRead(gpio);
}

void checkRails() {
  for (int i = 0; i < N; i++) {
    String label = String(PINS[i].name) + " (GPIO " + PINS[i].gpio + ")";
    toGnd[i] = !readWith(PINS[i].gpio, INPUT_PULLUP);
    toVcc[i] = readWith(PINS[i].gpio, INPUT_PULLDOWN);
    if (toGnd[i] && PINS[i].gpio == PIN_TFT_BL)  // BL drives a transistor base on the module: reads low, that is normal
      logln("[info] " + label + " bi keo xuong (transistor den nen); den tat/bat moi giay la day dung");
    else if (toGnd[i])
      note(label + " dinh GND", true);
    else if (toVcc[i])  // the module may pull BL/RST/CS up on purpose; only a hint
      logln("[info] " + label + " bi keo len 3V3 (binh thuong neu man co dien tro keo len)");
    else
      note(label + " khong dinh GND", false);
  }
}

void checkShorts() {
  for (int a = 0; a < N; a++) {
    if (toGnd[a] || toVcc[a]) continue;  // never drive a pin that sits on a rail
    for (int b = 0; b < N; b++) pinMode(PINS[b].gpio, INPUT_PULLUP);
    pinMode(PINS[a].gpio, OUTPUT);
    digitalWrite(PINS[a].gpio, LOW);
    delayMicroseconds(200);
    for (int b = 0; b < N; b++) {
      if (b == a || toGnd[b]) continue;
      if (!digitalRead(PINS[b].gpio)) note(String("Chap ") + PINS[a].name + " voi " + PINS[b].name, true);
    }
    pinMode(PINS[a].gpio, INPUT);
  }
  if (!problems) note("Khong co cap day nao chap nhau", false);
}

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // never block when no console is reading (kept the backlight blink uneven)
#endif
#if ARDUINO_USB_CDC_ON_BOOT
  Serial0.begin(115200);
#endif
  delay(2500);  // time for the USB serial port to come up on the PC
  logln("\n=== Kiem tra day man TFT ===");
  checkRails();
  checkShorts();
  for (int i = 0; i < N; i++) pinMode(PINS[i].gpio, INPUT);

  logln("Bat man hinh...");
  displayBegin();
  setBacklight(100);
  auto& d = display();
  const uint16_t bars[] = {TFT_RED, TFT_GREEN, TFT_BLUE, TFT_WHITE};
  for (int i = 0; i < 4; i++) d.fillRect(i * 60, 0, 60, 120, bars[i]);
  d.setTextDatum(top_center);
  d.setFont(&fonts::FreeSansBold12pt7b);
  d.setTextColor(problems ? TFT_RED : TFT_GREEN, TFT_BLACK);
  d.drawString(problems ? "Co loi day" : "Day OK", 120, 132);
  d.setFont(&fonts::Font2);
  d.setTextColor(TFT_WHITE, TFT_BLACK);
  d.drawString(problems ? report.substring(0, 60) : "Do Xanh la Xanh duong Trang", 120, 168);
  logln(String("=== Xong: ") + problems + " loi. Man phai hien 4 dai DO, XANH LA, XANH DUONG, TRANG ===");
  logln("Neu man den hoac trang: xem lai SCL/SDA/CS/DC/RST. Neu sai mau: bao lai de chinh cau hinh.");
}

void loop() {
  static uint32_t lastPrint = 0;
  if (millis() - lastPrint > 5000) {
    lastPrint = millis();
    Serial.print(fullLog);
#if ARDUINO_USB_CDC_ON_BOOT
    Serial0.print(fullLog);
#endif
  }
  // blink the backlight fully on/off every second so a working BL wire is obvious
  static uint32_t t = 0;
  static bool on = true;
  if (millis() - t > 1000) {
    t = millis();
    on = !on;
    setBacklight(on ? 100 : 0);
  }
}
