$ErrorActionPreference = "Continue"
$WorkDir = "C:\Users\user\forex-quant-risk-engine"
$Mt5Exe = "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
$PythonExe = (Get-Command python).Source

Set-Location $WorkDir

$mt5Proc = Get-Process -Name "terminal64" -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Mt5Exe }
if (-not $mt5Proc) {
    Start-Process -FilePath $Mt5Exe
    Start-Sleep -Seconds 12
}

& $PythonExe "$WorkDir\multi_forex_quant_bot.py"
& $PythonExe "$WorkDir\analyze_performance.py"
exit 0
