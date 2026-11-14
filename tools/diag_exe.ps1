# 빌드된 EXE 를 실제 Windows 창으로 띄우고, '업데이트 완료' 창에 닫기(WM_CLOSE)를 보내 멈추는지 확인한다.
$ErrorActionPreference = "Continue"
Add-Type -TypeDefinition @"
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public static class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsHungAppWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  public static List<IntPtr> Of(uint pid) { var r = new List<IntPtr>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid && IsWindowVisible(h)) r.Add(h); return true; }, IntPtr.Zero); return r; }
  public static string T(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
}
"@
function Show($procId, $label) {
  Write-Host "--- $label"
  foreach ($h in [W]::Of($procId)) { Write-Host ("  [{0}] hung={1}" -f [W]::T($h), [W]::IsHungAppWindow($h)) }
}
$scenario = $args[0]
$cfg = Join-Path $env:RUNNER_TEMP "cfg_$scenario"; $data = Join-Path $env:RUNNER_TEMP "data_$scenario"
New-Item -ItemType Directory -Force $cfg | Out-Null
$settings = @{ data_root = $data; last_run_version = "0.2.0"; check_updates = ($scenario -ne "nocheck") } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $cfg "settings.json"), $settings)
$env:CRS_CONFIG_DIR = $cfg
$env:CRS_DEBUG_DUMP = "20"
$env:CRS_DEBUG_DUMP_FILE = Join-Path $env:RUNNER_TEMP "dump_$scenario.txt"
$p = Start-Process -FilePath "dist\VConceptStudio\VConceptStudio.exe" -PassThru
Start-Sleep -Seconds 10
Show $p.Id "after start"
foreach ($h in [W]::Of($p.Id)) { if ([W]::T($h) -like "*업데이트 완료*") { Write-Host "  -> WM_CLOSE to dialog"; [W]::PostMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null } }
Start-Sleep -Seconds 6
Show $p.Id "6s after close"
Start-Sleep -Seconds 16
Show $p.Id "22s after close"
Write-Host "--- settings.json now:"; Get-Content (Join-Path $cfg "settings.json")
Write-Host "--- thread dump:"; Get-Content $env:CRS_DEBUG_DUMP_FILE -ErrorAction SilentlyContinue | Select-Object -First 80
Write-Host "--- app.log:"; Get-Content (Join-Path $data "logs\app.log") -ErrorAction SilentlyContinue
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
