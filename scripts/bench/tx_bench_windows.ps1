# Owner-present TX bench on Windows (MOR-3839). Not tested in CI here: it
# needs a real radio on a serial port into a dummy load with an owner at the
# bench, so no workflow runs it. Kept short on purpose.
#
# One-command TX smoke of Core into a dummy load: starts Core `station`
# (bridge off) on -Serial, waits for ready, asserts -Model, runs the bench
# tool unarmed (preflight) then armed runs 1-4, stops the server, and reads
# raw CI-V 1C 00 afterwards.
#
# Needs: $env:DUMMY_LOAD_CONFIRMED='yes' (owner confirmed the dummy load) and
# a non-empty $env:BENCH_OWNER_YES (note of who confirmed what, when).
# -Venv is a venv directory with rigplane installed; -Cwd is the directory
# bench-out and the server logs land in; -CivAddr is the radio's CI-V address
# as hex without 0x (e.g. "94" for an IC-7300), used only for the final read.
param(
  [string]$Serial = "COM3",
  [string]$Model = "IC-7300",
  [string]$Profile = "ic7300",
  [Parameter(Mandatory)][string]$Venv,
  [Parameter(Mandatory)][string]$Cwd,
  [string]$Python = "C:\Python312-x64\python.exe",
  [Parameter(Mandatory)][string]$CivAddr,
  [int]$Port = 9701,
  [int]$RigctldPort = 45971
)
$ErrorActionPreference = "Stop"
if ($env:DUMMY_LOAD_CONFIRMED -ne "yes") { Write-Error "refusing: DUMMY_LOAD_CONFIRMED=yes not set"; exit 2 }
if (-not $env:BENCH_OWNER_YES) { Write-Error "refusing: BENCH_OWNER_YES (owner confirmation note) not set"; exit 2 }
[void][Convert]::ToByte($CivAddr, 16)
if (Get-Process | Where-Object { $_.Name -match "rigplane" }) { Write-Error "refusing: a rigplane process is running (is the Pro app up? it holds $Serial)"; exit 2 }
$bench = Join-Path $PSScriptRoot "bench_tx_check.py"
$log = Join-Path $Cwd "core.log"; $err = Join-Path $Cwd "core.err"
$p = Start-Process -FilePath (Join-Path $Venv "Scripts\rigplane.exe") -ArgumentList "--backend","serial","--serial-port",$Serial,"--serial-baud","115200","--model",$Model,"station","--listen","127.0.0.1","--port","$Port","--rigctld-port","$RigctldPort" -RedirectStandardOutput $log -RedirectStandardError $err -PassThru -WindowStyle Hidden
$rc = 0
try {
  for ($i = 0; $i -lt 40; $i++) { try { if ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/readyz" -TimeoutSec 3).Content -match 'radioReady":true') { break } } catch {}; Start-Sleep 1 }
  Start-Sleep 4
  $info = (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/v1/info").Content | & $Python -c "import json,sys; d=json.load(sys.stdin); print(d['model'], d['version'])"
  "server: $info"
  if (-not $info.StartsWith($Model)) { throw "model is not ${Model}: $info" }
  Set-Location $Cwd
  "== dry run (unarmed, read only)"
  & $Python $bench --url "http://127.0.0.1:$Port" --label bench-dry --run 1 --profile $Profile --server-log $err
  if ($LASTEXITCODE -ne 0) { throw "dry run failed" }
  foreach ($run in 1,2,3,4) {
    "== armed run $run (dummy load, HF, minimum power)"
    $o = & $Python $bench --url "http://127.0.0.1:$Port" --label bench --run $run --profile $Profile --tx-armed --checklist-confirmed --no-clients-confirmed --server-log $err 2>&1
    $o | Tee-Object -FilePath (Join-Path $Cwd "run$run.out")
    if ($LASTEXITCODE -ne 0) { throw "run $run FAILED" }
    if ($o -match "DRY / READ ONLY") { throw "run $run WAS DRY (TX not armed): not a TX result" }
  }
}
catch { "ERROR: $_"; $rc = 1 }
finally {
  try { (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/v1/state" -TimeoutSec 4).Content | & $Python -c "import json,sys; d=json.load(sys.stdin); print('final state: ptt', d.get('ptt'), 'freq', d['main']['freqHz'])" } catch {}
  Stop-Process -Id $p.Id -ErrorAction SilentlyContinue; Start-Sleep 4
  "server alive after stop: " + [bool](Get-Process -Id $p.Id -ErrorAction SilentlyContinue)
  Select-String -Path $err -Pattern "managed TX (shutdown|release)" | Select-Object -Last 3 | ForEach-Object { $_.Line }
  & $Python -c "import serial,time,sys; s=serial.Serial(sys.argv[2],115200,timeout=0.8,dsrdtr=False,rtscts=False); s.dtr=False; s.rts=False; s.reset_input_buffer(); s.write(bytes([0xFE,0xFE,int(sys.argv[1],16),0xE0,0x1C,0x00,0xFD])); time.sleep(0.5); print('raw 1C 00 after: ' + s.read(64).hex()); s.close()" $CivAddr $Serial
}
if ($rc -eq 0) { "TX SMOKE ($Model): all armed runs completed" } else { "TX SMOKE ($Model): FAILED" }
exit $rc
