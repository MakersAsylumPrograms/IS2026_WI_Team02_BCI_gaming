# EqualCare code

| Folder | What it is |
| --- | --- |
| `backend/` | Flask + SQLite server. Serves the API **and** both screens. |
| `frontend/` | Patient screen, staff screen, landing page. Plain HTML/CSS/JS, no build step. |
| `firmware/` | ESP32 sketch and HTTP transport (joystick now, EOG later). |

Full API contract: [`../Documentation/openapi.json`](../Documentation/openapi.json).
Backend, network and firmware details: [`../Documentation/INTEGRATION.md`](../Documentation/INTEGRATION.md).

## Run the demo

Short demo-day checklist: [`START.md`](START.md).

1. **Start the backend on the patient laptop** (PowerShell, from the repo root):

   ```powershell
   & ./Code/backend/start.ps1
   ```

   The first run prints the patient password, staff password and device token.
   Print them again with
   `& Code/backend/.venv/Scripts/python.exe Code/backend/run.py --show-credentials`.

2. **Patient screen** (same laptop): open `http://localhost:8000/patient/`.
   A caregiver types the patient password once. Refreshing stays signed in.

3. **Staff screen** (second laptop): open `http://<patient-laptop-IP>:8000/staff/`,
   enter the staff password, then click **"Click to enable sound alerts"**.
   Browsers block sound until you click the page once.

4. *(Optional, no hardware)* Pretend to be the ESP32:

   ```powershell
   & Code/backend/.venv/Scripts/python.exe Code/backend/simulate_device.py
   ```

   Type `n`, `p` or `s` and press Enter (NEXT / PREVIOUS / SELECT).

**Testing both screens on ONE laptop:** sign-in cookies are shared per
address, so open the patient screen at `http://localhost:8000/patient/` and
the staff screen at `http://127.0.0.1:8000/staff/`. You can also use a
private window for one of them.

## Two laptops on a phone hotspot (setting the API address)

You don't need to configure any URL. The backend serves the pages and the
API from the same address, so whatever you type in the browser is the
backend. The setting exists in one place, `API_BASE` in
[`frontend/shared/api.js`](frontend/shared/api.js), and should stay
`'/api/v1'`. The backend deliberately has no CORS, so pointing the frontend
at another machine would be blocked by the browser.

1. Connect both laptops (and later the ESP32) to the same hotspot.
2. On the patient laptop run `ipconfig` and note the **Wi-Fi IPv4 address**,
   e.g. `192.168.43.20`.
3. Allow port 8000 through Windows Firewall on the patient laptop
   (PowerShell as Administrator):

   ```powershell
   New-NetFirewallRule -DisplayName "EqualCare 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```

   If Windows labels the hotspot as a *Public* network, either switch it to
   Private in Wi-Fi settings or add `-Profile Public` for the demo.
4. On the staff laptop, first check `http://<IP>:8000/api/v1/health`.
   It should show `{"status":"ok"}`. Then open `http://<IP>:8000/staff/`.
5. If the hotspot's IP changes, just reopen the staff page with the new IP
   (and update `EC_SERVER` in the ESP32 `config.h`).

## Controls

**Patient screen**: every input becomes one of four commands.

| Input | Command |
| --- | --- |
| → or ↓ | NEXT |
| ← or ↑ | PREVIOUS |
| Enter or Space | SELECT |
| Esc | BACK |
| Mouse click on an option | highlight it + SELECT |
| ESP32 joystick / EOG | NEXT / PREVIOUS / SELECT |

- The whole flow works with **only NEXT + SELECT** (EOG may give us just two
  gestures). "Cancel" and "Back to menu" are real on-screen options.
- A held key counts once. Two SELECTs within 0.7 s count as one.
- Flow: menu → "Water? Confirm / Cancel" → Request sent ✓ → Help is coming ✓
  → Done ✓ → back to the menu after 5 s. The confirm screen starts on
  **Cancel**, so an accidental double SELECT can't send an alert.
- If the backend doesn't answer, the screen says "Not sure it was sent" and
  offers **Try again**. That reuses the same request ID, so it can never
  create a duplicate.

**Staff screen**: mouse on the **Seen** / **Done** buttons, or the keyboard:
`S` marks the top waiting request Seen and `D` marks the top seen request Done.

## Where the ESP32 plugs in

The ESP32 never talks to the browser directly:

```text
ESP32 --POST /api/v1/input-events--> backend --polled every 250 ms--> patient browser
```

- Browser side: **`fromDevice()` in [`frontend/shared/input.js`](frontend/shared/input.js)**,
  marked `ESP32 HOOK`. `startDevicePolling()` in the same file fetches the
  events and passes each one to `fromDevice()`. Hardware commands then follow
  exactly the same path as the keyboard.
- Firmware side: call `transport.emit(CareCommand::Next, CareSource::Joystick)`
  (or `CareSource::Eog`) once per gesture. See `firmware/firmware.ino`.
- Switching from joystick to EOG needs **no frontend change**.

## Frontend files (`frontend/shared/`, served as `/assets/…`)

| File | Job |
| --- | --- |
| `api.js` | The only file that calls the backend. `API_BASE`, request type labels. |
| `input.js` | Keyboard / mouse / ESP32 → NEXT, PREVIOUS, SELECT, BACK. |
| `common.js` | Sign-in, polling helper, "Connected / Reconnecting" banner. |
| `patient.js` | Patient screen state machine (menu, confirm, status, …). |
| `staff.js` | Staff dashboard: sorting, timers, Seen/Done, alerts. |
| `styles.css` | All styling. Colours are at the top. |

Settings you might want to tweak are constants at the top of each file:
`CONFIRM_STARTS_ON_CANCEL` and `RETURN_TO_MENU_AFTER_MS` (patient.js),
`SELECT_DEBOUNCE_MS` (input.js), and `PATIENT_LABEL` and `OVERDUE_AFTER_MS`
(staff.js).

## Tests

```powershell
& Code/backend/.venv/Scripts/python.exe -m pytest Code/backend/tests -q
```

The browser end-to-end test (needs Microsoft Edge and the Node `playwright`
package on `NODE_PATH`) is `node Code/backend/tests/browser_smoke.cjs`.
