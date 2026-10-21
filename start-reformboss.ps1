# ReformBoss OpenAI-compatible LLM for GIM. Nothing is saved to disk.
# Default: DeepSeek V4.1 Flash, as returned by authenticated /v1/models.
param(
    [ValidateSet("deepseek-v4.1-flash", "glm-5.3", "kimi-k3")]
    [string]$Model = "deepseek-v4.1-flash",
    [switch]$Compare,
    [switch]$DisableJsonMode,
    [switch]$SkipProbe,
    [ValidateRange(1, 300)]
    [int]$TimeoutSeconds = 180,
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    Write-Host "GIM / ReformBoss live LLM"
    Write-Host "Model: $Model"
    Write-Host "Endpoint: https://api.reformboss.com/v1/chat/completions"
    Write-Host "Key will be VISIBLE while typing. Do not share a screenshot or terminal recording."

    # No secret is requested or sent until Python can establish a valid
    # HTTPS connection. HTTP 401 here is normal: the probe has no API key.
    python -m scripts.check_reformboss --check-network
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "TLS/network preflight failed. API key was not requested."
        return
    }

    # Plain Read-Host intentionally avoids SecureString conversion because the
    # same key successfully authenticated via direct Invoke-RestMethod.
    # The value appears on screen while typing; it is NOT persisted to a file.
    # The variable is cleared when this PowerShell process exits.
    $env:GIM_LLM_API_KEY = (Read-Host "Enter ReformBoss API key (VISIBLE)").Trim()
    if ([string]::IsNullOrWhiteSpace($env:GIM_LLM_API_KEY)) {
        throw "No key provided."
    }

    $env:GIM_LLM_ENDPOINT = "https://api.reformboss.com/v1/chat/completions"
    $env:GIM_LLM_MODEL = $Model
    $env:GIM_EVALUATION_MODE = "llm"
    $env:GIM_LLM_FALLBACK_TO_DEMO = "0"
    $env:GIM_LLM_TIMEOUT = [string]$TimeoutSeconds
    $env:GIM_LLM_JSON_MODE = if ($DisableJsonMode) { "0" } else { "1" }

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
    Remove-Item Env:GIM_LLM_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_ENDPOINT -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_MODEL -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_EVALUATION_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_FALLBACK_TO_DEMO -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_TIMEOUT -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_JSON_MODE -ErrorAction SilentlyContinue
    Pop-Location
}
