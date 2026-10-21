param([switch]$SkipProbe)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    . (Join-Path $PSScriptRoot 'console-utf8.ps1')
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Run: py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt' }
    if (-not (Test-Path -LiteralPath '.env')) { throw 'Copy .env.example to .env and set GIM_LLM_API_KEY or OPENAI_API_KEY.' }

    # Keep Docker credentials and resources separate from other local projects.
    $dockerConfig = Join-Path $env:TEMP 'gim-graphrag-docker-config'
    New-Item -ItemType Directory -Path $dockerConfig -Force | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $dockerConfig 'config.json'))) {
        Set-Content -LiteralPath (Join-Path $dockerConfig 'config.json') -Value '{}' -Encoding ascii
    }
    $env:DOCKER_CONFIG = $dockerConfig
    docker compose -f compose.graphrag.yaml up -d
    if ($LASTEXITCODE -ne 0) { throw 'Could not start isolated Neo4j.' }
    $setLocalDatabaseUrl = -not $env:GIM_DATABASE_URL
    if ($setLocalDatabaseUrl) {
        $env:GIM_DATABASE_URL = 'postgresql://gim:gim-local-db-2026@127.0.0.1:15432/gim'
    }
    $health = ''
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $health = docker inspect --format '{{.State.Health.Status}}' gim-graphrag-neo4j
        if ($health -eq 'healthy') { break }
        Start-Sleep -Seconds 2
    }
    if ($health -ne 'healthy') { throw 'Neo4j did not become healthy.' }
    $pgHealth = ''
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $pgHealth = docker inspect --format '{{.State.Health.Status}}' gim-graphrag-postgres
        if ($pgHealth -eq 'healthy') { break }
        Start-Sleep -Seconds 2
    }
    if ($pgHealth -ne 'healthy') { throw 'PostgreSQL did not become healthy.' }
    docker cp knowledge/seed.cypher gim-graphrag-neo4j:/tmp/gim-seed.cypher
    if ($LASTEXITCODE -ne 0) { throw 'Could not copy graph seed.' }
    docker exec gim-graphrag-neo4j cypher-shell -u neo4j -p gim-local-graph-2026 -f /tmp/gim-seed.cypher
    if ($LASTEXITCODE -ne 0) { throw 'Could not import graph seed.' }
    & $python -c "from backend.historical import import_historical; print('Historical split and import:', import_historical())"
    if ($LASTEXITCODE -ne 0) { throw 'Historical corpus preparation failed.' }

    & $python -c "import os; from backend.knowledge import embed_facts; key=os.getenv('OPENAI_API_KEY',''); print('Embedded facts:', embed_facts()) if key and not key.startswith('replace-') else print('Neo4j full-text GraphRAG enabled; OpenAI embeddings not configured.')"
    if ($LASTEXITCODE -ne 0) { throw 'Graph embedding failed. Check OPENAI_API_KEY and Neo4j.' }
    $env:GIM_EVALUATION_MODE = 'rag_llm'
    if (-not $SkipProbe) {
        & $python -m scripts.probe_scoring
        if ($LASTEXITCODE -ne 0) { throw 'Live scoring probe failed.' }
    }
    Write-Host 'Site: http://127.0.0.1:8765'
    Write-Host 'Neo4j Browser: http://127.0.0.1:17474'
    & $python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
} finally {
    Remove-Item Env:GIM_EVALUATION_MODE -ErrorAction SilentlyContinue
    if ($setLocalDatabaseUrl) { Remove-Item Env:GIM_DATABASE_URL -ErrorAction SilentlyContinue }
    Pop-Location
}
