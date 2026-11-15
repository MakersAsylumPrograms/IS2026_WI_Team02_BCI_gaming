@echo off
rem Left / right / centre calibration over Bluetooth (board on battery, no USB cable).
rem 1. Look test: CENTRE, then LOOK LEFT or LOOK RIGHT (random), 16 looks, then 30 s of free viewing.
rem 2. Recalibrates the eye model on every recording (Chords-Web sequence, look tests, blink tests).
rem Close EqualCare and any phone app or Chords window connected to the board first.
rem Extra look-test options can be added after the file name, e.g.:  Look-Calibration-Bluetooth.cmd --trials 20
setlocal
cd /d "%~dp0Code\acquisition"
if not exist ".venv\Scripts\python.exe" (
  echo The acquisition environment is missing. Run Start-EqualCare.cmd once first.
  pause
  exit /b 1
)
echo Step 1 of 2: look test over Bluetooth...
".venv\Scripts\python.exe" -u -m eog_acquisition look-test --port BLE --in-plus left %*
if errorlevel 1 (
  echo The look test did not finish; the eye model was not changed.
  pause
  exit /b 1
)
echo.
echo Step 2 of 2: recalibrating on all recordings (a few minutes)...
".venv\Scripts\python.exe" -u -m eog_acquisition calibrate-eyes
echo.
echo Restart Start-EqualCare.cmd to use the new calibration.
pause
