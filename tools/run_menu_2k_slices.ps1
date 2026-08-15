# SUPERSEDED - do not run.
#   The paths below are dead: C:\Users\SangGeun\monoeye is not this checkout, the
#   WinGet BizHawk install is absent, and out/title_trace6/menu_capture.lua was
#   lost. Replacement, resolved from the repo + BizHawk-2.11.1-win-x64:
#       python tools/run_title_menu_capture.py --runs 3 --write-baseline
#       python tools/run_menu_candidates.py --glob "out/patch/menu_bisect/*.wsc"
#   The 2 KB slice bisection this script drove is also obsolete: the region is
#   decoded (tools/analyze_bank72_menu_atlas.py), so candidates are now one
#   640-byte plate or one 32-byte tile instead of a 2 KB slice.
exit 1

$ErrorActionPreference = "Continue"
$emuDir = "C:\Users\SangGeun\AppData\Local\Microsoft\WinGet\Packages\TASEmulators.BizHawk_Microsoft.Winget.Source_8wekyb3d8bbwe"
$emu = Join-Path $emuDir "EmuHawk.exe"
$lua = "c:\Users\SangGeun\monoeye\out\title_trace6\menu_capture.lua"
$out = "c:\Users\SangGeun\monoeye\out\title_trace6"
$bisect = "c:\Users\SangGeun\monoeye\out\patch\menu_bisect"
$refMenu = "D144B003D040"
$refTitle = "BF8FD8CD1554"

function Hash12([string]$path) {
  if (-not $path -or -not (Test-Path -LiteralPath $path)) { return "NONE" }
  return (Get-FileHash -LiteralPath $path -Algorithm MD5).Hash.Substring(0, 12)
}

function Run-Menu([string]$tag, [string]$rom) {
  Get-Process EmuHawk -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Milliseconds 500
  Remove-Item -LiteralPath "$out\$tag*" -ErrorAction SilentlyContinue
  $env:MENU_TAG = $tag
  Write-Host "RUN $tag"
  $p = Start-Process -FilePath $emu -WorkingDirectory $emuDir -ArgumentList @("--lua=$lua", $rom) -PassThru
  $deadline = (Get-Date).AddSeconds(55)
  while (-not $p.HasExited -and (Get-Date) -lt $deadline) {
    if ((Test-Path -LiteralPath "$out\$tag.log") -and (Select-String -LiteralPath "$out\$tag.log" -Pattern "DONE" -Quiet)) {
      break
    }
    Start-Sleep -Milliseconds 400
  }
  Start-Sleep -Seconds 8
  if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
  Start-Sleep -Milliseconds 400
  $menu = Get-ChildItem -LiteralPath $out -Filter "$tag*_menu_*.png" -ErrorAction SilentlyContinue | Select-Object -First 1
  $title = Get-ChildItem -LiteralPath $out -Filter "$tag*_title_*.png" -ErrorAction SilentlyContinue | Select-Object -First 1
  $mh = if ($menu) { Hash12 $menu.FullName } else { "NONE" }
  $th = if ($title) { Hash12 $title.FullName } else { "NONE" }
  $flag = "same"
  if ($mh -ne $refMenu -or $th -ne $refTitle) { $flag = "CHANGED" }
  Write-Host "$tag title=$th menu=$mh $flag"
}

foreach ($t in @(
  "SLICE_72_0000_2k",
  "SLICE_72_0800_2k",
  "SLICE_72_1000_2k",
  "SLICE_72_1800_2k"
)) {
  Run-Menu $t (Join-Path $bisect "$t.wsc")
}
Get-Process EmuHawk -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "REF title=$refTitle menu=$refMenu"
