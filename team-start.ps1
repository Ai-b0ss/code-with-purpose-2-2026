param([switch]$Demo, [int]$Port = 8876)
# Local-only team smoke kit. No Git/admin access or stored credentials.
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
$oldPath = $env:Path
$oldMode = $env:GIM_EVALUATION_MODE
$oldExternalPermission = $env:GIM_ALLOW_EXTERNAL_LLM
$oldCoordinatorToken = $env:GIM_COORDINATOR_TOKEN
try {
    Write-Host "GIM / TEAM QA: local site, DeepSeek via ReformBoss"
    Write-Host "Requires Python 3.11+ and internet for dependency installation and AI calls."
    $venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            & py -3 -m venv .venv
        } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
            & python -m venv .venv
        } else {
            throw "Python 3.11+ is missing. Install Python and restart START-TEAM.cmd."
        }
        if ($LASTEXITCODE -ne 0) { throw "Could not create Python virtual environment." }
    }
    $validVersion = & $venvPython -c "import sys; print(int(sys.version_info >= (3,11)))"
    if ($LASTEXITCODE -ne 0 -or $validVersion -ne "1") { throw "Python 3.11+ is required." }
    $requirements = Join-Path $PSScriptRoot "requirements.txt"
    $currentHash = (Get-FileHash -Algorithm SHA256 -Path $requirements).Hash
    $stampPath = Join-Path $PSScriptRoot ".venv\requirements-sha256"
    $oldHash = if (Test-Path $stampPath) { (Get-Content -Raw $stampPath).Trim() } else { "" }
    if ($oldHash -ne $currentHash) {
        Write-Host "Installing/updating Python dependencies into local .venv..."
        & $venvPython -m pip install --disable-pip-version-check -r $requirements
        if ($LASTEXITCODE -ne 0) { throw "pip install failed. Check internet and requirements." }
        Set-Content -Path $stampPath -Value $currentHash -Encoding Ascii
    }
    $env:Path = (Join-Path $PSScriptRoot ".venv\Scripts") + ";" + $oldPath
    Write-Host "Demo/test-only: use fictitious candidates. NO real personal data."
    Write-Host "API binds to 127.0.0.1 only. No external/public access."
    if ($Demo) {
        $env:GIM_EVALUATION_MODE = "demo"
        $env:GIM_ALLOW_EXTERNAL_LLM = "0"
        if (-not $env:GIM_COORDINATOR_TOKEN) {
            $env:GIM_COORDINATOR_TOKEN = & $venvPython -c "import secrets; print(secrets.token_urlsafe(32))"
            Write-Host "Coordinator key for this demo session: $env:GIM_COORDINATOR_TOKEN"
        }
        Write-Host "Demo: http://127.0.0.1:$Port ; coordinator: /admin ; no external AI calls."
        & $venvPython -m uvicorn backend.app:app --host 127.0.0.1 --port $Port
        return
    }
    Write-Host "Key will be typed in console, visible, and kept only in this process environment."
    Write-Host "No model probe is run on startup; first submit performs the live call."
    & "$PSScriptRoot\start-reformboss.ps1" -SkipProbe -Port $Port -TimeoutSeconds 180
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "ReformBoss launcher failed with code $LASTEXITCODE" }
} finally {
    $env:Path = $oldPath
    $env:GIM_EVALUATION_MODE = $oldMode
    $env:GIM_ALLOW_EXTERNAL_LLM = $oldExternalPermission
    $env:GIM_COORDINATOR_TOKEN = $oldCoordinatorToken
    Pop-Location
}

