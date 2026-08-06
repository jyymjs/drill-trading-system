Set-Location 'C:\Users\32032\Desktop\deepseek\工作流\交易部门'
$log = 'C:\Users\32032\Desktop\deepseek\工作流\交易部门\数据基础\data\duckdb_runtime\daily_update.log'
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] daily update start"
& 'C:\Program Files\Python312\python.exe' -m 数据基础.duckdb.update_daily --workers 8 --xdxr-check *>> $log
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] done exit=$LASTEXITCODE"
