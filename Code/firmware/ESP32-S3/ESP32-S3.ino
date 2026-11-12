// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program. If not, see <https://www.gnu.org/licenses/>.
//
// Copyright (c) 2025 Krishnanshu Mittal - krishnanshu@upsidedownlabs.tech
// Copyright (c) 2025 Deepak Khatri - deepak@upsidedownlabs.tech
// Copyright (c) 2025 Upside Down Labs - contact@upsidedownlabs.tech
// Copyright (c) 2025 Mahesh Tupe - tupemahesh@upsidedownlabs.tech
//
// At Upside Down Labs, we create open-source DIY neuroscience hardware and software.
// Our mission is to make neuroscience affordable and accessible for everyone.
// By supporting us with your purchase, you help spread innovation and open science.
// Thank you for being part of this journey with us!
//
// ---------------------------------------------------------------------------
// Modified 2026-11 by EqualCare (IS2026 WI Team 02) for single-channel EOG
// with a BioAmp EXG Pill v3 on GPIO 1, plus a 3V3-powered analog joystick. Changes from the
// upstream Chords ESP32-S3 firmware, which remain under the GNU GPL v3 or later:
//   * Channel 1 = GPIO 1 (EOG). Channels 2-4 = joystick X (GPIO 4), Y (GPIO 5) and
//     button (GPIO 6, sent as 1 = pressed, 0 = released). Slots 5-16 are kept in
//     the official 36-byte packet so Chords decoders still work, but are always 0
//     (inactive). Packet layout and byte order are unchanged.
//   * ADC reads moved out of the timer interrupt. The interrupt only wakes a
//     high-priority sampler task; complete packets go through a bounded queue
//     and loop() writes them to Serial.
//   * Missed sampling intervals are never filled in. The packet counter
//     advances once per timer tick, so a late or dropped sample shows up on the
//     host as a counter gap. STATS reports how many were late or dropped.
//   * Starts stopped (the upstream copy in this repo auto-started and printed
//     plain text for the Serial Plotter). Only replies to commands are text, and
//     they are written between whole packets.
//   * Explicit 12-bit resolution and 11 dB attenuation on the analog pins so the
//     ADC covers 0-3.3 V (readings above ~3.1 V clip at 4095). EOG is read first
//     in every tick, so the joystick never delays the EOG sample.
//   * Wi-Fi and Bluetooth are never started (no radio code is linked).
//   * Added STATS. WHORU, START, STOP and STATUS behave as upstream.
//
// Tested build: Arduino IDE board "ESP32S3 Dev Module", esp32 by Espressif
// core 3.3.12, USB CDC On Boot: Disabled, Upload Mode: UART0 / Hardware CDC,
// Flash Size: 16MB, PSRAM: OPI PSRAM (ESP32-S3-DevKitC-1 N16R8).
// Connect the laptop to the board's "UART" USB port (USB-to-UART bridge).
// Close the Arduino Serial Monitor/Plotter before Chords or Python opens the port.
//
// Serial protocol, 230400 baud 8N1. Commands end with '\n':
//   WHORU  -> "ESP32-S3"           START -> begin streaming (no reply)
//   STOP   -> stop streaming        STATUS -> "RUNNING" or "STOPPED"
//   STATS  -> "STATS missed=<n> dropped=<n>" since the last START
// Packet (36 bytes, 250 per second while running):
//   [0]=0xC7 [1]=0x7C [2]=counter (0-255, wraps) [3..34]=16 channels, each a
//   big-endian uint16 (channel 1 = GPIO 1 EOG raw ADC counts 0-4095; channels 2-4 =
//   joystick X, Y counts 0-4095 and button 0/1; channels 5-16 = 0) [35]=0x01
// ---------------------------------------------------------------------------

#include <Arduino.h>

constexpr uint8_t EOG_PIN = 1;          // BioAmp EXG Pill OUT -> GPIO 1 (ADC1 channel 0)
constexpr uint8_t JOY_X_PIN = 4;        // Joystick VRx -> GPIO 4 (ADC1 channel 3), powered at 3V3
constexpr uint8_t JOY_Y_PIN = 5;        // Joystick VRy -> GPIO 5 (ADC1 channel 4)
constexpr uint8_t JOY_SW_PIN = 6;       // Joystick SW  -> GPIO 6 (pressed = LOW, internal pull-up)
constexpr uint8_t NUM_CHANNELS = 16;    // Slots in the official ESP32-S3 Chords packet
constexpr uint8_t HEADER_LEN = 3;
constexpr uint8_t PACKET_LEN = NUM_CHANNELS * 2 + HEADER_LEN + 1;  // 36 bytes
constexpr uint32_t SAMPLE_RATE_HZ = 250;
constexpr uint32_t TIMER_FREQ_HZ = 1000000;  // 1 MHz timer tick
constexpr uint32_t BAUD_RATE = 230400;
constexpr uint8_t SYNC_BYTE_1 = 0xC7;
constexpr uint8_t SYNC_BYTE_2 = 0x7C;
constexpr uint8_t END_BYTE = 0x01;
constexpr UBaseType_t QUEUE_DEPTH = 64;  // 256 ms of packets
constexpr size_t COMMAND_MAX = 16;

struct Packet {
  uint8_t bytes[PACKET_LEN];
};

static hw_timer_t *sampleTimer = nullptr;
static TaskHandle_t samplerTask = nullptr;
static QueueHandle_t packetQueue = nullptr;

static volatile bool running = false;           // Written by loop(), read by the sampler
static volatile bool restartRequested = false;  // START asks the sampler to reset its counter
static volatile uint32_t missedIntervals = 0;   // Timer ticks the sampler could not service
static volatile uint32_t droppedPackets = 0;    // Packets lost because the queue was full

// The interrupt does no ADC or Serial work: it only wakes the sampler task.
void IRAM_ATTR onSampleTimer() {
  BaseType_t woken = pdFALSE;
  vTaskNotifyGiveFromISR(samplerTask, &woken);
  if (woken == pdTRUE) portYIELD_FROM_ISR();
}

static void samplerLoop(void *) {
  Packet packet = {};
  packet.bytes[0] = SYNC_BYTE_1;
  packet.bytes[1] = SYNC_BYTE_2;
  packet.bytes[PACKET_LEN - 1] = END_BYTE;  // Channel slots 5-16 stay 0 (inactive)
  uint8_t counter = 0;

  for (;;) {
    // Returns how many ticks happened since the last wake (normally 1).
    const uint32_t ticks = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    if (restartRequested) {
      restartRequested = false;
      counter = 0;
    }
    if (!running || ticks == 0) continue;

    // Ticks we could not service were never sampled: skip their counter values
    // so the host sees a gap instead of a fabricated sample.
    if (ticks > 1) {
      missedIntervals = missedIntervals + (ticks - 1);  // Only this task writes while running
      counter += (uint8_t)(ticks - 1);
    }

    const uint16_t value = analogRead(EOG_PIN);     // EOG first: its timing matters most
    const uint16_t joystick[3] = {(uint16_t)analogRead(JOY_X_PIN), (uint16_t)analogRead(JOY_Y_PIN),
                                  (uint16_t)(digitalRead(JOY_SW_PIN) == LOW ? 1 : 0)};
    packet.bytes[2] = counter++;
    packet.bytes[HEADER_LEN] = highByte(value);      // Channel 1, big-endian
    packet.bytes[HEADER_LEN + 1] = lowByte(value);
    for (uint8_t i = 0; i < 3; i++) {                // Channels 2-4
      packet.bytes[HEADER_LEN + 2 + 2 * i] = highByte(joystick[i]);
      packet.bytes[HEADER_LEN + 3 + 2 * i] = lowByte(joystick[i]);
    }
    if (xQueueSend(packetQueue, &packet, 0) != pdTRUE) droppedPackets = droppedPackets + 1;
  }
}

static void startStreaming() {
  if (running) return;
  timerStop(sampleTimer);
  xQueueReset(packetQueue);
  ulTaskNotifyValueClear(samplerTask, 0xFFFFFFFF);  // Forget ticks from before START
  missedIntervals = 0;
  droppedPackets = 0;
  restartRequested = true;
  running = true;
  timerWrite(sampleTimer, 0);
  timerStart(sampleTimer);
}

static void stopStreaming() {
  running = false;
  timerStop(sampleTimer);
  xQueueReset(packetQueue);  // Nothing is sent after STOP
}

static void handleCommand(char *command) {
  for (char *c = command; *c; ++c) *c = toupper((unsigned char)*c);
  if (strcmp(command, "WHORU") == 0) {
    Serial.println("ESP32-S3");
  } else if (strcmp(command, "START") == 0) {
    startStreaming();
  } else if (strcmp(command, "STOP") == 0) {
    stopStreaming();
  } else if (strcmp(command, "STATUS") == 0) {
    Serial.println(running ? "RUNNING" : "STOPPED");
  } else if (strcmp(command, "STATS") == 0) {
    Serial.printf("STATS missed=%lu dropped=%lu\n", (unsigned long)missedIntervals,
                  (unsigned long)droppedPackets);
  } else {
    Serial.println("UNKNOWN COMMAND");
  }
}

// Reads command bytes without blocking; a command ends at '\n' or '\r'.
static void pollCommands() {
  static char line[COMMAND_MAX + 1];
  static size_t length = 0;
  static bool overflow = false;
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      line[length] = '\0';
      if (overflow) Serial.println("UNKNOWN COMMAND");
      else if (length > 0) handleCommand(line);
      length = 0;
      overflow = false;
    } else if (c != ' ' && c != '\t') {
      if (length < COMMAND_MAX) line[length++] = c;
      else overflow = true;
    }
  }
}

void setup() {
  Serial.setTxBufferSize(2048);  // 8 packets of headroom; 250 x 36 B/s uses ~40% of 230400 baud
  Serial.begin(BAUD_RATE);

  analogReadResolution(12);
  for (uint8_t pin : {EOG_PIN, JOY_X_PIN, JOY_Y_PIN}) {
    analogSetPinAttenuation(pin, ADC_11db);
    analogRead(pin);  // Configure each ADC channel before sampling starts
  }
  pinMode(JOY_SW_PIN, INPUT_PULLUP);

  packetQueue = xQueueCreate(QUEUE_DEPTH, sizeof(Packet));
  // Core 0 is otherwise idle because the radios are off; loop() runs on core 1.
  xTaskCreatePinnedToCore(samplerLoop, "eog-sampler", 4096, nullptr, configMAX_PRIORITIES - 2,
                          &samplerTask, 0);
  if (packetQueue == nullptr || samplerTask == nullptr) {
    for (;;) {
      Serial.println("EOG firmware could not allocate its queue or task");
      delay(1000);
    }
  }

  sampleTimer = timerBegin(TIMER_FREQ_HZ);
  timerAttachInterrupt(sampleTimer, &onSampleTimer);
  timerAlarm(sampleTimer, TIMER_FREQ_HZ / SAMPLE_RATE_HZ, true, 0);
  timerStop(sampleTimer);  // Start stopped; START begins streaming
}

void loop() {
  Packet packet;
  if (running) {
    // Wait up to 2 ms for a packet so commands are still checked promptly.
    if (xQueueReceive(packetQueue, &packet, pdMS_TO_TICKS(2)) == pdTRUE) {
      do {
        if (running) Serial.write(packet.bytes, PACKET_LEN);  // Whole packets only
      } while (running && xQueueReceive(packetQueue, &packet, 0) == pdTRUE);
    }
  } else {
    vTaskDelay(pdMS_TO_TICKS(2));
  }
  pollCommands();
}
