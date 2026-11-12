$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        foreach ($version in @('3.11', '3.12', '3.13')) {
            & py -$version --version *> $null
            if ($LASTEXITCODE -eq 0) {
                & py -$version -m venv .venv
                if ($LASTEXITCODE -eq 0) { break }
            }
        }
    }
    if (-not (Test-Path '.venv/Scripts/python.exe')) {
        throw 'Python 3.11, 3.12, or 3.13 is required. Install one with: py install 3.11. Then rerun this setup script.'
    }
}
& .venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready. Next: .venv\Scripts\python.exe -m eog_acquisition check'
