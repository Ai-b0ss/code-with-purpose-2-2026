# Runs a private/local copy of GIM with a freshly provided ReformBoss key.
# Requires Python 3.11+; dependencies installed into .venv on FIRST startup.
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
$oldPath = $env:Path
try {
    Write-Host "GIM: portable QA for Ilya. Python 3.11+ required."
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            & py -3 -m venv .venv
        } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
            & python -m venv .venv
        } else {
            throw "Python is missing. Install Python 3.11+ and restart."
        }
        if ($LASTEXITCODE -ne 0) { throw "Failed to create local Python venv." }
    }
    $versionOk = & $venvPython -c "import sys;print(int(sys.version_info >= (3,11)))"
    if ($versionOk -ne "1") { throw "Python 3.11+ required." }
    $ready = Join-Path $PSScriptRoot ".venv\deps-ready"
    if (-not (Test-Path $ready)) {
        Write-Host "Installing dependencies into local .venv (first run needs Internet)..."
        & $venvPython -m pip install --disable-pip-version-check -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
        Set-Content -Path $ready -Value "requirements.txt installed" -Encoding Ascii
    }
    $env:Path = (Join-Path $PSScriptRoot ".venv\Scripts") + ";" + $oldPath
    Write-Host "Local-only. The server is not exposed to other PCs or the public Internet."
    Write-Host "DeepSeek is used by the backend when a volunteer SUBMITS a form."
    Write-Host "Candidate sees confirmation. Curator report is a separate local file."
    Write-Host "Do NOT publish the report or any personal information."
    & "$PSScriptRoot\start-reformboss.ps1" -SkipProbe -Port 8876 -TimeoutSeconds 180
} finally {
    $env:Path = $oldPath
    Pop-Location
}
