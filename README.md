# EqualCare BCI
Maker's Asylum Innovation School · 2026 Winter Diwali Cohort #8 · Team 2

A bedside call system for patients who cannot press a button. The patient moves a highlight with
their eyes (or a joystick) and picks Water, Toilet assistance, Adjust position or Call nurse; the
nurse sees the request on a second screen and marks it Seen, then Done.

```text
eyes (BioAmp EXG Pill) + joystick -> ESP32-S3 -> USB or Bluetooth -> patient laptop -> patient screen
                                                                          |                |
                                                          nurse screen <--+-- request -----+
```

| Eyes | Joystick | Keyboard | Screen |
| --- | --- | --- | --- |
| look **left** and hold | left / up | ← | previous option |
| look **right** and hold | right / down | → | next option |
| **blink twice** | press | Enter | select |
| | | Esc | back |

Educational prototype, not a medical device. With electrodes on a person, run the laptop on
battery with the charger and any mains-powered devices unplugged.

## 1. First start (once per laptop)

You need Windows, **Python 3.11–3.13** (`py install 3.11`), **Node.js 20+** and internet the first time.

```powershell
git clone https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming.git
```

Double-click **`Start-EqualCare.cmd`**. The first start takes a few minutes: it installs two Python
environments, builds the screens, starts the server and opens http://127.0.0.1:8000/patient/.

**Write down the passwords it prints.** They are shown once. The nurse's login ID is `staff`.
Lost them? Close the launcher and set new ones:

```powershell
Code\backend\.venv\Scripts\python.exe Code\backend\run.py --set-password patient
```

```powershell
Code\backend\.venv\Scripts\python.exe Code\backend\run.py --set-password staff
```

Flash the board once (see [Code/acquisition/README.md](Code/acquisition/README.md#3-flash-the-firmware)).

## 2. Every day

1. Electrodes: **IN+ at the outer corner of the left eye, IN− at the outer corner of the right eye,
   REF behind the right ear.**
2. Connect the board with the USB cable (its **UART** port), or power it from a battery for Bluetooth.
   Close Chords-Web, the Arduino Serial Monitor and any phone app connected to the board.
3. Double-click `Start-EqualCare.cmd` and sign in on the patient screen. The board is found
   automatically: USB first, then Bluetooth.
4. Keep the launcher window open. Closing it or Ctrl+C stops everything.

No board? The screens still work with keyboard and mouse; the launcher keeps looking for the board
every 10 seconds.

## 3. Calibrate the eyes for a new person

Everyone's eye signals differ, so calibrate once per person and again if the electrodes move.

1. On the patient screen menu, click **Calibrate eyes** (bottom of the screen; only mouse or
   keyboard can start it, not the eyes).
2. Follow the cue on screen for about 2.5 minutes (two rounds, with a beep at each cue):
   *Look left* / *Look right*: move only your eyes far to that side and hold until the next cue.
   *Blink once* / *Blink twice*: firm blinks, about half a second apart for twice.
   *Look around normally*: use the screen as usual.
3. The result shows how many looks and double blinks were caught and any false commands. If it is
   **approved**, the new settings are used at once. If not, the previous settings stay and the
   screen says which number fell short. Re-seat the electrodes and calibrate again.
4. **Test eyes** shows each signal the sensor picks up (Left, Right, Blink, Double blink) without
   sending anything. Use it to check the electrodes before relying on the eyes.

Approved means, on each round scored with settings fitted on the other: at least 90% of looks
caught, at least 90% of look commands in the right direction, at most one false command a minute
and no false select. That is a short check on one sitting; watch for false commands in real use.

The new settings are saved to `Code/acquisition/models/eog-gestures.json` (the previous one is kept
in `Code/acquisition/data/models/`), and both rounds to `Code/acquisition/data/recordings/`. Commit
them to share them with the team. Without the patient screen: close EqualCare and double-click
`Calibrate-Eyes.cmd`.

## 4. Nurse screen

Same laptop: http://127.0.0.1:8000/staff/. From another laptop or phone on the same Wi-Fi: run
`ipconfig` on the patient laptop, note its Wi-Fi IPv4 address and open
`http://<that address>:8000/staff/`. Allow the server through Windows Firewall (Private networks)
when asked. Sign in with `staff` and the staff password, click **Enable sound alerts**, then use
**Seen** and **Done** (or the keys S and D).

## 5. Something wrong?

| Problem | Fix |
| --- | --- |
| "Port 8000 is already in use" | An older server is still running: close its window, or `Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess` |
| "No board found on USB or Bluetooth" | Cable in the board's UART port? Another program holding it (Chords-Web, Serial Monitor)? On battery: is it powered and not connected to a phone? To force a port, copy `Code/launcher-config.example.json` to `Code/launcher-config.json` and set `"port": "COM7"` (or `"BLE"`) |
| "Node.js is not installed" | Install Node.js 20+, open a new window, start again |
| Eyes do nothing or the wrong way | Use **Test eyes**. Left shows as Right: IN+ and IN− are swapped (calibration also detects and corrects this). Nothing at all: check the plot window, where the raw signal should sit near 1,800–2,000; re-seat the electrodes |
| Many false moves while just reading | Calibrate again; hold looks longer and further out during calibration |
| "ESP32 controller not connected" | The launcher is still looking for the board; keyboard and mouse work meanwhile |
| Nurse device can't open the page | Same Wi-Fi? Right IP? Firewall allowed? Some hotspots block device-to-device traffic |

## Repository

| Folder | Contents |
| --- | --- |
| `Code/backend/` | Flask + SQLite server: logins, requests, device input, eye-calibration jobs. API: [Documentation/openapi.json](Documentation/openapi.json) |
| `Code/frontend/` | Patient and nurse screens (React + Tailwind); `npm run build` writes `dist/`, which the server serves |
| `Code/acquisition/` | Laptop side of the board: eye gestures, joystick, calibration ([guide](Code/acquisition/README.md)) |
| `Code/firmware/` | ESP32-S3 firmware: EOG on GPIO 1, joystick on GPIO 4/5/6, USB and Bluetooth |
| `Code/launcher.py` | What `Start-EqualCare.cmd` runs: sets up, starts the server and the input process |
| `Documentation/`, `Reference_Data/` | Flowchart, block diagram, circuit, API contract; supplier datasheets |
| `Electronics/`, `CAD/`, `Photos_Videos/`, `MA Landing Page/` | Wiring, enclosure, media, project landing page |

Tests (from `Code/`, after one start so the environments exist):

```powershell
backend\.venv\Scripts\python.exe -m pytest -q backend\tests tests
```

```powershell
acquisition\.venv\Scripts\python.exe -m pytest -q acquisition\tests
```

Passwords and the device token live in the git-ignored `Code/backend/instance/credentials.json`;
requests in `Code/backend/instance/equalcare.sqlite3`. `run.py --show-credentials` shows the
login ID and device settings. Plain HTTP and local passwords are meant for a controlled LAN.

## Licenses

- Hardware: [CERN Open Hardware License v2 — Strongly Reciprocal](https://spdx.org/licenses/CERN-OHL-S-2.0.html).
- Software: [MIT License](https://opensource.org/license/mit). The firmware is derived from Upside
  Down Labs' Chords firmware and stays under the GNU GPL v3 or later.
- Documentation: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

Supplier datasheets keep their own notices. See the
[wiki licensing page](https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming/wiki/License).
