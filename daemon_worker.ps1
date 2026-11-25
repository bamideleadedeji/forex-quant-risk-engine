# Prevent Windows Modern Standby / Sleep while this worker is running (allows screen to turn off)
$code = @"
using System;
using System.Runtime.InteropServices;
public class PowerState {
    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
Add-Type -TypeDefinition $code -ErrorAction SilentlyContinue
# ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x00000001)
[PowerState]::SetThreadExecutionState(2147483649) | Out-Null

$WorkDir = "C:\Users\user\forex-quant-risk-engine"
$Runner = "$WorkDir\run_exness_engine.ps1"

while ($true) {
    try {
        & $Runner
    } catch {
        Add-Content -Path "$WorkDir\bot_execution.log" -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [ERROR] Daemon cycle error: $_"
    }
    Start-Sleep -Seconds 900
}
