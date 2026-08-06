foreach ($n in @('TradingDailyUpdate','TradingDailyScan')) {
  $t = Get-ScheduledTask -TaskName $n
  $t.Settings.StartWhenAvailable = $true
  $t.Settings.DisallowStartIfOnBatteries = $false
  $t.Settings.StopIfGoingOnBatteries = $false
  Set-ScheduledTask -TaskName $n -Settings $t.Settings | Out-Null
  Write-Host "$n : StartWhenAvailable + battery OK"
}
