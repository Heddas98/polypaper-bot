# JSONL log query helper (PowerShell native, no jq dependency).
#
# Usage:
#   .\scripts\jsonl_query.ps1                                    # tail last 50, all
#   .\scripts\jsonl_query.ps1 -Level ERROR                       # only errors
#   .\scripts\jsonl_query.ps1 -Level WARNING -Last 100           # 100 warnings
#   .\scripts\jsonl_query.ps1 -LoggerLike reconciliation         # logger filter
#   .\scripts\jsonl_query.ps1 -SinceMinutes 60                   # last 60 min
#   .\scripts\jsonl_query.ps1 -MsgLike "MISMATCH"                # message search
#   .\scripts\jsonl_query.ps1 -Level ERROR -SinceMinutes 30 -Format Tsv
[CmdletBinding()]
param(
  [string]$File = "data_store\structured.jsonl",
  [ValidateSet("DEBUG","INFO","WARNING","ERROR","CRITICAL","")]
  [string]$Level = "",
  [string]$LoggerLike = "",
  [string]$MsgLike = "",
  [int]$SinceMinutes = 0,
  [int]$Last = 50,
  [ValidateSet("Table","Tsv","Json","Count")]
  [string]$Format = "Table"
)

if (-not (Test-Path $File)) {
  Write-Error "File not found: $File. Has the bot been restarted to pick up P1-06?"
  exit 1
}

$cutoff = if ($SinceMinutes -gt 0) {
  (Get-Date).AddMinutes(-$SinceMinutes).ToUniversalTime()
} else { $null }

$rows = Get-Content $File | ForEach-Object {
  if ([string]::IsNullOrWhiteSpace($_)) { return }
  try { $_ | ConvertFrom-Json } catch { }
} | Where-Object {
  ($Level -eq "" -or $_.level -eq $Level) -and
  ($LoggerLike -eq "" -or $_.logger -like "*$LoggerLike*") -and
  ($MsgLike -eq "" -or $_.msg -like "*$MsgLike*") -and
  ($null -eq $cutoff -or [datetime]$_.ts -gt $cutoff)
}

if ($Last -gt 0 -and $Format -ne "Count") {
  $rows = $rows | Select-Object -Last $Last
}

switch ($Format) {
  "Table" { $rows | Format-Table ts, level, logger, msg -AutoSize -Wrap }
  "Tsv"   { $rows | ForEach-Object { "$($_.ts)`t$($_.level)`t$($_.logger)`t$($_.msg)" } }
  "Json"  { $rows | ConvertTo-Json -Depth 5 }
  "Count" {
    $total = ($rows | Measure-Object).Count
    Write-Host "Total matching rows: $total"
    $rows | Group-Object level | Sort-Object Name |
      ForEach-Object { Write-Host ("  {0,-10} {1}" -f $_.Name, $_.Count) }
  }
}
