$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) { throw 'Run setup.ps1 first.' }
$sessionId = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$calibration = "data/recordings/calibration-$sessionId.csv"
$model = "data/models/cycle-$sessionId.json"

Write-Host 'Do each command as soon as it appears. LEFT/RIGHT: move only your eyes and HOLD until CENTRE.'
Write-Host 'Use comfortable side looks, not extreme looks or hard squeezes. Side holds last 3.5 seconds.'
Write-Host 'Check the raw plot first: if it hits 0/4095, correct the signal before recording.'
Write-Host 'Ctrl+C saves a partial recording. A partial session will not be calibrated.'
& $python -m eog_acquisition session --set calibration --in-plus left --port COM7 --plot --out $calibration
if ($LASTEXITCODE -ne 0) { throw 'Session incomplete. Recording kept; calibration skipped.' }
& $python -m eog_acquisition calibrate $calibration --out $model
if ($LASTEXITCODE -ne 0) { throw 'Calibration failed. Recording kept; review the message above.' }
Write-Host "Model saved: $model"
$evaluation = "data/recordings/evaluation-$sessionId.csv"
Write-Host 'Take a five-minute break, keeping electrodes in place.'
$answer = Read-Host 'Type EVALUATE for a separate evaluation recording, or press Enter to finish'
if ($answer -ne 'EVALUATE') { return }
& $python -m eog_acquisition session --set evaluation --in-plus left --port COM7 --plot --out $evaluation
if ($LASTEXITCODE -ne 0) { throw 'Evaluation incomplete. Recording kept; model remains unapproved.' }
& $python -m eog_acquisition evaluate $evaluation --model $model --apply
if ($LASTEXITCODE -ne 0) { throw 'Evaluation failed. Review the output above.' }
$configPath = Join-Path $PSScriptRoot '../launcher-config.json'
$config = if (Test-Path $configPath) { Get-Content $configPath -Raw | ConvertFrom-Json } else { [pscustomobject]@{} }
$config | Add-Member -NotePropertyName model -NotePropertyValue "Code/acquisition/$model" -Force
$config | ConvertTo-Json | Set-Content $configPath -Encoding UTF8
Write-Host 'Model selected for the one-click launcher. EOG still requires passing evaluation.'
$answer = Read-Host 'Type RUN for the dashboard (unapproved EOG stays disabled), or press Enter to stop'
if ($answer -eq 'RUN') {
    & $python -m eog_acquisition run --model $model --dashboard --plot --port COM7
}
