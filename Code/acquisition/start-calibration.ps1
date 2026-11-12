$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) { throw 'Run setup.ps1 first.' }
$sessionId = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$calibration = "data/recordings/calibration-$sessionId.csv"
$model = "data/models/cycle-$sessionId.json"

Write-Host 'Do each command as soon as it appears. LEFT/RIGHT: move only your eyes and HOLD until CENTRE.'
Write-Host 'Ctrl+C saves a partial recording. A partial session will not be calibrated.'
& $python -m eog_acquisition session --set calibration --in-plus left --port COM7 --plot --out $calibration
if ($LASTEXITCODE -ne 0) { throw 'Session incomplete. Recording kept; calibration skipped.' }
& $python -m eog_acquisition calibrate $calibration --out $model
if ($LASTEXITCODE -ne 0) { throw 'Calibration failed. Recording kept; review the message above.' }
Write-Host "Model saved: $model"
$answer = Read-Host 'Type RUN to drive the dashboard with this model (backend running, patient signed in), or press Enter to stop'
if ($answer -eq 'RUN') {
    & $python -m eog_acquisition run --model $model --dashboard --plot --port COM7
}
