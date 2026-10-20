# Install the application runtime and prepare the local database/model services.
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$runtime = Join-Path $PSScriptRoot ".local\runtime"
$runtimePython = Join-Path $runtime "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $Python -m venv $runtime
    if ($LASTEXITCODE -ne 0) { throw "Could not create the application runtime." }
}
& $runtimePython -m pip install -q --disable-pip-version-check -e $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw "Could not install the application." }
$application = Join-Path $runtime "Scripts\rag-mcp.exe"
$config = Join-Path $PSScriptRoot ".env"
& $application --config $config setup
if ($LASTEXITCODE -ne 0) { throw "Local service setup failed." }
Write-Output "MCP command: $application"
Write-Output "MCP env RAG_CONFIG: $config"
