# EqualCare setup and team guide

## Start and connect

Normal Windows startup: double-click root `Start-EqualCare.cmd`. It starts the backend
and one combined EOG/joystick acquisition process, then opens the patient page.
First setup uses isolated venvs and needs internet; unchanged subsequent starts work
offline. Optional settings are in `Code/launcher-config.json` (copy the example).
Keep the launcher open; Ctrl+C stops only its child processes. Port 8000 must be free.
Runtime logs are local under `Code/runtime/`. At the team's request, the launcher
enables the latest provisional EOG fit for prototype navigation. It remains unverified;
false navigation is expected. Joystick press selects the highlighted item. Set
`allow_provisional_eog` to false to require independent evaluation approval.
The manual backend-only instructions below remain useful for diagnostics.

Requires Python 3.11+; initial dependency installation needs internet.
From the repository root in PowerShell:

```powershell
& ./Code/backend/start.ps1
```

Leave the terminal open; Ctrl+C stops the server. The helper creates a virtual
environment, installs requirements and starts Flask through Waitress on port 8000.
After pulling new code, stop the old server before starting again. The launcher
refuses to start if port 8000 is still in use, because on Windows a second server
can bind the same port silently while the old code keeps answering.
For subsequent offline starts after installation:

```powershell
& Code/backend/.venv/Scripts/python.exe Code/backend/run.py
```

If PowerShell blocks the helper, run these directly:

```powershell
py -3 -m venv Code/backend/.venv
& Code/backend/.venv/Scripts/python.exe -m pip install -r Code/backend/requirements.txt
& Code/backend/.venv/Scripts/python.exe Code/backend/run.py
```

| Screen/check | Address |
| --- | --- |
| Patient on backend laptop | http://localhost:8000/patient/ |
| Nurse on second laptop | http://PATIENT-LAPTOP-IP:8000/staff/ |
| Server check | http://PATIENT-LAPTOP-IP:8000/api/v1/health |

Health returns JSON with `status: "ok"` and `version: "1"`.
Both screens can run in the same browser on one laptop (for example, two tabs):
each screen signs in separately and signing in to one does not sign out the other.

## Passwords, token and local data

Patient login stays password-only. Staff signs in with login ID **staff** and its
existing password. No registration is required. Sign-in survives page reloads;
restarting the backend ends browser sessions. Logout is explicit on both screens.

On first installation the launcher prints newly generated passwords once. Existing
passwords and the ESP32 token are preserved when credentials migrate to hashes.
Passwords cannot be displayed after migration. View the staff login ID and device
settings with:

```powershell
& Code/backend/.venv/Scripts/python.exe Code/backend/run.py --show-credentials
```

| Saved setting | Purpose |
| --- | --- |
| PATIENT_PASSWORD_HASH / STAFF_PASSWORD_HASH | Hashed existing passwords; not plaintext login values |
| STAFF_LOGIN_ID | Staff login ID; defaults to staff |
| PATIENT_NAME | Default patient's display name; defaults to Bed 1 |
| DEVICE_ID | Defaults to esp32-01; matches firmware EC_DEVICE_ID |
| DEVICE_TOKEN | Firmware/simulator credential; copy into EC_DEVICE_TOKEN |

Local settings are in gitignored `Code/backend/instance/credentials.json`.
Requests remain in `Code/backend/instance/equalcare.sqlite3`. Neither is reset
by restarting. The browser-signing secret exists only in memory for that run.

Change a password using a readable prompt (saved as a hash):

```powershell
& Code/backend/.venv/Scripts/python.exe Code/backend/run.py --set-password staff
```

Use `patient` instead of `staff` for the patient password. Restart the backend.
Alternatively add `STAFF_PASSWORD` or `PATIENT_PASSWORD` with a readable string to
credentials.json; restart converts it to the corresponding hash and removes the
plaintext entry. Never type a readable password into a `_PASSWORD_HASH` field.

Environment overrides EQUALCARE_PATIENT_PASSWORD, EQUALCARE_STAFF_PASSWORD,
EQUALCARE_STAFF_LOGIN_ID, EQUALCARE_PATIENT_NAME, EQUALCARE_DEVICE_ID and
EQUALCARE_DEVICE_TOKEN apply to that process only and take precedence over the file.
The simulator reads the saved device
token unless its terminal overrides EQUALCARE_DEVICE_TOKEN.

## Network and ESP32

The ESP32 (EOG + joystick) connects to the **patient laptop by USB**; `acquisition/`
on that laptop sends its commands to the backend (see [acquisition/README.md](acquisition/README.md)).
Only the nurse laptop uses the network:

1. Join both laptops to the same LAN/hotspot.
2. Run `ipconfig` on the patient laptop and find its Wi-Fi IPv4 address.
   Reserve that address where possible; keep the laptop awake.
3. Allow inbound TCP 8000 for the backend on Windows' Private network profile.
   Check the health URL from the nurse laptop. If unreachable, check the IP,
   firewall and hotspot/client isolation.

The frontend uses the relative API_BASE `/api/v1` in shared/api.js; keep it
same-origin. Opening the nurse URL automatically uses that laptop-hosted backend.
If the host IP changes, update the nurse URL.
Run one backend process; command buffers/leases are in memory. Plain HTTP and
local credentials are intended for a controlled LAN demonstration.

## Screens and controls

Patient flow: menu -> confirmation -> sending -> Sent / Help is coming / Done.
Confirmation initially highlights Cancel; completed requests return to the menu
after five seconds. Uncertain delivery offers Try again with the same saved
idempotency key. The full flow works with just NEXT and SELECT.

| Input | Action |
| --- | --- |
| Right/down arrow | NEXT |
| Left/up arrow | PREVIOUS |
| Enter/Space | SELECT |
| Escape | BACK (browser shortcut only) |
| Mouse option | Highlight and select |
| Nurse S / D | Mark top waiting request Seen / top seen request Done |

Held keyboard repeats are ignored; SELECT has a 700 ms debounce. Nurse cards show
waiting requests first, oldest within each status group, with Seen/Done controls
and resolved history. Click Enable sound alerts to allow beeps for new requests;
existing requests on initial load do not sound an alert.

Stored statuses: PENDING -> ACKNOWLEDGED -> RESOLVED. Seen means acknowledged;
Done means resolved. Repeating an action never moves status backwards. Identical
active request types are coalesced; different types can remain active together.
PENDING means saved by the backend, not that the nurse has seen it.

## File responsibilities and shared interface

| Files | Responsibility |
| --- | --- |
| backend/app.py | API, permissions, SQLite requests, device deduplication and leases |
| backend/credentials.py, run.py, start.ps1, requirements.txt | Credential migration/loading, server startup and dependencies |
| backend/simulate_device.py | Hardware-free command sender and heartbeat |
| backend/tests/test_api.py, test_http.py | API/reliability tests and real HTTP simulator test |
| backend/tests/browser_smoke.cjs | Patient/nurse browser integration checks |
| frontend/index.html, patient/index.html, staff/index.html | Landing page and dashboard markup |
| frontend/shared/api.js | Shared HTTP client, API_BASE, request labels and server-clock offset |
| frontend/shared/common.js | Sessions, polling and connection indicators |
| frontend/shared/input.js | Keyboard/mouse/device commands; fromDevice() is the ESP32 entry point |
| frontend/shared/patient.js | Menu, confirmation, submission, retry and status screens |
| frontend/shared/staff.js | Request ordering, Seen/Done, waiting timers and sound alerts |
| frontend/shared/styles.css | Shared and screen-specific styles |
| firmware/ESP32-S3/ESP32-S3.ino | ESP32-S3: EOG GPIO 1 + 3V3 joystick GPIO 4/5/6, 250 Hz USB Chords packets |
| acquisition/ | Laptop side: eye CYCLE detection, joystick, and sending commands to the backend |

Full request/response schemas: [OpenAPI](../Documentation/openapi.json).
Browser sign-in uses a session cookie; writes require X-CSRF-Token. One cookie can
hold both roles, so browser calls send `X-EqualCare-Role: patient|staff` (optional
when only one role is signed in).
Staff login sends role, login_id and password; patient login sends role and password.
The shared API client handles these. Device calls use
`Authorization: Bearer <device-token>`; the device token never belongs in frontend code.

Firmware streams raw Chords packets over USB-UART. The laptop acquisition process
classifies EOG and joystick input and sends commands through `DashboardLink` with
one sequence counter. Its bounded queue discards stale commands rather than replaying
navigation after an outage. The removed firmware HTTP transport API is no longer used.

POST /api/v1/input-events with:

```json
{"device_id":"esp32-01","boot_id":"random-per-restart","seq":1,"source":"joystick","command":"NEXT"}
```

Retries reuse the same payload; seq increases across both input sources.
Supported firmware commands are NEXT/PREVIOUS/SELECT; BACK is frontend-only.
HTTP 200 with accepted:false means a handled duplicate/old event; stop retrying.
Send POST /api/v1/heartbeat with device_id every five seconds.
Device connectivity expires after 15 seconds.

The patient polls input every 250 ms and statuses every second. Each page has its
own X-Consumer-ID; only one consumes device input, with a three-second lease.
After reconnect/refresh it synchronizes to the latest cursor and epoch rather
than replaying old navigation. Backend events expire after two seconds and the
buffer holds 128 events. Requests and retry keys persist in SQLite; navigation,
sequence history and input leases reset when the backend restarts.

## Simulator, tests and remaining hardware work

With the backend running:

```powershell
& Code/backend/.venv/Scripts/python.exe Code/backend/simulate_device.py
```

Type n/p/s then Enter; q quits. Optional flags: --server http://IP:8000,
--source eog, --device-id ID, or --commands NEXT SELECT.
The simulator starts a heartbeat automatically.

```powershell
& Code/backend/.venv/Scripts/python.exe -m pytest Code/backend/tests -q
node Code/backend/tests/browser_smoke.cjs
```

The optional browser test requires Node's playwright package on NODE_PATH and
installed Microsoft Edge. PYTHON can override its default venv interpreter.
Tests use temporary databases and test credentials. Coverage includes lifecycle,
retries, persistence, permissions, concurrent sends, expired/duplicate commands,
confirmation/cancel, offline recovery and device-only navigation.

Device path: BioAmp EXG Pill v3 (GPIO 1) and joystick (GPIO 4/5/6) -> ESP32-S3 running
`firmware/ESP32-S3` (Chords protocol, 250 Hz) -> USB-to-UART -> `acquisition/` on the
patient laptop. A held look to either side is one eye CYCLE (PREVIOUS, source `eog`); joystick
pushes are NEXT/PREVIOUS and its press is SELECT (source `joystick`). One gesture = one command;
analog samples never leave the laptop. Run `python -m eog_acquisition run --dashboard` from
`acquisition/`; see [acquisition/README.md](acquisition/README.md). The backend does not process
raw signals.

During electrode use the laptop runs on battery with its charger and any
mains-powered peripherals disconnected (the USB-to-UART link goes only to that
battery-powered laptop). `backend/simulate_device.py` sends commands without hardware.

Original flowchart/hardware diagram: [Documentation](../Documentation/).
Supplier datasheets: [Reference_Data](../Reference_Data/).
