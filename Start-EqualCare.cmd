@echo off
setlocal
cd /d "%~dp0"
for %%V in (3.11 3.12 3.13) do (
  py -%%V --version >nul 2>&1
  if not errorlevel 1 (
    py -%%V Code\launcher.py
    if errorlevel 1 pause
    exit /b
  )
)
echo Install Python with: py install 3.11
pause
exit /b 1
