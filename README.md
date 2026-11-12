# EqualCare BCI
Maker's Asylum Innovation School · 2026 Winter Diwali Cohort #8 · Team 2

Local patient-assistance prototype: one ESP32, one patient laptop running the
backend, and one nurse laptop on the same Wi-Fi.

```text
Joystick / EOG -> ESP32 -> backend -> patient menu -> confirmed request
                              ^                          |
                              +-- nurse Seen / Done <----+
```

The patient can request Water, Toilet assistance, Adjust position or Call nurse.
The nurse sees waiting requests, marks them Seen and then Done; the patient
screen shows progress. Game and OLED features are outside the current scope.

**Start:** double-click `Start-EqualCare.cmd` on Windows. First setup needs internet and Python 3.11–3.13.
**Guide:** [Setup, passwords and team guide](Code/README.md).
**API contract:** [OpenAPI](Documentation/openapi.json).

| Location | Contents |
| --- | --- |
| Code/backend/ | Flask + SQLite API, launcher, simulator and tests |
| Code/frontend/ | Connected patient/staff screens and shared JavaScript/CSS |
| Code/firmware/ | ESP32-S3 EOG and joystick acquisition over USB-UART |
| Code/acquisition/ | EOG CYCLE detection, joystick fallback, calibration and dashboard bridge |
| Documentation/ | Original flowchart, hardware block diagram and API contract |
| Reference_Data/ | ESP32, BioAmp, joystick and OLED supplier datasheets |
| Electronics/, CAD/, Photos_Videos/ | Placeholders for wiring, mechanical designs and demonstration media |

The original diagrams differ: the flowchart includes game/OLED behaviour; the
hardware block diagram describes the patient/staff workflow implemented here.
The implementation-plan DOCX was supplied outside the repository.
EOG is on GPIO 1; joystick X/Y/switch are GPIO 4/5/6. Both modules use 3V3.
The bundled EOG model is unverified; dashboard EOG control requires independent
evaluation approval. Joystick control remains available. Physical validation is
still required; automated checks do not establish real-person accuracy.

## Licenses

- Hardware: [CERN Open Hardware License v2 — Strongly Reciprocal](https://spdx.org/licenses/CERN-OHL-S-2.0.html).
- Software: [MIT License](https://opensource.org/license/mit).
- Documentation: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

These are the original repository's stated licenses. No standalone LICENSE.md
is present; see the [wiki licensing page](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/License).
Supplier datasheets retain their own notices. Name new photos/videos sequentially
and include the team number and project name.
