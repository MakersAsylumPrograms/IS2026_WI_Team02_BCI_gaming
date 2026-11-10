# Hardware and firmware handoff

Status: pending hardware-owner completion; no GPIOs assigned by backend code.

| Item | Hardware owner supplies | Firmware owner verifies |
| --- | --- | --- |
| ESP32 | Exact development-board model/revision and photo | Arduino board selection and available pins |
| BioAmp | Exact version, supply voltage, measured output range | ADC compatibility, sampling/filtering/calibration |
| Joystick X/Y | Supply voltage and chosen ADC GPIOs | Wi-Fi-compatible ADC use, centre dead zone and thresholds |
| Joystick switch | GPIO and pull-up/down wiring | Edge detection, debounce, one SELECT per press/release |
| Power | Isolated battery and regulator wiring | Startup/restart behaviour and low-voltage symptoms |
| Electrodes | Placement and isolated test procedure | EOG feasibility, noise rejection and fallback |
| Command mapping | Agreed gestures/directions | NEXT, PREVIOUS, SELECT through EqualCareTransport.emit |

Record each physical connection as `module pin -> board GPIO/power pin`, including
voltage, shared ground within the isolated system, and any conditioning components.
Attach a dated schematic/photo and name the hardware/firmware reviewers here.

The supplied hardware diagram requires isolated battery power during electrode
use, with no USB or charger attached. USB Serial simulation is for bench testing
without attached electrodes. Backend setup and simulators do not validate the
electrical setup or authorize human testing.

Before the joint demo: confirm firmware emits one event per action, joystick
fallback remains usable, Wi-Fi reconnect drops stale commands, and ESP32 reboot
creates a new boot ID. No frontend/backend changes should be needed for pin changes.
