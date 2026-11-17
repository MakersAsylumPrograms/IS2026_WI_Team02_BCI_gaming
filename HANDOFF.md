# EqualCare project handoff

Prepared 17 November 2026. Based on the local `main` checkout at `05daed3`, current source files, saved calibration reports and the local wiki checkout. Remote repository state was not refreshed. This document describes the prototype as it exists; recorded results, team accounts and checks performed for this handoff are distinguished below.

## 1. Goal and scope

EqualCare is an assistive communication prototype for a patient who has difficulty using conventional controls. Eye movements or a joystick navigate a patient menu and submit requests for **Water, Toilet assistance, Adjust position or Call nurse**. A caregiver opens the staff screen, marks a request **Seen**, then **Done**, and the patient sees that progress.

The repository retains the name BCI Gaming, but the implemented sensing is **EOG: electrical signals associated with eye movements**. EEG thought decoding, games and OLED interaction are outside the current implementation scope. The original flowchart includes features that do not describe the current application.

## 2. Current status

The repository contains the complete software path: ESP32 acquisition firmware, USB and Bluetooth readers, signal processing and calibration, a Flask/SQLite backend, React patient/staff screens, and a Windows launcher. CAD, circuit artwork, photographs and showcase media are also present.

| Area | What exists | Evidence boundary |
| --- | --- | --- |
| Patient and staff workflow | Sign-in, menu, confirmation, request persistence, Seen/Done, retry and connection handling | Source and automated tests; no new two-device physical demonstration was performed for this handoff |
| Hardware acquisition | ESP32-S3 firmware samples at 250 Hz; USB serial and BLE transports; joystick channels alongside EOG | Source and saved hardware records; currently flashed firmware and assembled wiring were not inspected |
| Eye controls | Left → PREVIOUS, right → NEXT, double blink → SELECT; current default is `eog-gestures.json` | Current model includes later prompted recordings through 15 November; results below are saved evaluation results |
| Backup controls | Joystick navigation and press; keyboard, mouse and browser BACK shortcut | Implemented; availability still depends on connection, wiring and calibration |
| Physical assembly | FreeCAD/DXF enclosure files, circuit SVG and photos | Day 7 reports soldering, assembly and a resolved blinking issue; it does not identify the repair or provide a repeatable acceptance record |
| Presentation | App landing page plus a separate showcase HTML page with video and a 3D model | Files present; not proof of sensor accuracy or physical fit |

### Current saved eye-model results

`Code/acquisition/models/eog-gestures.json` records a **4-fold evaluation held out by recording**, marked passed:

| Measure | Saved result |
| --- | --- |
| Correct cued looks | 43 / 46; recall 93.5% |
| Look precision | 95.6% |
| Wrong-direction commands | 1 |
| Cued double blinks detected | 70 / 84; recall 83.3% |
| False moves / false SELECTs | 1 / 0 |
| Total evaluated duration | 19.3 minutes |
| False commands per minute | 0.1, as rounded in the report |

These figures are claims in the saved report, not prospective accuracy on a new person or session. **The handoff checks found that all three Chords-Web CSVs differ from the hashes recorded in this model**, so the report's exact source-data provenance is currently unresolved. Neither the committed `HEAD` bytes nor working-copy bytes normalized from CRLF to LF match those hashes. Do not treat these figures as independently reproduced. The approval criteria require look recall and precision of at least 90%, no more than one false command per minute, and zero false SELECTs. **They do not impose a minimum double-blink recall**, so “passed” does not mean every intended selection succeeds.

The older README statement that the model has never been tried with electrodes is stale: later blink/look recordings and audit updates exist. Equally, the team's statement that the prototype was ready does not establish a measured end-to-end acceptance result for the exact current firmware/model combination.

## 3. Architecture and key decisions

The BioAmp and joystick feed the ESP32. The ESP32 streams samples over USB or BLE to Python on the patient laptop. Python interprets gestures and sends input events to the local backend. The patient browser consumes those events and submits confirmed requests. The caregiver browser uses the same backend over the local network.

| Decision | Why it matters |
| --- | --- |
| Process EOG on the patient laptop | Calibration, plots and replay share the same Python pipeline; the ESP32 handles acquisition rather than the application workflow |
| USB or BLE from the board; LAN between laptops | The current firmware does not start Wi-Fi. BLE supports a battery-powered board without a laptop cable; the staff device still needs access to the patient laptop |
| One backend process, SQLite persistence | Requests survive restart while deployment stays small. Input queues and browser ownership leases are in memory, so multiple backend processes are unsupported |
| Same-origin React pages served by the backend | Patient and staff screens use `/api/v1` on the host that served them; no separate production frontend server is needed |
| NEXT, PREVIOUS and SELECT shared across inputs | Eye, joystick and keyboard controls use the same menu logic; joystick remains a fallback when eye control is unreliable |
| Confirmation starts on Cancel | Opening confirmation does not immediately submit a care request |
| Idempotent submission and coalescing of identical active requests | Retrying an uncertain response should not create duplicate requests; different request types may remain active together |
| Short-lived input and a single patient consumer | Stale commands are dropped rather than replayed after a delay; two patient tabs should not both act on the same movement |
| Gesture model replaces the earlier CYCLE model | The older model gave a direction-free command and had weak calibration evidence. The current model distinguishes left, right and double blink |
| Match the Chords-Web filter chain | Live raw samples need the same units and processing as the recordings used for calibration |
| Gate eye forwarding on model approval | The default launcher has `allow_provisional_eog: false`; unapproved models require an explicit override. This is a software gate, not a physical validation certificate |
| Separate Python environments and pinned acquisition package | Keeps backend and scientific/GUI dependencies separate; preserves the Chords implementation used for the data path |
| Local credentials and request database stay outside Git | Each installation retains its own passwords, token and requests. Recorded signal data is deliberately included in the repository |

Implementation details worth preserving: the backend input lifetime is 2 seconds, the patient input lease is 3 seconds, and the device is marked offline after 15 seconds without a heartbeat. The acquisition bridge drops commands it cannot deliver within 1.6 seconds. Requests progress only from `PENDING` to `ACKNOWLEDGED` to `RESOLVED`; “Sent” means stored, not seen by a caregiver.

## 4. File structure

Paths below are relative to the repository root.

| Path | Purpose / handoff note |
| --- | --- |
| `README.md` | Project overview; some status and folder descriptions are outdated |
| `OPENING-GUIDE.md` | Windows setup and ordinary USB startup |
| `Start-EqualCare.cmd` | Normal application entry point |
| `Blink-Test.cmd`, `Blink-Test-Bluetooth.cmd` | Prompted blink recording helpers |
| `Look-Calibration-Bluetooth.cmd` | Records a look test, then recalibrates using available eye recordings; can replace the default model |
| `Code/launcher.py` | Dependency setup, frontend build, backend/acquisition supervision and browser opening |
| `Code/launcher-config.example.json` | Shared defaults; copy to ignored `launcher-config.json` for local changes |
| `Code/backend/app.py` | API, SQLite schema, request lifecycle, input queue and consumer lease |
| `Code/backend/credentials.py`, `run.py`, `start.ps1` | Credentials, server entry and manual setup |
| `Code/backend/simulate_device.py` | Simulated device input for software checks |
| `Code/backend/tests/`, `Code/tests/` | Backend/HTTP/credential tests, optional browser smoke test, launcher tests |
| `Code/backend/instance/` | Ignored local credentials and `equalcare.sqlite3`; preserve when moving an installation |
| `Code/frontend/src/patient/`, `src/staff/` | Patient and caregiver views and controllers |
| `Code/frontend/src/landing/`, `src/components/`, `src/lib/` | Landing page, shared UI, API, session and input handling |
| `Code/frontend/dist/` | Generated frontend served by the backend; ignored and rebuilt locally |
| `Code/firmware/ESP32-S3/ESP32-S3.ino` | Main acquisition firmware, including USB and BLE |
| `Code/firmware/ESP32-S3/sketch_nov14a/` | Standalone joystick diagnostic sketch; not the application firmware |
| `Code/acquisition/eog_acquisition/device.py`, `protocol.py`, `ble.py` | Device links and packet handling |
| `Code/acquisition/eog_acquisition/processing.py`, `chords_filter.py`, `gestures.py` | Live pipeline, compatible filtering and current detector |
| `Code/acquisition/eog_acquisition/chords_calibration.py`, `cli.py` | Calibration, evaluation and command-line workflows |
| `Code/acquisition/eog_acquisition/joystick.py`, `dashboard.py` | Joystick interpretation and backend bridge |
| `Code/acquisition/eog_acquisition/cycle.py`, `blink.py` | Earlier detection approach retained for compatibility |
| `Code/acquisition/models/` | Current gesture model and historical CYCLE models; optional local joystick profile |
| `Code/acquisition/data/recordings/` | Raw/prompted recordings, Chords-Web CSVs and calibration cue sheet |
| `Code/acquisition/data/events/`, `data/diagnostics/` | Command logs and diagnostic results |
| `Code/acquisition/hardware-tests/` | Dated audit reports; read updates and dates before reusing claims |
| `Code/acquisition/tests/` | Processing, recording protection, calibration, joystick and bridge tests |
| `Code/runtime/` | Ignored launcher logs |
| `Documentation/` | OpenAPI contract, original architecture PDFs and circuit SVG |
| `CAD/` | FreeCAD enclosure and DXF files |
| `Electronics/`, `Reference_Data/`, `Photos_Videos/` | Wiring references, supplier datasheets and photos |
| `MA Landing Page/MA Landing Page/` | Separate showcase site and media; distinct from the React app landing page |
| `work/wiki/` | Local checkout of the separate GitHub wiki, on `master`; main repository is on `main` |

## 5. How to resume operation

1. Read `OPENING-GUIDE.md`. Use Windows with Python 3.11–3.13 and Node.js; the guide specifies Node 20+, but compatibility with the installed Vite version should be checked on a fresh machine. First setup needs internet.
2. Use `ESP32-S3.ino`, not the nested joystick test sketch. Existing guides specify ESP32S3 Dev Module, USB CDC disabled, 16 MB flash, OPI PSRAM and the UART connector. Do not flash an assembled board without first recording what firmware/setup is currently in use.
3. Confirm wiring against the actual assembly: EOG GPIO 1; joystick X/Y/switch GPIO 4/5/6; modules at 3V3. Follow the existing opening guide's battery-only precaution during electrode use.
4. Copy `Code/launcher-config.example.json` to `Code/launcher-config.json` if needed. Default input is `COM7`; change it for the real USB port. For the implemented Bluetooth path use `BLE`, or `BLE:<name or address>`. The advertised board name is `NPG-EqualCare-6CH`. Only one program/link should own the stream.
5. Keep `model` set to `Code/acquisition/models/eog-gestures.json` and `allow_provisional_eog` false for the normal path. An older local configuration can still point to a historical model.
6. Double-click `Start-EqualCare.cmd`. Keep its window open. It starts the backend at `http://127.0.0.1:8000` and opens `/patient/`; it does not support an arbitrary backend URL through the launcher configuration.
7. Record the passwords shown on first setup. Staff login ID defaults to `staff`. Reset a forgotten password with `Code\backend\.venv\Scripts\python.exe Code\backend\run.py --set-password patient` (or `staff`), then restart. Do not put passwords or device tokens in this handoff.
8. Open `/staff/` locally or `http://<patient-laptop-IP>:8000/staff/` on the same LAN. Permit the server on the appropriate private network and check hotspot isolation if unreachable.

For a software-only session after dependencies/build are installed, start `Code\backend\.venv\Scripts\python.exe Code\backend\run.py` directly. The normal launcher expects an input device and exits when that process fails. Keyboard and mouse can exercise the patient flow without hardware.

Stopping the backend ends browser sessions, but does not clear saved requests or credentials. Back up the local instance directory privately when transferring an installation. The current deployment uses HTTP and is intended for a controlled local demonstration.

## 6. Known issues, limitations and documentation gaps

| Issue | Status and practical consequence | Next handling |
| --- | --- | --- |
| Calibration source hashes do not match | Current automated provenance test fails; all three Chords-Web source files disagree with the model hashes, including committed bytes and LF-normalized working copies | Recover and compare the original recordings before accepting the saved evaluation; do not just replace hashes to silence the test |
| Synthetic rebound-blink test fails | `test_second_blink_starting_deep_in_the_first_ones_rebound_still_counts` detects the expected pair, then fails because its generated second peak is about 0.799 rather than below 0.45 | Review the synthetic waveform and intended regression condition; this failure alone does not demonstrate a detector miss |
| Double blinks can be missed | Current saved held-out recall is 70/84. Earlier rebound/clipping fixes improved recordings but do not establish reliable selection in every session | Measure new prompted blinks and free viewing; retain joystick selection |
| Natural looks can resemble commands | One horizontal EOG channel cannot identify intent from an otherwise equivalent movement | Include ordinary reading/viewing in acceptance tests, not only prompted gestures |
| Long held look can generate a return command | Detector forgets away position after 8 seconds | Test/document this interaction; review detector behavior if it interferes with use |
| Electrode orientation and session changes matter | Swapping IN+ and IN− reverses directions; thresholds may not transfer | Verify directions and signal before each session; collect fresh data if needed |
| Earlier noisy recordings produced false SELECTs | November 14 audit reports increased false selections on older noisy sessions after sensitivity changes; those numbers refer to an earlier model state | Keep as a regression case; do not present the historical count as a fresh measurement of the current model |
| Setup and calibration documentation is stale | README is USB-centric, calls populated folders placeholders, and describes the November 13 model. Audit contains both “no live test” wording and later live-test updates | Reconcile guides with current BLE firmware, November 15 model and dated evidence |
| Calibration helper can overwrite the default model | `Look-Calibration-Bluetooth.cmd` invokes `calibrate-eyes`; its final message says restart even if calibration exits unsuccessfully | Inspect calibration exit/result and model approval before restarting; prefer `--out` to preserve the current model during experiments |
| Operational startup failures | Wrong/busy COM port, another BLE client, occupied port 8000, missing frontend build or LAN firewall can prevent operation | Close the competing application and use runtime logs; do not treat these as unexplained detector bugs |
| Limited deployment model | One patient workflow, one backend process and one active device-input consumer; no established multi-bed service | Preserve this scope unless expansion is explicitly designed |
| Licensing packaging incomplete | README states hardware/software/documentation licenses, but no standalone license file is present; `Licenses_facts.svg` is locally deleted | Review and restore/package the intended notices separately; do not silently discard the local deletion |

No new hardware fault diagnosis was made for this handoff. The Day 7 blink issue was reported fixed, but its cause and exact repair remain undocumented.

## 7. Verification for this handoff

Checks run on 17 November 2026:

| Check | Result |
| --- | --- |
| Backend and launcher tests | 33 passed, 3 failed. Two HTTP tests fail with Windows socket permission error 10013; the port-detection test also fails in this restricted execution environment |
| Acquisition tests | 75 passed, 8 failed. Six dashboard integration tests are affected by denied local socket connections; two additional failures are the synthetic blink assertion and model/source hash mismatch described above |
| Frontend production build | Passed with the existing installed dependencies |
| Calibration provenance follow-up | All three Chords-Web CSVs mismatch model hashes in working-copy, LF-normalized and committed HEAD forms; simple CRLF conversion does not explain the discrepancy |

The suites are **not green**. Re-run the network-dependent tests in an environment that permits local connections before deciding whether there are additional application faults. Saved model metrics above were read from the current JSON; a full new calibration was not run. Firmware was not flashed or compiled, and no electrodes, Bluetooth device, two-laptop session or browser smoke test was exercised during this review.

Re-run software checks from the repository root:

```powershell
& Code/backend/.venv/Scripts/python.exe -m pytest -q Code/backend/tests Code/tests
Push-Location Code/acquisition
& .venv/Scripts/python.exe -m pytest -q tests
Pop-Location
Push-Location Code/frontend
npm run build
Pop-Location
```

An additional browser integration test is `Code/backend/tests/browser_smoke.cjs`; it requires Playwright and Microsoft Edge and starts its own temporary backend. A successful build alone does not verify browser interactions.

## 8. Working-copy state to preserve

Before this handoff was added, `git status --short` showed:

- Deleted `Licenses_facts.svg`.
- Untracked `Code/acquisition/data/events/events-20261116-190735.csv`; it contains only the CSV header, so it is not evidence of successful operation.
- Untracked `work/`, including the separate wiki checkout. Its own working tree was clean when checked.

These changes were not reverted or committed. The handoff adds only this document to the source tree; the frontend check regenerates ignored build output. Do not add the nested wiki wholesale to the main repository. Publish wiki changes from its own checkout when authorized.

## 9. Next action

**First resolve the calibration source mismatch, then run and record a fresh end-to-end acceptance session.** The model claims approval but the checked-in recordings do not match its recorded source hashes. This evidence gap takes priority over carrying forward the historical accuracy claims.

Compare the three Chords-Web CSVs with their original exports or recoverable history, preserve both versions, and document which data produced the model. If the original sources cannot be recovered, calibrate and evaluate from an explicitly identified current dataset into a new output file. Repair the synthetic test's waveform/assertion without weakening the intended rebound-blink regression check, and rerun the affected tests. Rerun local HTTP tests where socket connections are permitted. Then proceed with physical acceptance below.

Suggested owner: the teammate operating the hardware, with a second teammate on the staff screen. No individual owner is assigned in the reviewed records.

1. Record commit, firmware identity, model ID, USB/BLE transport, wiring/electrode orientation and relevant local configuration without credentials. Preserve the current model before recalibrating.
2. Start with the acquisition guide's check without dashboard forwarding. Try five looks each way and five double blinks, then ordinary reading/viewing. Log intended gestures, recognized commands, wrong directions, misses, unintended movements/selections, duration and stream gaps.
3. If the signal and commands are usable, run the complete patient workflow for all four request types. Confirm that Cancel submits nothing, each confirmed request appears once, and Seen/Done reaches the patient screen. Exercise joystick fallback as well.
4. Check an input disconnect/reconnect and a backend restart: stale gestures should not execute later, saved requests should remain, and sign-in should be required again after restart. Record outcomes rather than assuming them from tests.
5. Save a dated report and associated recordings under `Code/acquisition/hardware-tests/`. Use the model's documented approval criteria for recorded evaluation, but report double-blink recall separately. Investigate any unintended SELECT before calling the session accepted.
6. If the session fails, retain the failed recording, identify whether the fault is transport, signal/detection or request delivery, and recalibrate into a separate output file if appropriate. If it succeeds, reconcile README, opening guide, calibration audit and wiki with that dated evidence.

The handoff is complete when the incoming teammate can start the prototype, distinguish historical results from current evidence, and continue from this acceptance session without replacing or losing the existing calibration and local data.
