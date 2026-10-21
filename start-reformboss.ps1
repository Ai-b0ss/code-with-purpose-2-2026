# ReformBoss OpenAI-compatible LLM for GIM. Nothing is saved to disk.
# Default: DeepSeek V4.1 Flash 1017, as displayed in the user's ReformBoss menu.
param(
    [ValidateSet("deepseek-v4.1-flash-1017", "glm-5.3", "kimi-k3")]
    [string]$Model = "deepseek-v4.1-flash-1017",
    [switch]$Compare,
    [switch]$DisableJsonMode,
    [switch]$SkipProbe
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    Write-Host "GIM / ReformBoss live LLM"
    Write-Host "Model: $Model"
    Write-Host "Endpoint: https://api.reformboss.com/v1/chat/completions"
    Write-Host "No key will be printed, written to disk or committed."

    # Secure local terminal input; only the current PowerShell process and its
    # child uvicorn process receive the temporary environment variable.
    $secret = Read-Host "Enter ReformBoss API key (hidden)" -AsSecureString
    $ptr = [IntPtr]::Zero
    try {
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        $env:GIM_LLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
        $secret.Dispose()
    }
    if ([string]::IsNullOrWhiteSpace($env:GIM_LLM_API_KEY)) {
        throw "No key provided."
    }

    $env:GIM_LLM_ENDPOINT = "https://api.reformboss.com/v1/chat/completions"
    $env:GIM_LLM_MODEL = $Model
    $env:GIM_EVALUATION_MODE = "llm"
    $env:GIM_LLM_FALLBACK_TO_DEMO = "0"
    $env:GIM_LLM_TIMEOUT = "60"
    $env:GIM_LLM_JSON_MODE = if ($DisableJsonMode) { "0" } else { "1" }

    if (-not $SkipProbe) {
        Write-Host "Checking actual model response with synthetic candidate..."
        $probe = @("-m", "scripts.check_reformboss", "--model", $Model)
        if ($Compare) { $probe += "--compare" }
        if ($DisableJsonMode) { $probe += "--disable-json-mode" }
        python @probe
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Live LLM probe failed. Server NOT started; check model ID, API allowance or try -DisableJsonMode."
            return
        }
    }
    Write-Host "Live GIM server: http://127.0.0.1:8765"
    Write-Host "Ctrl+C stops this server and clears its API key from this PowerShell session."
    python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
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
