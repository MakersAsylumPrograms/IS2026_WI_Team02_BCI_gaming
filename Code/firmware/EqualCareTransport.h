#pragma once
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

// No GPIO or signal-processing assumptions. Call emit once per debounced action.
enum class CareCommand : uint8_t { Next, Previous, Select };
enum class CareSource : uint8_t { Joystick, Eog };

class EqualCareTransport {
 public:
  bool begin();
  // Nonblocking, task-context only. False means queue full/not started; do not
  // keep retrying the action later. Hardware acquisition can continue normally.
  bool emit(CareCommand command, CareSource source);
 private:
  struct Event { uint32_t created; CareCommand command; CareSource source; };
  QueueHandle_t queue_ = nullptr;
  String boot_;
  uint32_t seq_ = 0;
  static void worker(void* self);
  void run();
  int post(const char* path, const String& payload, uint16_t timeoutMs);
};
