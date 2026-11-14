// Pin definitions for ESP32-S3
const int PIN_VRX = 4;  // X-axis (ADC1_CH3)
const int PIN_VRY = 5;  // Y-axis (ADC1_CH4)
const int PIN_SW  = 6;  // Z-axis button (Digital)

void setup() {
  // ESP32-S3 typically uses 115200 baud for Serial Monitor
  Serial.begin(115200);
  
  // Enable internal pull-up resistor for the joystick switch
  // Reads HIGH (1) when unpressed, LOW (0) when pressed
  pinMode(PIN_SW, INPUT_PULLUP);

  // Set ADC resolution to 12-bit (0 - 4095)
  analogReadResolution(12);
}

void loop() {
  // Read analog values (0 to 4095, center is roughly ~2048)
  int xValue = analogRead(PIN_VRX);
  int yValue = analogRead(PIN_VRY);
  
  // Invert button state so 1 = Pressed, 0 = Released
  int zPressed = !digitalRead(PIN_SW);

  // Print formatted output to Serial Monitor
  Serial.print("X: ");
  Serial.print(xValue);
  Serial.print("\t | Y: ");
  Serial.print(yValue);
  Serial.print("\t | Z (Button): ");
  Serial.println(zPressed ? "PRESSED" : "Released");

  delay(1000); // Read 1 times per second
}
