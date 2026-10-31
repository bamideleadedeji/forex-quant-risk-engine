Set WshShell = CreateObject("WScript.Shell")
' Change the path below to your actual local repository directory path
WshShell.Run "cmd.c /c cd /d ""C:\path\to\your\forex-quant-risk-engine"" && python multi_forex_quant_bot.py && git add trade_log.csv && git commit -m ""Auto-update trade log [skip ci]"" && git push origin master", 0, False
Set WshShell = Nothing
