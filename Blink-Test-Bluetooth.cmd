@echo off
rem Double-blink test over Bluetooth: the board on a battery, no USB cable.
rem The board must run the Bluetooth firmware and not be connected to a phone app or Chords.
rem Extra options can be added after the file name, e.g.:  Blink-Test-Bluetooth.cmd --trials 5
setlocal
cd /d "%~dp0Code\acquisition"
if not exist ".venv\Scripts\python.exe" (
  echo The acquisition environment is missing. Run Start-EqualCare.cmd once first.
  pause
  exit /b 1
)
echo Starting the double-blink test over Bluetooth...
".venv\Scripts\python.exe" -u -m eog_acquisition blink-test --port BLE --in-plus left %*
echo.
pause
