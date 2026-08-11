param([switch]$Manual)
Set-Location 'C:\Users\32032\Desktop\deepseek\工作流\交易部门'
$log = 'C:\Users\32032\Desktop\deepseek\工作流\交易部门\数据基础\行情数据\duckdb_runtime\daily_scan.log'
# 日志编码修复（R-034C，2026-08-07）：python 输出统一 UTF-8 解读，*>> 写盘改 UTF-8，避免中文乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
function Write-Log($msg) {
    [System.IO.File]::AppendAllText($log, "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg`n", [System.Text.Encoding]::UTF8)
}
# 扫描批次 + 执行卡留档（2026-08-11 老板拍板：关键时刻溯源——065342 批次删除后
# 无法核对触发价差异，教训入库）。当日全部批次（主+变体）与执行卡版本按时间戳归档
# 到 产出\输出\归档\，每日多次生成全部保留，不覆盖。
function Invoke-Archive {
    param([string]$date)
    $scanArch = "产出\输出\归档\扫描"
    New-Item -ItemType Directory -Force -Path $scanArch | Out-Null
    $files = Get-ChildItem '数据基础\扫描输出' -Filter "scan_result_${date}_*.csv" -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        $dest = Join-Path $scanArch $f.Name
        if (-not (Test-Path $dest)) { Copy-Item $f.FullName $dest -Force }
    }
    # R-066（2026-08-12）：归档必须含当日主文件（无变体后缀）——缺失 → 告警
    $mainFile = $files | Where-Object { $_.Name -match '^scan_result_\d{8}_\d{6}\.csv$' }
    if (-not $mainFile) { Write-Log "⚠️ R-066: 归档缺主文件（scan_result_${date}_*.csv 仅变体或全缺）——检查扫描链路" }
    $cardArch = "产出\输出\归档\执行卡"
    New-Item -ItemType Directory -Force -Path $cardArch | Out-Null
    $card = "产出\输出\执行卡_$date.md"
    if (Test-Path $card) {
        $ts = Get-Date -Format 'HHmmss'
        Copy-Item $card "$cardArch\执行卡_${date}_$ts.md" -Force
    }
    Write-Log "archive: $($files.Count) scan files + 执行卡 → 产出\输出\归档\"
    # 归档目录 git 留档（2026-08-11 老板拍板：溯源硬保障——每日自动入库，失败不阻断）
    try {
        git add "产出/输出/归档/扫描" "产出/输出/归档/执行卡" 2>$null
        git commit -m "chore: 每日扫描/执行卡归档（${date}）" 2>$null | Out-Null
        Write-Log "archive git: ok"
    } catch { Write-Log "archive git: fail ($_)" }
}
# 健康检查（R-034C，2026-08-07）：执行卡缺失 / 数据不新鲜 / 计划任务状态，异常集中一行一条写日志
function Invoke-HealthCheck {
    param([string]$date)
    $issues = @()
    # 检查1：当日执行卡（扫描正常应生成；去重跳过日也应存在）
    $card = "产出\输出\执行卡_$date.md"
    if (-not (Test-Path $card)) { $issues += "⚠️ 执行卡缺失 ($card)" }
    # 检查1.5（R-066 2026-08-12）：当日主扫描文件必须存在（空表也算——"无候选"语义）
    $mainScan = Get-ChildItem '数据基础\扫描输出' -Filter "scan_result_${date}_*.csv" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^scan_result_\d{8}_\d{6}\.csv$' } | Select-Object -First 1
    if (-not $mainScan) { $issues += "⚠️ 当日主扫描文件缺失（scan_result_${date}_*.csv 无主文件——扫描未生成/失败）" }
    # 检查2：数据新鲜度复核（与主流程 $check 逻辑一致，取首行防多行错误文本污染）
    $dbLatest = (& 'C:\Program Files\Python312\python.exe' -c "import duckdb; con=duckdb.connect(r'数据基础\行情数据\t017_p2.duckdb',read_only=True); print(con.execute('select max(date) from daily').fetchone()[0])" 2>&1 | Select-Object -First 1)
    $dbDate = "$dbLatest".Trim()
    if ($dbDate -ne (Get-Date -Format 'yyyy-MM-dd')) { $issues += "⚠️ 数据不新鲜(db=$dbDate)" }
    # 检查3：计划任务状态（非 Ready 视为异常，含不存在）
    foreach ($task in 'TradingDailyUpdate', 'TradingDailyScan') {
        $st = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
        if (-not $st) { $issues += "⚠️ 计划任务 $task 不存在" }
        elseif ($st.State -ne 'Ready') { $issues += "⚠️ 计划任务 $task 状态异常 ($($st.State))" }
    }
    if ($issues.Count -eq 0) { Write-Log "health check OK" }
    else { foreach ($i in $issues) { Write-Log $i } }
}
# 手动来源标注（R-034C）：带 -Manual 开关（老板手动执行）时首行注明 MANUAL
$mode = ''
if ($Manual) { $mode = ' (MANUAL by 总助)' }
Write-Log "scan start$mode"
# 1. 周末跳过
$day = (Get-Date).DayOfWeek
if ($day -eq 'Saturday' -or $day -eq 'Sunday') { Write-Log "weekend, skip"; exit 0 }
# 1.5 当日去重（T-022，2026-08-06）：白天手动跑过（已产出当日 scan_result）→ 跳过，
#     不再白跑 5 分钟；若当日带日期报告还没复制则顺手补一份，保证报告完整。
$date = Get-Date -Format 'yyyyMMdd'
$todayScan = Get-ChildItem '数据基础\扫描输出' -Filter "scan_result_${date}_*.csv" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($todayScan) {
    Write-Log "today scan already done, skip ($($todayScan.Name))"
    $daily = "产出\输出\扫描_$date.csv"
    if (-not (Test-Path $daily)) { Copy-Item $todayScan.FullName $daily -Force; Write-Log "report: $daily" }
    Invoke-Archive -date $date
    Invoke-HealthCheck -date $date
    Write-Log "scan done (skipped)"
    exit 0
}
# 2. 数据新鲜度检查（库最新日期应为今天或最近交易日）
$check = & 'C:\Program Files\Python312\python.exe' -c "import duckdb; con=duckdb.connect(r'数据基础\行情数据\t017_p2.duckdb',read_only=True); print(con.execute('select max(date) from daily').fetchone()[0])" 2>&1
$latest = $check.Trim()
Write-Log "db latest: $latest"
$today = Get-Date -Format 'yyyy-MM-dd'
if ($latest -ne $today) {
    Write-Log "data not fresh, run update first"
    & 'C:\Program Files\Python312\python.exe' -m 数据基础.duckdb.update_daily --workers 8 --xdxr-check *>> $log
    $latest2 = & 'C:\Program Files\Python312\python.exe' -c "import duckdb; con=duckdb.connect(r'数据基础\行情数据\t017_p2.duckdb',read_only=True); print(con.execute('select max(date) from daily').fetchone()[0])" 2>&1
    if ($latest2.Trim() -ne $today) {
        # 容错（2026-08-06）：增量失败重试一次；仍失败 → 用旧数据继续扫描 + 报告标注（不再中止）
        Write-Log "update retry (first attempt failed)"
        & 'C:\Program Files\Python312\python.exe' -m 数据基础.duckdb.update_daily --workers 8 --xdxr-check *>> $log
        $latest3 = & 'C:\Program Files\Python312\python.exe' -c "import duckdb; con=duckdb.connect(r'数据基础\行情数据\t017_p2.duckdb',read_only=True); print(con.execute('select max(date) from daily').fetchone()[0])" 2>&1
        if ($latest3.Trim() -ne $today) {
            Write-Log "update failed after retry, scan with old data (db date: $($latest3.Trim()))"
            $env:SCAN_STALE_DATA = $latest3.Trim()
        }
    }
}
# 3. 全市场扫描（prebreak S 级候选，5600 元整手约束）
& 'C:\Program Files\Python312\python.exe' main.py scan --strategy zuanqian_strategy --mode prebreak --max-price 50 *>> $log
# 4. 报告落盘（扫描输出复制为带日期报告；源目录=数据基础\扫描输出）
# R-066（2026-08-12）：只认主文件（scan_result_YYYYMMDD_HHMMSS.csv，无变体后缀）——
# 08-11 曾把 _grade 变体当主文件复制（产出/输出/扫描_20260811.csv 实为变体）
$src = Get-ChildItem '数据基础\扫描输出' -Filter 'scan_result*.csv' |
    Where-Object { $_.Name -match '^scan_result_\d{8}_\d{6}\.csv$' } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($src) { Copy-Item $src.FullName "产出\输出\扫描_$date.csv" -Force; Write-Log "report: 扫描_$date.csv" }
else { Write-Log "⚠️ R-066: 当日主扫描文件缺失（扫描未生成主文件或失败）——报告未复制" }
# 4.2 模拟线自动跟进（2026-08-08 老板确认方案①③）：扫描候选 → 模拟条件单（10万名义，
#     到价才成交）→ 到价成交/超期撤销/持仓出场——模拟线从此每天自动留痕
& 'C:\Program Files\Python312\python.exe' main.py track sim-auto-open *>> $log
& 'C:\Program Files\Python312\python.exe' main.py track sim-check *>> $log
# 4.45 扫描批次留档（2026-08-11 老板拍板）：当日批次 + 执行卡版本 → 归档目录（溯源用）
Invoke-Archive -date $date
# 4.5 健康检查（R-034C，2026-08-07）：执行卡/数据新鲜度/计划任务，异常集中写日志（曲线更新前，供开场流程/日报读取）
Invoke-HealthCheck -date $date
# 5. 曲线自动更新（2026-08-07 老板拍板"从今天起记录曲线"）：R 值曲线 + 净值/修正收益率 + 双线对照
& 'C:\Program Files\Python312\python.exe' main.py rcurve stats --plot *>> $log
& 'C:\Program Files\Python312\python.exe' main.py track equity-report *>> $log
& 'C:\Program Files\Python312\python.exe' main.py track dual-line *>> $log
& 'C:\Program Files\Python312\python.exe' main.py dashboard --live-only *>> $log
Write-Log "scan done"
