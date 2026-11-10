# EqualCare team integration guide

## Shared system

One patient and one ESP32 use a backend hosted on the patient laptop. The nurse
laptop connects to that same backend. All three devices join the same LAN.
The ESP32 sends only small classified commands, never raw EOG traces. Frontend
and firmware members can work independently against `openapi.json` in this folder.

```text
Electrodes / BioAmp -> acquisition + classifier --+
                                                +-> ESP32 transport -> HTTP API
Joystick -> debounce + edge detection -----------+                         |
                                                                          v
Patient browser <- command polling -> Flask + SQLite <- request polling -> Nurse browser
```

The patient browser turns SELECT into a request. The backend never interprets
gestures or maintains menu selection. The ESP32 never maintains patient request
statuses or renders dashboard data. Game logic and OLED integration are excluded.

## Start the backend (backend owner)

Requires Python 3.11+ and network access for the initial dependency installation.
In PowerShell from the repository root:

```powershell
& ./Code/backend/start.ps1
```

If local script policy blocks the helper, run its equivalent commands directly:

```powershell
py -3 -m venv Code/backend/.venv
& Code/backend/.venv/Scripts/python.exe -m pip install -r Code/backend/requirements.txt
& Code/backend/.venv/Scripts/python.exe Code/backend/run.py
```

The first run generates different patient/staff passwords, a device token, and a
session signing secret in gitignored `Code/backend/instance/credentials.json`.
It prints the login credentials in your local console. Retrieve them later with:

```powershell
& Code/backend/.venv/Scripts/python.exe Code/backend/run.py --show-credentials
```

Environment variables `EQUALCARE_PATIENT_PASSWORD`, `EQUALCARE_STAFF_PASSWORD`,
`EQUALCARE_DEVICE_TOKEN`, `EQUALCARE_SECRET`, and `EQUALCARE_DEVICE_ID` override local
defaults. Default device ID: `esp32-01`. Keep secrets off Git and screenshots.
Storage is `Code/backend/instance/equalcare.sqlite3`. Use `run.py` to preserve the
signing secret across restarts. Run **one backend process**, not multiple workers;
command buffers and input-consumer leases are intentionally in process memory.

- Patient: `http://localhost:8000/patient/` on the host laptop.
- Nurse: `http://<patient-laptop-ip>:8000/staff/` on the second laptop.
- Health: `http://<patient-laptop-ip>:8000/api/v1/health`.
- For both roles on one laptop, use separate browser profiles or a private window;
  session cookies are shared between tabs in one profile.

After setup, starting `run.py` does not require internet. The app has no CDN assets.

## Network checklist (all members)

1. Reserve the patient laptop's IPv4 address in the router's DHCP settings. Put
   that address in firmware `EC_SERVER`; never use localhost on another device.
2. ESP32-WROVER uses 2.4 GHz Wi-Fi. Laptops may use either band if routed on the
   same LAN. Disable guest/client isolation for this demonstration network.
3. On Windows, permit inbound TCP port 8000 for the backend on the **Private**
   network profile. Do not expose it through router port forwarding.
4. Open `/api/v1/health` from the nurse laptop before debugging firmware. If it
   fails, check address, firewall, router isolation, and laptop sleep settings.
5. Keep the backend laptop awake. If its address changes, update EC_SERVER and
   nurse browser URL together. Plain HTTP and local credentials are scoped to
   this controlled demonstration LAN; they are not internet authentication.

## Frontend handoff (patient and nurse owners)

The supplied HTML/CSS screens are replaceable reference clients. The stable
boundary is `/api/v1`; no frontend framework is required. Import the reusable
`Code/frontend/shared/api.js` client or implement the OpenAPI contract directly.

Serve built frontend files under the existing patient/staff directories, or use
a development-server proxy for `/api` to `http://127.0.0.1:8000`. All browser API
calls must reach the same backend origin; broad CORS is deliberately disabled.
For a framework build, expose its generated assets through an explicit static
route and retain the API routes. Current CSP permits same-origin scripts/styles.

```javascript
import {EqualCareAPI, newId} from '/assets/api.js';
const api = new EqualCareAPI();
await api.login('patient', password); // cookie + automatic CSRF handling
const key = newId();                 // persist before sending
await api.submit('WATER', key);      // keep key if delivery is uncertain
const {requests} = await api.requests();
```

Login: `POST /api/v1/session`, JSON `{ "role": "patient", "password": "..." }`,
header `X-EqualCare-Client: dashboard`. Roles are `patient` and `staff`.
The response supplies a CSRF token; send it as `X-CSRF-Token` on every authenticated
write. `GET /session` restores the token after a page refresh. Never put the
device token in frontend code. Staff cannot submit patient requests; patients
cannot acknowledge/resolve them. Errors use `{ "error": { "code": "...", "message": "..." } }`.

### Patient input loop

- Generate a fresh `X-Consumer-ID` per page instance. Never share it across tabs.
- First GET `/input-events` omits `after`; store returned `cursor` and `epoch`.
- Poll `/input-events?after=<cursor>&epoch=<epoch>` every 250 ms, with no overlapping
  polls. Save the returned cursor before acting. Apply NEXT/PREVIOUS to the menu
  and SELECT to its current item. Each event includes the firmware identity fields.
- After a failed poll, page refresh, visibility resume, or network reconnection,
  omit `after` again. Never replay buffered navigation. `reset: true` means accept
  the new cursor without processing old commands.
- A gap caused by buffer overflow or expired navigation also resets to the current
  cursor; SELECT is never delivered after silently losing earlier menu movement.
- Only one consumer lease is allowed; another receives `409 CONSUMER_BUSY`. Stop
  polling in hidden/closed screens. The lease expires after three seconds.
- Firmware must edge-trigger and debounce actions. The keyboard simulator ignores
  held key repeats. The backend additionally coalesces identical active requests.
- Poll requests and device status every second; show stale/disconnected state on
  failure. Device offline is separate from backend offline and request status.

### Request rules

| Type | Label |
| --- | --- |
| WATER | Water |
| TOILET_ASSISTANCE | Toilet assistance |
| REPOSITIONING | Repositioning |
| CALL_NURSE | Call nurse |

POST `/requests` with `{ "type": "WATER", "idempotency_key": "unique-action-id" }`.
Store an uncertain request and its key across refreshes. Retry explicitly with
that same key; do not silently send offline selections later. Only display sent
after the backend confirms success. If the same type is already active, another
key maps to that existing request. Once resolved, a new key creates a new request;
an old key still returns the original. Different types may be active together.

`PENDING -> ACKNOWLEDGED -> RESOLVED` is the only state progression. Nurse actions
are POST `/requests/{id}/acknowledge` and `/requests/{id}/resolve`. Resolve before
acknowledge returns 409. Repeated actions do not regress state. Show active requests
oldest first; separate resolved history. All timestamps are UTC ISO strings;
format them in the browser's local time. There is no nurse-presence or delivery-to-
screen guarantee: PENDING means saved by the backend, not seen by the nurse.

## Firmware handoff (ESP32 owner)

Copy `Code/firmware/config.example.h` to `config.h` and set Wi-Fi credentials,
reserved host address, device ID, and token. Open `firmware.ino` in Arduino IDE
with Espressif's ESP32 Arduino board package. Select the actual development board
after the hardware owner confirms it. No GPIO mapping is assumed by this adapter.

The HTTP adapter uses the ESP32 core's built-in WiFi/HTTPClient and FreeRTOS; no
JSON package or MQTT broker is needed. Its HTTP calls use the
[Espressif HTTPClient interface](https://github.com/espressif/arduino-esp32/blob/master/libraries/HTTPClient/src/HTTPClient.h).
Compile and memory verification on the team's selected board remain required.

Keep one global `EqualCareTransport` instance. Call `begin()` once, then:

```cpp
transport.emit(CareCommand::Next, CareSource::Joystick);
transport.emit(CareCommand::Select, CareSource::Eog);
```

Call emit **once per confirmed/debounced action**, from normal task context, not
an interrupt. Return false means the bounded queue is full or transport unavailable;
discard that action rather than queuing a delayed repeat. Returning true means
queued, not delivered. The network worker runs separately from acquisition, uses
an eight-event queue and one HTTP operation at a time, and retries only within
two seconds of enqueue. It drops expired input. Existing classifier code does not
need Flask, SQLite, timestamps, menu labels, or request-state logic.

POST `/api/v1/input-events`:

```json
{"device_id":"esp32-01","boot_id":"random-per-boot-id","seq":1,"source":"joystick","command":"NEXT"}
```

Headers: `Content-Type: application/json`, `Authorization: Bearer <device-token>`.
Use the identical payload for retries. Sequence increases across both sources,
not independently per source. Start a new random boot ID on each firmware restart.
`source` is `joystick` or `eog`; commands are NEXT/PREVIOUS/SELECT. HTTP 200 with
`accepted:false` is a successfully handled duplicate/out-of-order/retired-boot
event, so stop retrying. Stop and diagnose 4xx responses. Retry network/5xx failures
only while the original event remains fresh. The backend keeps 128 recent events,
expires them after two seconds, and rejects retired boots within its process lifetime.

POST `/api/v1/heartbeat` every five seconds with `{"device_id":"esp32-01"}` using
the same token. The backend marks the device offline after 15 seconds without a
valid input/heartbeat. The adapter needs no clock synchronization. A server restart
clears volatile navigation/dedup history; patient clients resynchronize using a
new server epoch and never replay the old command backlog.

## Simulator and validation

Start the backend and sign in to both dashboards, then in another terminal:

```powershell
& Code/backend/.venv/Scripts/python.exe Code/backend/simulate_device.py
```

The simulator reads the local provisioned token; `EQUALCARE_DEVICE_TOKEN` overrides
it. Use `--server http://<host>:8000` for another host and `--source eog` to test EOG
messages. Enter n/p/s followed by Enter. The patient screen also supports arrow keys
and Enter when focus is outside buttons/inputs. The firmware demo accepts n/p/s
over Serial for **electronics-only bench testing**, before electrodes are attached.

Backend acceptance tests:

```powershell
& Code/backend/.venv/Scripts/python.exe -m pytest Code/backend/tests -q
```

An optional headless browser test is `Code/backend/tests/browser_smoke.cjs`.
It needs the Node `playwright` package available on NODE_PATH and installed
Microsoft Edge; run `node Code/backend/tests/browser_smoke.cjs`. It creates a
temporary loopback Waitress server and separate browser contexts for each role,
and checks lost-response retry, refresh, offline recovery and device navigation.
Set PYTHON if the interpreter is not at `Code/backend/.venv/Scripts/python.exe`.

Test two-laptop operation before hardware: create each request, acknowledge then
resolve, refresh browsers, disconnect/reconnect Wi-Fi, restart the backend, open a
second patient tab, and verify no duplicate/stale selections. Then integrate
joystick, then EOG through the same adapter. Healthy-LAN status feedback target:
within two seconds. This timing must be measured on the actual LAN.

## Remaining physical integration gates

See `WIRING_HANDOFF.md`. Do not infer GPIOs from the generic ESP32 module datasheet.
No hardware was programmed as part of this software scaffold. Board compilation,
GPIO mapping, EOG acquisition/classification, physical debounce, actual Wi-Fi
reliability, and the two-laptop demonstration require the team's hardware.
