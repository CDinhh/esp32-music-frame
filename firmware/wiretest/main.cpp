// Wiring check for the TFT. Only weak internal pull resistors are used to look for shorts,
// so a wrong wire cannot be damaged by this test. Results go to the serial console (115200) and the screen.
#include <Arduino.h>
#include "display.h"
#include "pins.h"

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
  Serial.println((bad ? "[LOI] " : "[ok]  ") + line);
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
    bool lowWithPullup = !readWith(PINS[i].gpio, INPUT_PULLUP);
    bool highWithPulldown = readWith(PINS[i].gpio, INPUT_PULLDOWN);
    toGnd[i] = lowWithPullup;
    toVcc[i] = highWithPulldown;
    if (lowWithPullup)
      note(String(PINS[i].name) + " (GPIO " + PINS[i].gpio + ") dinh GND", true);
    else if (highWithPulldown)
      // the module may pull BL/RST/CS up on purpose; only a hint
      Serial.printf("[info] %s (GPIO %d) bi keo len 3V3 (binh thuong neu man co dien tro keo len)\n", PINS[i].name, PINS[i].gpio);
    else
      note(String(PINS[i].name) + " khong dinh GND", false);
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
      if (!digitalRead(PINS[b].gpio))
        note(String("Chap ") + PINS[a].name + " voi " + PINS[b].name, true);
    }
    pinMode(PINS[a].gpio, INPUT);
  }
  if (!problems) note("Khong co cap day nao chap nhau", false);
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println("\n=== Kiem tra day man TFT ===");
  checkRails();
  checkShorts();
  for (int i = 0; i < N; i++) pinMode(PINS[i].gpio, INPUT);

  Serial.println("Bat man hinh...");
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
  Serial.printf("=== Xong: %d loi. Man phai hien 4 dai DO, XANH LA, XANH DUONG, TRANG ===\n", problems);
  Serial.println("Neu man den hoac trang: xem lai SCL/SDA/CS/DC/RST. Neu sai mau: bao lai de chinh cau hinh.");
}

void loop() {
  // breathe the backlight so a working BL wire is obvious
  static uint32_t t = 0;
  if (millis() - t > 30) {
    t = millis();
    setBacklight(40 + 60 * (0.5 + 0.5 * sin(millis() / 600.0)));
  }
}
