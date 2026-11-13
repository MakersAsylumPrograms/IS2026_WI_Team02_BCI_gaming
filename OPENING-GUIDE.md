# Opening EqualCare: step by step

How to get the project running on a Windows laptop, from nothing to the patient screen
moving when you look left and right. Steps 1–4 are once per laptop; after that, step 5 is all
you need.

## What you need

| Item | Notes |
| --- | --- |
| Windows laptop | This becomes the **patient laptop**: it runs everything |
| Python 3.11, 3.12 or 3.13 | From python.org, or `py install 3.11` in PowerShell |
| Node.js 20 or newer | From nodejs.org (builds the screens) |
| Internet | First start only, to install packages |
| ESP32-S3 board with the BioAmp EXG Pill and joystick | Optional: keyboard and mouse work without it |
| Arduino IDE | Only to flash the board (step 4) |
| A second laptop or phone | Optional: the nurse dashboard |

## 1. Get the code

```powershell
git clone https://github.com/MakersAsylumPrograms/IS2026_WI_Team02_BCI_gaming.git
cd IS2026_WI_Team02_BCI_gaming
```

Already have it? Run `git pull` instead. (GitHub Desktop or "Download ZIP" also work.)

## 2. Check Python and Node

In PowerShell:

```powershell
py --list        # should list 3.11, 3.12 or 3.13
node --version   # should print v20 or higher
```

If either is missing, install it and open a **new** PowerShell window.

## 3. First start

Double-click **`Start-EqualCare.cmd`** in the project folder.

The first time it takes a few minutes. It:

1. creates two Python environments and installs their packages,
2. installs and builds the screens,
3. starts the server and opens **http://127.0.0.1:8000/patient/** in your browser.

**Write down the passwords it prints the first time.** They are shown only once. The staff
login ID is `staff`. If they are lost, set new ones (close the launcher first):

```powershell
Code\backend\.venv\Scripts\python.exe Code\backend\run.py --set-password patient
Code\backend\.venv\Scripts\python.exe Code\backend\run.py --set-password staff
```

Keep the launcher window open while you use EqualCare. Closing it (or Ctrl+C) stops everything.

No board connected? The launcher will report that it cannot open COM7 and stop. To use the
screens without the board, start only the server instead:

```powershell
Code\backend\.venv\Scripts\python.exe Code\backend\run.py
```

## 4. Connect the board (once per board)

1. Open `Code/firmware/ESP32-S3/ESP32-S3.ino` in the Arduino IDE.
2. Tools settings: board **ESP32S3 Dev Module**, USB CDC On Boot **Disabled**, Flash Size
   **16MB**, PSRAM **OPI PSRAM**, Port **COM7** (the board's **UART** USB connector).
3. Upload, then **close the Serial Monitor**. Only one program can use COM7, and that includes
   Chords-Web: close it too.
4. Wiring: Pill OUT → GPIO 1; joystick VRx/VRy/SW → GPIO 4/5/6; everything on **3V3**, never 5 V.

If the board shows up as a different COM port, copy `Code/launcher-config.example.json` to
`Code/launcher-config.json` and change `"port"`.

**Safety:** with electrodes on a person, run the laptop on battery with the charger unplugged.

## 5. Every time after that

1. Electrodes on: **IN+ outer corner of the left eye, IN− outer corner of the right eye,
   REF behind the right ear.**
2. Plug in the board, double-click `Start-EqualCare.cmd`.
3. Sign in on the patient screen (the browser remembers it after that).
4. Wait for the plot window and 3 seconds of warm-up, then:

| Do this | The screen does |
| --- | --- |
| Look **left** and hold half a second | Highlight moves back (PREVIOUS) |
| Look **right** and hold | Highlight moves on (NEXT) |
| **Blink twice**, about half a second apart | Selects the highlighted option |
| Joystick left/up, right/down, press | The same three things |
| Keyboard ← →, Enter, Esc | The same, plus Esc = back |

Before relying on the eyes, do the two-minute check in
[Code/acquisition/README.md](Code/acquisition/README.md) (section 5). If looking left moves
the highlight the wrong way, IN+ and IN− are swapped.

## 6. The nurse dashboard

- On the same laptop: open **http://127.0.0.1:8000/staff/** in another tab.
- On another laptop or phone on the same Wi-Fi:
  1. On the patient laptop run `ipconfig` and note the Wi-Fi **IPv4 address**, e.g. 192.168.1.23.
  2. Allow the server through Windows Firewall when Windows asks (Private networks).
  3. On the nurse device open **http://192.168.1.23:8000/staff/** (your address).

Sign in with login ID `staff` and the staff password. New requests appear with a banner;
press **Seen**, then **Done**.

## Something wrong?

| Problem | Fix |
| --- | --- |
| "Port 8000 is already in use" | An older server is still running. Close its window, or in PowerShell: `Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess` |
| "Cannot open COM7" | Board unplugged, wrong port, or Arduino Serial Monitor / Chords-Web still open |
| "Node.js is not installed" | Install Node.js 20+, open a new window, start again |
| Page says "screens are not built yet" | Start with `Start-EqualCare.cmd`, or run `npm install` and `npm run build` in `Code\frontend` |
| "ESP32 controller not connected" on the patient screen | The board or launcher isn't running; keyboard and mouse still work |
| Eyes do nothing | Check the plot: raw signal should sit near 1,800–2,000. Re-seat electrodes; see the two-minute check |
| Nurse device can't open the page | Same Wi-Fi? Right IP? Firewall allowed? Some hotspots block device-to-device traffic |
| Forgot a password | `run.py --set-password patient` / `staff` (step 3) |

## Where to read more

- [Code/README.md](Code/README.md) — passwords, network, API and file map
- [Code/acquisition/README.md](Code/acquisition/README.md) — sensor, eye calibration, joystick
- [Code/frontend/README.md](Code/frontend/README.md) — the screens and how to change them
