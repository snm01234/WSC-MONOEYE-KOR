# SUPERSEDED - do not run. See the banner in tools/run_menu_2k_slices.ps1.
# Replacement: tools/run_title_menu_capture.py + tools/run_menu_candidates.py
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
  if (-not $path -or -not (Test-Path $path)) { return "NONE" }
  return (Get-FileHash $path -Algorithm MD5).Hash.Substring(0, 12)
}

function Run-Menu([string]$tag, [string]$rom) {
  Get-Process EmuHawk -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Milliseconds 500
  Remove-Item "$out\$tag*" -ErrorAction SilentlyContinue
  $env:MENU_TAG = $tag
  Write-Host "RUN $tag"
  $p = Start-Process -FilePath $emu -WorkingDirectory $emuDir -ArgumentList @("--lua=$lua", $rom) -PassThru
  $deadline = (Get-Date).AddSeconds(55)
  while (-not $p.HasExited -and (Get-Date) -lt $deadline) {
    if ((Test-Path "$out\$tag.log") -and (Select-String -Path "$out\$tag.log" -Pattern "DONE" -Quiet)) {
      break
    }
    Start-Sleep -Milliseconds 400
  }
  Start-Sleep -Seconds 8
  if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
  Start-Sleep -Milliseconds 400
  $menu = Get-ChildItem "$out\$tag*_menu_*.png" -ErrorAction SilentlyContinue | Select-Object -First 1
  $title = Get-ChildItem "$out\$tag*_title_*.png" -ErrorAction SilentlyContinue | Select-Object -First 1
  $mh = Hash12 $menu.FullName
  $th = Hash12 $title.FullName
  $flag = "same"
  if ($mh -ne $refMenu -or $th -ne $refTitle) { $flag = "CHANGED" }
  Write-Host "$tag title=$th menu=$mh $flag"
}

$tests = @(
  "SLICE_72_0000",
  "SLICE_72_2000",
  "SLICE_72_4000",
  "SLICE_72_6000"
)
foreach ($t in $tests) {
  Run-Menu $t (Join-Path $bisect "$t.wsc")
}
Get-Process EmuHawk -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "REF title=$refTitle menu=$refMenu"
