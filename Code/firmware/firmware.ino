// Transport-only demo: no electrodes, GPIO assumptions or EOG classifier.
// Arduino sketch folder is named firmware, matching this file.
#include "EqualCareTransport.h"
EqualCareTransport transport;
void setup() {
  Serial.begin(115200);
  if (!transport.begin()) Serial.println("EqualCare transport could not start");
  Serial.println("Serial simulator: n=NEXT, p=PREVIOUS, s=SELECT");
}
void loop() {
  if (Serial.available()) {
    const char c = Serial.read();
    if (c == 'n') transport.emit(CareCommand::Next, CareSource::Joystick);
    if (c == 'p') transport.emit(CareCommand::Previous, CareSource::Joystick);
    if (c == 's') transport.emit(CareCommand::Select, CareSource::Joystick);
  }
  delay(1);
}
