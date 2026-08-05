Set-Location 'C:\Users\32032\Desktop\deepseek\工作流\交易部门'
$log = 'C:\Users\32032\Desktop\deepseek\工作流\交易部门\数据基础\data\duckdb_runtime\daily_scan.log'
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] scan start"
# 1. 周末跳过
$day = (Get-Date).DayOfWeek
if ($day -eq 'Saturday' -or $day -eq 'Sunday') { Add-Content -Path $log -Value "weekend, skip"; exit 0 }
# 1.5 当日去重（T-022，2026-08-06）：白天手动跑过（已产出当日 scan_result）→ 跳过，
#     不再白跑 5 分钟；若当日带日期报告还没复制则顺手补一份，保证报告完整。
$date = Get-Date -Format 'yyyyMMdd'
$todayScan = Get-ChildItem '数据基础\output' -Filter "scan_result_${date}_*.csv" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($todayScan) {
    Add-Content -Path $log -Value "today scan already done, skip ($($todayScan.Name))"
    $daily = "产出\输出\扫描_$date.csv"
    if (-not (Test-Path $daily)) { Copy-Item $todayScan.FullName $daily -Force; Add-Content -Path $log -Value "report: $daily" }
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] scan done (skipped)"
    exit 0
}
# 2. 数据新鲜度检查（库最新日期应为今天或最近交易日）
$check = & 'C:\Program Files\Python312\python.exe' -c "import duckdb; con=duckdb.connect(r'数据基础\data\t017_p2.duckdb',read_only=True); print(con.execute('select max(date) from daily').fetchone()[0])" 2>&1
$latest = $check.Trim()
Add-Content -Path $log -Value "db latest: $latest"
$today = Get-Date -Format 'yyyy-MM-dd'
if ($latest -ne $today) {
    Add-Content -Path $log -Value "data not fresh, run update first"
    & 'C:\Program Files\Python312\python.exe' -m 数据基础.duckdb.update_daily --workers 8 --xdxr-check *>> $log
    $latest2 = & 'C:\Program Files\Python312\python.exe' -c "import duckdb; con=duckdb.connect(r'数据基础\data\t017_p2.duckdb',read_only=True); print(con.execute('select max(date) from daily').fetchone()[0])" 2>&1
    if ($latest2.Trim() -ne $today) { Add-Content -Path $log -Value "update failed, abort scan"; exit 1 }
}
# 3. 全市场扫描（prebreak S 级候选，5600 元整手约束）
& 'C:\Program Files\Python312\python.exe' main.py scan --strategy zuanqian_strategy --mode prebreak --max-price 50 *>> $log
# 4. 报告落盘（扫描输出复制为带日期报告；源目录=数据基础\output——扫描输出的实际落盘目录）
$src = Get-ChildItem '数据基础\output' -Filter 'scan_result*.csv' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($src) { Copy-Item $src.FullName "产出\输出\扫描_$date.csv" -Force; Add-Content -Path $log -Value "report: 扫描_$date.csv" }
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] scan done"
