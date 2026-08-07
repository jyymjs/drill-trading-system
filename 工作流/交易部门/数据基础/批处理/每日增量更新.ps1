param([switch]$Manual)
Set-Location 'C:\Users\32032\Desktop\deepseek\工作流\交易部门'
$log = 'C:\Users\32032\Desktop\deepseek\工作流\交易部门\数据基础\行情数据\duckdb_runtime\daily_update.log'
# 日志编码修复（R-034C，2026-08-07）：python 输出统一 UTF-8 解读，*>> 写盘改 UTF-8，避免中文乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
function Write-Log($msg) {
    [System.IO.File]::AppendAllText($log, "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg`n", [System.Text.Encoding]::UTF8)
}
# 手动来源标注（R-034C）：带 -Manual 开关（老板手动执行）时首行注明 MANUAL
$mode = ''
if ($Manual) { $mode = ' (MANUAL by 总助)' }
Write-Log "daily update start$mode"
& 'C:\Program Files\Python312\python.exe' -m 数据基础.duckdb.update_daily --workers 8 --xdxr-check *>> $log
Write-Log "done exit=$LASTEXITCODE"
