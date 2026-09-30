param(
    [string] $Python = "python"
)

# Use the integration's own client and public application configuration.
# The Python probe reads credentials interactively and creates a disposable list.
$ErrorActionPreference = "Stop"
$probeExitCode = 1
Push-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try {
    & $Python -m scripts.live_api_probe
    $probeExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $probeExitCode
