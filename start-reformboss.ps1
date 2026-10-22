# ReformBoss OpenAI-compatible LLM for GIM. Nothing is saved to disk.
# Default: DeepSeek V4.1 Flash, as returned by authenticated /v1/models.
param(
    [ValidateSet("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4.1-flash")]
    [string]$Model = "deepseek-v4.1-flash",
    [switch]$Compare,
    [switch]$DisableJsonMode,
    [switch]$SkipProbe,
    [ValidateRange(1, 300)]
    [int]$TimeoutSeconds = 300,
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
$previousCoordinatorToken = $env:GIM_COORDINATOR_TOKEN
$previousExternalPermission = $env:GIM_ALLOW_EXTERNAL_LLM
$previousCaFile = $env:SSL_CERT_FILE
try {
    Write-Host "GIM / ReformBoss live LLM"
    Write-Host "Model: $Model"
    Write-Host "Endpoint: https://api.reformboss.com/v1/chat/completions"
    Write-Host "Using a key from the current process or local .env when available."

    # Use the maintained CA bundle when no operator override is configured.
    # Some Windows root stores contain an expired alternate chain. Certificate
    # and hostname verification stay enabled, including during the preflight.
    if (-not $env:SSL_CERT_FILE -and -not $env:SSL_CERT_DIR) {
        $trustedCaFile = python -c "import certifi; print(certifi.where())"
        if ($LASTEXITCODE -ne 0) { throw "Install requirements.txt before starting the live server." }
        $env:SSL_CERT_FILE = $trustedCaFile.Trim()
    }

    # No secret is requested or sent until Python can establish a valid
    # HTTPS connection. HTTP 401 here is normal: the probe has no API key.
    python -m scripts.check_reformboss --check-network
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "TLS/network preflight failed. API key was not requested."
        return
    }

    if (-not $env:GIM_LLM_API_KEY -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".env"))) {
        $env:GIM_LLM_API_KEY = (& python -c "from dotenv import dotenv_values; print(dotenv_values('.env').get('GIM_LLM_API_KEY', ''))").Trim()
    }
    if (-not $env:GIM_LLM_API_KEY) {
        Write-Host "Key will be visible while typing. Do not share a screenshot or terminal recording."
        $env:GIM_LLM_API_KEY = (Read-Host "Enter ReformBoss API key (VISIBLE)").Trim()
    }
    if ([string]::IsNullOrWhiteSpace($env:GIM_LLM_API_KEY)) {
        throw "No key provided."
    }

    $env:GIM_LLM_ENDPOINT = "https://api.reformboss.com/v1/chat/completions"
    $env:GIM_LLM_MODEL = $Model
    $env:GIM_EVALUATION_MODE = "llm"
    $env:GIM_ALLOW_EXTERNAL_LLM = "1"
    if (-not $env:GIM_COORDINATOR_TOKEN) {
        $env:GIM_COORDINATOR_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
        Write-Host "Coordinator key for this local session: $env:GIM_COORDINATOR_TOKEN"
    }
    Write-Host "Synthetic data only. External analysis enabled for this demonstration."
    $env:GIM_LLM_FALLBACK_TO_DEMO = "0"
    $env:GIM_LLM_TIMEOUT = [string]$TimeoutSeconds
    $env:GIM_LLM_JSON_MODE = if ($DisableJsonMode) { "0" } else { "1" }
    # Streaming keeps a long evaluation alive through the provider's edge proxy.
    $env:GIM_LLM_STREAM = "1"
    $env:GIM_LLM_MAX_TOKENS = "4096"

    if (-not $SkipProbe) {
        Write-Host "Checking actual model response with synthetic candidate..."
        $probe = @("-m", "scripts.check_reformboss", "--model", $Model)
        if ($Compare) { $probe += "--compare" }
        if ($DisableJsonMode) { $probe += "--disable-json-mode" }
        python @probe
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Live LLM probe failed. The safe error code above identifies TLS, HTTP, or response format. Server NOT started."
            return
        }
    }
    # Bind-and-release probes avoid accidentally killing an existing server.
    # If the preferred port is occupied, try the next available port.
    $selectedPort = $null
    for ($candidate = $Port; $candidate -le [Math]::Min($Port + 20, 65535); $candidate++) {
        $listener = $null
        try {
            $address = [System.Net.IPAddress]::Parse("127.0.0.1")
            $listener = [System.Net.Sockets.TcpListener]::new($address, $candidate)
            $listener.Start()
            $selectedPort = $candidate
            break
        } catch [System.Net.Sockets.SocketException] {
            continue
        } finally {
            if ($null -ne $listener) { $listener.Stop() }
        }
    }
    if ($null -eq $selectedPort) {
        throw "No free localhost TCP ports found in range $Port-$([Math]::Min($Port + 20, 65535))."
    }
    if ($selectedPort -ne $Port) {
        Write-Warning "Port $Port is busy. Using free port $selectedPort; existing process untouched."
    }
    Write-Host "Live GIM server: http://127.0.0.1:$selectedPort"
    Write-Host "Ctrl+C stops this server. This script clears the key from its process environment on exit."
    $serverEnvArgs = @()
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot ".env")) { $serverEnvArgs = @("--env-file", ".env") }
    python -m uvicorn backend.app:app --host 127.0.0.1 --port $selectedPort @serverEnvArgs
} finally {
    if ($previousCaFile) { $env:SSL_CERT_FILE = $previousCaFile } else { Remove-Item Env:SSL_CERT_FILE -ErrorAction SilentlyContinue }
    if ($previousExternalPermission) { $env:GIM_ALLOW_EXTERNAL_LLM = $previousExternalPermission } else { Remove-Item Env:GIM_ALLOW_EXTERNAL_LLM -ErrorAction SilentlyContinue }
    if ($previousCoordinatorToken) { $env:GIM_COORDINATOR_TOKEN = $previousCoordinatorToken } else { Remove-Item Env:GIM_COORDINATOR_TOKEN -ErrorAction SilentlyContinue }
    Remove-Item Env:GIM_LLM_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_ENDPOINT -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_EVALUATION_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_FALLBACK_TO_DEMO -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_TIMEOUT -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_JSON_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_STREAM -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_MAX_TOKENS -ErrorAction SilentlyContinue
    Pop-Location
}

