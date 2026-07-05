# Scheduled 6-hourly analyst run (Windows Task Scheduler).
# Register once (PowerShell):
#   schtasks /Create /TN "TradeLab Analyst" /SC HOURLY /MO 6 /F /TR "powershell -NoProfile -ExecutionPolicy Bypass -File E:\A_develop\trade_bot_fable\ops\run_analyst.ps1"
# Requires: claude CLI logged in, git credentials stored, SSH key in ~/.ssh.

Set-Location (Split-Path $PSScriptRoot -Parent)
git pull -q 2>&1 | Out-Null

$prompt = Get-Content ops\analyst_prompt.md -Raw
$log = "ops\analyst_runs.log"
"`n===== run $(Get-Date -Format o) =====" | Add-Content $log

claude -p $prompt --permission-mode acceptEdits --max-turns 120 2>&1 |
  Tee-Object -Variable out | Add-Content $log

"===== end $(Get-Date -Format o) =====" | Add-Content $log
