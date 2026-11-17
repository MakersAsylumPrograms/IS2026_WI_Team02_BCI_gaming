@echo off
rem Eye calibration without the patient screen: about 2.5 minutes of cued looks and blinks, then a new
rem eye model if it passes its check. Close EqualCare, Chords-Web and the Arduino Serial Monitor first.
rem The board is found on USB, else Bluetooth. Extra options go after the name, e.g.  Calibrate-Eyes.cmd --port COM7
setlocal
cd /d "%~dp0Code\acquisition"
if not exist ".venv\Scripts\python.exe" (
  echo The eye-sensor environment is missing. Run Start-EqualCare.cmd once first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u -m eog_acquisition calibrate-eyes --plot %*
echo.
pause
