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

#include <Arduino.h>
#include <sdkconfig.h>

// ----- Configuration -----
#define TIMER_FREQ 1000000UL  // 1 MHz timer tick
#define NUM_CHANNELS 16
#define HEADER_LEN 3
#define PACKET_LEN (NUM_CHANNELS * 2 + HEADER_LEN + 1)
#define SAMP_RATE 250.0  // 250 samples/sec for 16 channels
#define SYNC_BYTE_1 0xC7
#define SYNC_BYTE_2 0x7C
#define END_BYTE 0x01
#define BAUD_RATE 230400

const uint8_t adcPins[NUM_CHANNELS] = { 1, 11, 2, 12, 3, 13, 4, 14, 5, 15, 6, 16, 7, 17, 8, 18 };

// Global constants and variables
uint8_t packetBuffer[PACKET_LEN];  // The transmission packet
uint8_t currentChannel;            // Current channel being sampled
uint16_t adcValue = 0;             // ADC current value
bool timerStatus = false;          // Timer status bit
bool bufferReady = false;          // Buffer ready status bit

hw_timer_t *timer_1 = NULL;

void IRAM_ATTR ADC_ISR() {
  // ADC value Reading, Converting, and Storing:
  for (currentChannel = 0; currentChannel < NUM_CHANNELS; currentChannel++) {
    adcValue = analogRead(adcPins[currentChannel]);
    // Store current values in packetBuffer to send.
    packetBuffer[((2 * currentChannel) + HEADER_LEN)] = highByte(adcValue);     // Write High Byte
    packetBuffer[((2 * currentChannel) + HEADER_LEN + 1)] = lowByte(adcValue);  // Write Low Byte
  }
  // Increment the packet counter
  packetBuffer[2]++;
  // Set bufferReady status bit to true
  bufferReady = true;
}

void timerStart() {
  timerStatus = true;
  timerStart(timer_1);
}

void timerStop() {
  timerStatus = false;
  bufferReady = false;
  timerStop(timer_1);
}

void setup() {

  Serial.begin(BAUD_RATE);
  Serial.setTimeout(100);
  while (!Serial) {
    ;  // Wait for serial port to connect. Needed for native USB
  }

  // Initialize packetBuffer
  packetBuffer[0] = SYNC_BYTE_1;            // Sync 0
  packetBuffer[1] = SYNC_BYTE_2;            // Sync 1
  packetBuffer[2] = 0;                      // Packet counter
  packetBuffer[PACKET_LEN - 1] = END_BYTE;  // End Byte

  timer_1 = timerBegin(1000000);
  timerAttachInterrupt(timer_1, &ADC_ISR);
  timerAlarm(timer_1, (int)(TIMER_FREQ / SAMP_RATE), true, 0);
  timerStop(timer_1);
  analogReadResolution(12);
}

void loop() {
  // Send data if the buffer is ready and the timer is activ
  if (timerStatus and bufferReady) {


    // Send the packetBuffer to the Serial port
    Serial.write(packetBuffer, PACKET_LEN);
    // Reset the bufferReady flag
    bufferReady = false;
  }

  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();         // Remove extra spaces or newline characters
    command.toUpperCase();  // Normalize to uppercase for case-insensitivity

    if (command == "WHORU")  // Who are you?
    {
      Serial.println("ESP32-S3");
    } else if (command == "START")  // Start data acquisition
    {
      timerStart();
    } else if (command == "STOP")  // Stop data acquisition
    {
      timerStop();
    } else if (command == "STATUS")  // Get status
    {
      Serial.println(timerStatus ? "RUNNING" : "STOPPED");
    } else {
      Serial.println("UNKNOWN COMMAND");
    }
  }
}