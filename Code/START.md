# EqualCare start sequence

Quick checklist for demo day. Details and troubleshooting are in [README.md](README.md).

## 1. Patient laptop: start the backend

PowerShell, from the repo root:

```powershell
& ./Code/backend/start.ps1
```

This creates `.venv` if needed, installs `requirements.txt`, and serves on port 8000.
Leave this window open. Wait for:

```text
EqualCare: http://localhost:8000 | Patient: /patient/ | Nurse: /staff/
```

## 2. Get the passwords

They are printed on the very first run only. To print them again (second PowerShell window):

```powershell
& Code/backend/.venv/Scripts/python.exe Code/backend/run.py --show-credentials
```

Never commit `Code/backend/instance/credentials.json`.

## 3. Check the server is up

Open `http://localhost:8000/api/v1/health`. It should show `{"status":"ok"}`.

## 4. Patient screen (patient laptop)

1. Open `http://localhost:8000/patient/`.
2. Enter the **patient password** once.
3. Arrow keys / Enter / Esc, or the ESP32, drive the menu.

## 5. Staff screen

**Second laptop (real demo):**

1. Connect both laptops (and the ESP32) to the same hotspot.
2. On the patient laptop, run `ipconfig` and note the Wi-Fi IPv4 address.
3. First time only, allow port 8000 (PowerShell as Administrator):

   ```powershell
   New-NetFirewallRule -DisplayName "EqualCare 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```

4. On the staff laptop, check `http://<IP>:8000/api/v1/health`, then open `http://<IP>:8000/staff/`.

**Same laptop (testing):** open `http://127.0.0.1:8000/staff/`. Use `127.0.0.1` here,
not `localhost`, so the patient and staff sign-ins don't share cookies.

Then:

1. Enter the **staff password**.
2. Click **"Click to enable sound alerts"**.

## 6. Input device

- **ESP32:** set `EC_SERVER` in the firmware `config.h` to `http://<IP>:8000` and power it on.
- **No hardware:** simulate it in another PowerShell window:

  ```powershell
  & Code/backend/.venv/Scripts/python.exe Code/backend/simulate_device.py
  ```

  Type `n`, `p` or `s` and press Enter (NEXT / PREVIOUS / SELECT).

## 7. Smoke test

On the patient screen choose a request → **Confirm**. It should appear on the
staff screen with a sound. Mark it **Seen** (`S`), then **Done** (`D`).

## Stop

Press `Ctrl+C` in the backend window.
