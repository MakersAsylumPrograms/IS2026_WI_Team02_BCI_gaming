$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) { throw 'Run setup.ps1 first.' }
$sessionId = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$calibration = "data/recordings/calibration-$sessionId.csv"
$evaluation = "data/recordings/evaluation-$sessionId.csv"
$model = "data/models/model-$sessionId.json"

Write-Host 'Follow each prompt. Let the session finish; it saves automatically.'
Write-Host 'Ctrl+C saves a partial recording. A partial session will not be calibrated.'
& $python -m eog_acquisition session --set calibration --in-plus left --port COM7 --out $calibration
if ($LASTEXITCODE -ne 0) { throw 'Session incomplete. Recording kept; calibration skipped.' }
& $python -m eog_acquisition calibrate $calibration --out $model
if ($LASTEXITCODE -ne 0) { throw 'Calibration failed. Recording kept; review the warnings above.' }
Write-Host "Model saved: $model"
Write-Host 'Take a five-minute break. Keep electrodes in place.'
$answer = Read-Host 'Type EVALUATE when ready for a separate evaluation session, or press Enter to stop'
if ($answer -ne 'EVALUATE') { return }
& $python -m eog_acquisition session --set evaluation --in-plus left --port COM7 --out $evaluation
if ($LASTEXITCODE -ne 0) { throw 'Evaluation incomplete. Recording kept; model remains disabled.' }
& $python -m eog_acquisition evaluate $evaluation --model $model --apply
if ($LASTEXITCODE -ne 0) { throw 'Evaluation failed; review the output above.' }
$answer = Read-Host 'Type RUN to try the enabled gestures locally, or press Enter to finish'
if ($answer -eq 'RUN') {
    & $python -m eog_acquisition run --model $model --plot --port COM7
}
