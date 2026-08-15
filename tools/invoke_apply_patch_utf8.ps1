[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PatchText,

    [string]$WorkingDirectory = (Get-Location).Path
)

$ErrorActionPreference = 'Stop'

function ConvertTo-WindowsCommandLineArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0

    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * ($backslashes * 2 + 1)))
            [void]$builder.Append('"')
        }
        else {
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * $backslashes))
            }
            [void]$builder.Append($character)
        }
        $backslashes = 0
    }

    if ($backslashes -gt 0) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

$codexCandidates = @(
    'C:\Users\Administrator\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe',
    'C:\Users\Administrator\AppData\Local\OpenAI\Codex\bin\d7e8094cfb76a267\codex.exe'
)
$codexPath = $codexCandidates | Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $codexPath) {
    throw 'Codex apply-patch executable was not found.'
}

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $codexPath
$startInfo.Arguments = '--codex-run-as-apply-patch ' +
    (ConvertTo-WindowsCommandLineArgument -Value $PatchText)
$startInfo.UseShellExecute = $false
$startInfo.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path

$process = [System.Diagnostics.Process]::Start($startInfo)
if ($null -eq $process) {
    throw 'Failed to start the Codex apply-patch process.'
}
$process.WaitForExit()
if ($process.ExitCode -ne 0) {
    throw "Codex apply-patch failed with exit code $($process.ExitCode)."
}
