# Creates an isolated Python environment and PostgreSQL container; cleans both up.
param([string]$Python = "python", [string]$Docker = "docker")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if ($Docker -eq "docker" -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $Docker = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
}
$Docker = (Get-Command $Docker -ErrorAction Stop).Source
$container = "rag-memory-test-" + [guid]::NewGuid().ToString("N")
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$work = Join-Path $tempRoot $container
$variables = @("PATH", "TEMP", "TMP", "PIP_CACHE_DIR", "POSTGRES_PASSWORD", "MEMORY_TEST_DATABASE_URL", "PYTHONDONTWRITEBYTECODE")
$saved = @{}
foreach ($name in $variables) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
$exitCode = 1
try {
    New-Item -ItemType Directory -Path (Join-Path $work "scratch") | Out-Null
    $env:PATH = (Split-Path -Parent $Docker) + [IO.Path]::PathSeparator + $env:PATH
    $env:TEMP = $env:TMP = Join-Path $work "scratch"
    $env:PIP_CACHE_DIR = Join-Path $work "pip-cache"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    & $Python -B -m venv (Join-Path $work "venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the temporary Python environment." }
    $testPython = Join-Path $work "venv\Scripts\python.exe"
    & $testPython -B -m pip install -q --disable-pip-version-check $repo
    if ($LASTEXITCODE -ne 0) { throw "Could not install the project into the temporary environment." }

    $env:POSTGRES_PASSWORD = [guid]::NewGuid().ToString("N")
    & $Docker run --detach --rm --name $container --env POSTGRES_PASSWORD --env POSTGRES_USER=rag_test --env POSTGRES_DB=rag_memory_test --publish 127.0.0.1::5432 pgvector/pgvector:0.8.1-pg17 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not start the disposable database." }
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        & $Docker exec $container pg_isready -h 127.0.0.1 -U rag_test -d rag_memory_test *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw "PostgreSQL did not become ready." }
    $address = & $Docker port $container 5432/tcp
    if ($LASTEXITCODE -ne 0 -or $address -notmatch '^127\.0\.0\.1:(\d+)$') { throw "Could not read the database port." }
    $env:MEMORY_TEST_DATABASE_URL = "postgresql://rag_test:$($env:POSTGRES_PASSWORD)@127.0.0.1:$($Matches[1])/rag_memory_test"
    & $testPython -B (Join-Path $PSScriptRoot "check_retrieval.py")
    $exitCode = $LASTEXITCODE
} finally {
    try {
        & $Docker rm --force --volumes $container 2>$null | Out-Null
    } finally {
        foreach ($name in $variables) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
        if (Test-Path -LiteralPath $work) {
            $resolvedWork = (Resolve-Path -LiteralPath $work).Path
            $expectedWork = [IO.Path]::GetFullPath((Join-Path $tempRoot $container))
            if ($resolvedWork -ne $expectedWork -or -not $resolvedWork.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $resolvedWork) -ne $container) {
                throw "Refusing to remove an unexpected temporary path."
            }
            Remove-Item -LiteralPath $resolvedWork -Recurse -Force
        }
    }
}
exit $exitCode
