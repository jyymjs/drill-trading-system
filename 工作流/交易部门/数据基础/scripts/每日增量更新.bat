@echo off
chcp 65001 >nul
cd /d "C:\Users\32032\Desktop\deepseek\工作流\交易部门"
echo [%date% %time%] daily update start >> data\duckdb_runtime\daily_update.log
"C:\Program Files\Python312\python.exe" -m 数据基础.duckdb.update_daily --workers 8 --xdxr-check >> data\duckdb_runtime\daily_update.log 2>&1
echo [%date% %time%] done exit=%errorlevel% >> data\duckdb_runtime\daily_update.log
