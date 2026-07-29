@echo off
cd /d C:\Users\32032\Desktop\deepseek\量化交易系统
set ZHIPU_API_KEY=f6de857c9a5f45acafd3be75cdbb7e62.IQQmrpKGiX65q70J
set PYTHONIOENCODING=utf-8
set PATH=%USERPROFILE%\.local\bin;%PATH%
python scripts/batch_process.py --dir "D:\BaiduNetdiskDownload\路肖南\钻潜交易内训" --parallel 2 --frame-interval 10 --batch-size 8 --concurrency 3 --output-dir scripts/output > batch_out.log 2>&1
