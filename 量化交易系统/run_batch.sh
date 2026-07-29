#!/bin/bash
export ZHIPU_API_KEY="f6de857c9a5f45acafd3be75cdbb7e62.IQQmrpKGiX65q70J"
export PYTHONIOENCODING=utf-8
export PATH="$HOME/.local/bin:$PATH"
cd "C:/Users/32032/Desktop/deepseek/量化交易系统"
python scripts/batch_process.py --dir "D:/BaiduNetdiskDownload/路肖南/钻潜交易内训" --parallel 2 --output-dir scripts/output --frame-interval 10 --batch-size 8 --concurrency 3 > batch_out.log 2>&1
echo "Batch script finished"
