#include "EqualCareTransport.h"
#include "config.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <esp_system.h>

bool EqualCareTransport::begin() {
  if (queue_) return false;
  queue_ = xQueueCreate(8, sizeof(Event));
  if (!queue_) return false;
  char boot[33];
  snprintf(boot, sizeof(boot), "%08lx%08lx%08lx%08lx",
           (unsigned long)esp_random(), (unsigned long)esp_random(),
           (unsigned long)esp_random(), (unsigned long)esp_random());
  boot_ = boot;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(EC_WIFI_SSID, EC_WIFI_PASSWORD);
  if (xTaskCreate(worker, "equalcare-net", 6144, this, 1, nullptr) != pdPASS) {
    vQueueDelete(queue_); queue_ = nullptr; return false;
  }
  return true;
}

bool EqualCareTransport::emit(CareCommand command, CareSource source) {
  if (!queue_ || (uint8_t)command > 2 || (uint8_t)source > 1) return false;
  Event event{millis(), command, source};
  return xQueueSend(queue_, &event, 0) == pdTRUE;
}

int EqualCareTransport::post(const char* path, const String& payload, uint16_t timeoutMs) {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(timeoutMs);
  http.setTimeout(timeoutMs);
  if (!http.begin(client, String(EC_SERVER) + path)) return -1;
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + EC_DEVICE_TOKEN);
  const int result = http.POST(payload);
  http.end();
  return result;
}

void EqualCareTransport::worker(void* self) {
  static_cast<EqualCareTransport*>(self)->run();
}

void EqualCareTransport::run() {
  uint32_t heartbeatAt = millis() - 5000;
  uint32_t reconnectAt = millis();
  const char* commands[] = {"NEXT", "PREVIOUS", "SELECT"};
  for (;;) {
    if (WiFi.status() != WL_CONNECTED && millis() - reconnectAt >= 5000) {
      reconnectAt = millis(); WiFi.reconnect();
    }
    if (WiFi.status() == WL_CONNECTED && millis() - heartbeatAt >= 5000) {
      heartbeatAt = millis();
      post("/api/v1/heartbeat", String("{\"device_id\":\"") + EC_DEVICE_ID + "\"}", 200);
    }
    Event event;
    if (xQueueReceive(queue_, &event, pdMS_TO_TICKS(20)) != pdTRUE) continue;
    if (millis() - event.created >= 2000) continue;
    // Rotate epoch before signed-32-bit protocol sequence limit.
    if (seq_ >= 2147483647UL) { boot_ += "r"; seq_ = 0; }
    const uint32_t seq = ++seq_;
    const String payload = String("{\"device_id\":\"") + EC_DEVICE_ID +
      "\",\"boot_id\":\"" + boot_ + "\",\"seq\":" + String(seq) +
      ",\"source\":\"" + (event.source == CareSource::Joystick ? "joystick" : "eog") +
      "\",\"command\":\"" + commands[(uint8_t)event.command] + "\"}";
    while (millis() - event.created < 2000) {
      const uint32_t remaining = 2000 - (millis() - event.created);
      if (remaining < 100) break;
      if (WiFi.status() == WL_CONNECTED) {
        const int status = post("/api/v1/input-events", payload, (uint16_t)min(remaining / 3, (uint32_t)200));
        if (status >= 200 && status < 300) break;
        if (status >= 400 && status < 500) {
          Serial.printf("EqualCare rejected event: HTTP %d\n", status); break;
        }
      }
      vTaskDelay(pdMS_TO_TICKS(50));
    }
  }
}
