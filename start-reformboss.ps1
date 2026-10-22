# ReformBoss OpenAI-compatible LLM for GIM. Nothing is saved to disk.
# Model names and endpoint are read from knowledge/provider_models.json.
param(
    [string]$Model = "",
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
    $catalog = Get-Content -LiteralPath (Join-Path $PSScriptRoot "knowledge/provider_models.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    $endpoint = [string]$catalog._default_endpoint
    $providerHost = ([uri]$endpoint).Host
    $modelProperty = $catalog.PSObject.Properties[$providerHost]
    if ($null -eq $modelProperty) { throw "No model catalog for configured endpoint host." }
    $availableModels = @($modelProperty.Value)
    if ([string]::IsNullOrWhiteSpace($Model)) { $Model = [string]$catalog._default_model }
    if ($Model -notin $availableModels) { throw "Requested model is absent from knowledge/provider_models.json." }
    Write-Host "GIM / ReformBoss live LLM"
    Write-Host "Model: $Model"
    Write-Host "Endpoint: $endpoint"
    Write-Host "Key will be VISIBLE while typing. Do not share a screenshot or terminal recording."

    # HTTP 401 here is normal: the network probe has no API key.
    python -m scripts.check_reformboss --check-network
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Cloud network preflight failed. Configured local fallback can still be used."
    }

    # Plain Read-Host intentionally avoids SecureString conversion because the
    # same key successfully authenticated via direct Invoke-RestMethod.
    # The value appears on screen while typing; it is NOT persisted to a file.
    # The variable is cleared when this PowerShell process exits.
    $env:GIM_LLM_API_KEY = (Read-Host "Enter ReformBoss API key (VISIBLE)").Trim()
    if ([string]::IsNullOrWhiteSpace($env:GIM_LLM_API_KEY)) {
        throw "No key provided."
    }

    $env:GIM_LLM_ENDPOINT = $endpoint
    $env:GIM_LLM_MODEL = $Model
    $env:GIM_LLM_BACKUP_MODELS = ($availableModels | Where-Object { $_ -ne $Model }) -join ","
    $env:GIM_EVALUATION_MODE = "llm"
    $env:GIM_LLM_FALLBACK_TO_DEMO = "0"
    $env:GIM_LLM_TIMEOUT = [string]$TimeoutSeconds
    $env:GIM_LLM_JSON_MODE = if ($DisableJsonMode) { "0" } else { "1" }

    if (-not $SkipProbe) {
        Write-Host "Checking production harness with a synthetic candidate and model failover..."
        python -m scripts.probe_live_harness
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Live harness probe failed. The safe error code above identifies the failure. Server NOT started."
            return
        }
        if ($Compare) {
            $comparison = @("-m", "scripts.check_reformboss", "--compare")
            if ($DisableJsonMode) { $comparison += "--disable-json-mode" }
            python @comparison
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
    Remove-Item Env:GIM_LLM_BACKUP_MODELS -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_EVALUATION_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_FALLBACK_TO_DEMO -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_TIMEOUT -ErrorAction SilentlyContinue
    Remove-Item Env:GIM_LLM_JSON_MODE -ErrorAction SilentlyContinue
    Pop-Location
}
