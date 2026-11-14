@echo off
rem Double-blink test: asks for a double blink 10 times and says if each one was caught.
rem Close EqualCare, Chords-Web and the Arduino Serial Monitor first (they hold COM7).
rem Extra options can be added after the file name, e.g.:  Blink-Test.cmd --trials 5 --window 3
setlocal
cd /d "%~dp0Code\acquisition"
if not exist ".venv\Scripts\python.exe" (
  echo The acquisition environment is missing. Run Start-EqualCare.cmd once first.
  pause
  exit /b 1
)
echo Starting the double-blink test...
".venv\Scripts\python.exe" -u -m eog_acquisition blink-test --in-plus left %*
echo.
pause
