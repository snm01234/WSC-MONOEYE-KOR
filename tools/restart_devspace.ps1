[CmdletBinding()]
param(
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = 'D:\monoeye'
$Hostname = 'desktop-n33c4n7.tail76b4aa.ts.net'
$SetupScript = 'C:\Users\Administrator\.codex\skills\chatgpt-workspace-setup\scripts\devspace_tailscale_setup.py'

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "Project root not found: $ProjectRoot"
}

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $SetupScript -PathType Leaf)) {
    throw "DevSpace setup script not found: $SetupScript"
}

$Python = (Get-Command python -ErrorAction Stop).Source
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    throw 'tailscale command not found. Install Tailscale or check PATH.'
}

function Invoke-DevSpaceSetup {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Python $SetupScript @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "DevSpace setup command failed. exit code=$LASTEXITCODE"
    }
}

Write-Host 'Tailscale Funnel status:'
& tailscale funnel status
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to read Tailscale Funnel status.'
}

if (-not $Apply) {
    Write-Host "`nRunning read-only diagnosis for $ProjectRoot"
    Invoke-DevSpaceSetup @(
        'doctor',
        '--root', $ProjectRoot,
        '--hostname', $Hostname
    )

    Write-Host "`nTo restart DevSpace, run:"
    Write-Host "powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Apply"
    exit 0
}

Write-Host "`nShowing the setup plan before applying changes."
Invoke-DevSpaceSetup @(
    'setup',
    '--root', $ProjectRoot,
    '--hostname', $Hostname,
    '--dry-run'
)

$confirmation = Read-Host 'If the plan is correct, enter Y to restart DevSpace and configure Funnel'
if ($confirmation -notmatch '^(Y|y|Yes|yes)$') {
    Write-Host 'Cancelled. No changes were made.'
    exit 0
}

Write-Host "`nInitializing/restarting DevSpace and connecting Tailscale Funnel."
Invoke-DevSpaceSetup @(
    'setup',
    '--root', $ProjectRoot,
    '--hostname', $Hostname,
    '--apply'
)

Write-Host "`nChecking the repaired endpoint."
Invoke-DevSpaceSetup @(
    'doctor',
    '--root', $ProjectRoot,
    '--hostname', $Hostname
)

Write-Host "`nChatGPT Developer Mode URL: https://$Hostname/mcp"
