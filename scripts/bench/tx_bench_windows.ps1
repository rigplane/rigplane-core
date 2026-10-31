# Owner-present TX bench on Windows (MOR-3839). Not tested in CI here: it
# needs a real radio on a serial port into a dummy load with an owner at the
# bench, so no workflow runs it. Parameterized from the proven one-session
# IC-7300 recipe; same safety order, no machine-specific paths.
#
# Safety properties (kept from the proven recipe):
# - refuses without $env:DUMMY_LOAD_CONFIRMED='yes' and a non-empty
#   $env:BENCH_OWNER_YES (owner at the bench, dummy load confirmed);
# - refuses when any rigplane process is running (it may hold the serial port);
# - $ErrorActionPreference="Continue": a native stderr line must never become
#   a terminating error while the key may be down;
# - the bench tool runs through Start-Process -PassThru with output files, and
#   $null = $proc.Handle is read before WaitForExit() so ExitCode is not $null;
# - teardown order in finally: stop the bench process first, then ask Core to
#   force_off via the API and wait until ptt is false + releaseRequired false
#   + observedPtt off, then stop the server, then do a raw CI-V 1C 00 read and
#   a raw unkey if it reads TX (scripts/bench/civ_tx_state.py);
# - armed runs that printed "DRY / READ ONLY" are not TX results: fail;
# - after a failed run, stop before keying anything else.
param(
  [string]$Serial = "COM3",
  [string]$Model = "IC-7300",
  [string]$Profile = "ic7300",
  [Parameter(Mandatory)][string]$Venv,  # python.exe of the rigplane install (must have pyserial)
  [string]$Python = "",                 # a Python 3.11+ for the bench tool (stdlib only); defaults to -Venv
  [string]$RigsDir = "",                # optional rigs/ override, passed as --rigs-dir
  [Parameter(Mandatory)][string]$CivAddr,  # CI-V address as hex without 0x, e.g. "94"
  [int]$Baud = 115200,
  [int]$Port = 9701
)
$ErrorActionPreference = "Continue"      # native stderr must never become a terminating error mid-run
if ($env:DUMMY_LOAD_CONFIRMED -ne "yes") { Write-Output "refusing: DUMMY_LOAD_CONFIRMED=yes not set"; exit 2 }
if (-not $env:BENCH_OWNER_YES) { Write-Output "refusing: BENCH_OWNER_YES (owner confirmation note) not set"; exit 2 }
try { $null = [Convert]::ToByte($CivAddr, 16) } catch { Write-Output "refusing: -CivAddr must be hex without 0x (e.g. '94')"; exit 2 }
if (-not (Test-Path $Venv)) { Write-Output "refusing: -Venv not found: $Venv"; exit 2 }
$rigplane = Join-Path (Split-Path $Venv) "rigplane.exe"
if (-not (Test-Path $rigplane)) { Write-Output "refusing: rigplane.exe not next to -Venv: $rigplane"; exit 2 }
if (-not $Python) { $Python = $Venv }
& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { Write-Output "refusing: bench python is older than 3.11: $Python"; exit 2 }
& $Venv -c "import serial"
if ($LASTEXITCODE -ne 0) { Write-Output "refusing: -Venv python has no pyserial: $Venv"; exit 2 }
if ($RigsDir -and -not (Test-Path $RigsDir)) { Write-Output "refusing: -RigsDir not found: $RigsDir"; exit 2 }
if (Get-Process | Where-Object { $_.Name -match "rigplane" }) { Write-Output "refusing: a rigplane process is running (it may hold $Serial)"; exit 2 }
$base = "http://127.0.0.1:$Port"
$bench = Join-Path $PSScriptRoot "bench_tx_check.py"
$civHelper = Join-Path $PSScriptRoot "civ_tx_state.py"
$coreLog = Join-Path (Get-Location) "core.log"; $coreErr = Join-Path (Get-Location) "core.err"
function Get-State { try { Invoke-RestMethod -Uri "$base/api/v1/state" -TimeoutSec 4 } catch { $null } }
function Get-Managed { try { Invoke-RestMethod -Uri "$base/api/v1/managed-transmit" -TimeoutSec 4 } catch { $null } }
function Release-Radio {
  # Ask Core to release (force_off) and wait for fresh RX with nothing owed; false if it could not be confirmed.
  try { Invoke-RestMethod -Method Post -Uri "$base/api/v1/managed-transmit/command" -ContentType "application/json" -Body '{"operation":"force_off"}' -TimeoutSec 4 | Out-Null } catch {}
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    $s = Get-State; $m = Get-Managed
    if ($s -and $m -and $s.ptt -eq $false -and $m.managedTransmit.releaseRequired -eq $false -and $m.txObservation.observedPtt -eq "off") { return $true }
  }
  return $false
}
function Run-Bench([string]$label, [int]$run, [bool]$armed) {
  $benchArgs = @($bench, "--url", $base, "--label", $label, "--run", "$run", "--profile", $Profile, "--server-log", $coreErr)
  if ($RigsDir) { $benchArgs += @("--rigs-dir", $RigsDir) }
  if ($armed) { $benchArgs += @("--tx-armed", "--checklist-confirmed", "--no-clients-confirmed") }
  $so = Join-Path (Get-Location) "$label-run$run.out"; $se = Join-Path (Get-Location) "$label-run$run.err"
  $script:benchProc = Start-Process -FilePath $Python -ArgumentList $benchArgs -RedirectStandardOutput $so -RedirectStandardError $se -PassThru -NoNewWindow
  $null = $script:benchProc.Handle   # cache the handle: otherwise ExitCode can read as $null
  $script:benchProc.WaitForExit()
  $code = $script:benchProc.ExitCode; $script:benchProc = $null
  Get-Content $so, $se -ErrorAction SilentlyContinue | Out-Host
  return @{ code = $code; dry = [bool](Select-String -Path $so -Pattern "DRY / READ ONLY" -Quiet) }
}
$p = Start-Process -FilePath $rigplane -ArgumentList "--backend","serial","--serial-port",$Serial,"--serial-baud","$Baud","--model",$Model,"station","--listen","127.0.0.1","--port","$Port","--rigctld-port","45971" -RedirectStandardOutput $coreLog -RedirectStandardError $coreErr -PassThru -WindowStyle Hidden
$rc = 0; $script:benchProc = $null
try {
  for ($i = 0; $i -lt 40; $i++) { try { if ((Invoke-WebRequest -UseBasicParsing "$base/readyz" -TimeoutSec 3).Content -match 'radioReady":true') { break } } catch {}; Start-Sleep 1 }
  Start-Sleep 4
  $info = (Invoke-RestMethod -Uri "$base/api/v1/info" -TimeoutSec 5); "server: $($info.model) $($info.version)"
  if ($info.model -ne $Model) { throw "model is not ${Model}: $($info.model)" }
  "== dry run (unarmed, read only)"
  $d = Run-Bench "bench-dry" 1 $false
  if ($d.code -ne 0) { throw "dry run failed" }
  foreach ($run in 1,2,3,4) {
    "== armed run $run (dummy load, HF, minimum power)"
    $r = Run-Bench "bench" $run $true
    if ($r.dry) { throw "run $run WAS DRY (TX not armed): not a TX result" }
    if ($r.code -ne 0) { "RUN $run FAILED (exit $($r.code)); stopping before anything else is keyed"; $rc = 1; break }
  }
}
catch { "ERROR: $_"; $rc = 1 }
finally {
  # Safe teardown order: tool first, then a confirmed release through Core, then the server, then a raw check.
  if ($script:benchProc -and -not $script:benchProc.HasExited) { Stop-Process -Id $script:benchProc.Id -Force -ErrorAction SilentlyContinue; "stopped bench process" }
  $released = Release-Radio
  "release confirmed by Core before stopping the server: $released"
  if ($p) { Stop-Process -Id $p.Id -ErrorAction SilentlyContinue; Start-Sleep 4 }
  "server alive after stop: " + [bool](Get-Process -Id $p.Id -ErrorAction SilentlyContinue)
  Select-String -Path $coreErr -Pattern "managed TX (shutdown|release)" | Select-Object -Last 3 | ForEach-Object { $_.Line }
  & $Venv $civHelper --port $Serial --addr $CivAddr --baud $Baud --unkey-if-keyed
}
if ($rc -eq 0) { "TX BENCH ($Model): all armed runs completed" } else { "TX BENCH ($Model): FAILED" }
exit $rc
