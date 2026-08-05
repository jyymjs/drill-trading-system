#!/usr/bin/env python3
"""启动并行批量处理"""
import os
import subprocess
import sys

DIR = "D:/BaiduNetdiskDownload/路肖南/钻潜交易内训"
KEY = "f6de857c9a5f45acafd3be75cdbb7e62.IQQmrpKGiX65q70J"

env = os.environ.copy()
env["ZHIPU_API_KEY"] = KEY
env["PYTHONIOENCODING"] = "utf-8"
env["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env.get("PATH", "")

cmd = [
    sys.executable, "工具链/脚本/batch_process.py",
    "--dir", DIR,
    "--parallel", "2",
    "--frame-interval", "10",
    "--batch-size", "8",
    "--concurrency", "3",
    "--output-dir", "工具链/脚本/output",
]

print("Starting batch processing (2 parallel)...")
proc = subprocess.Popen(cmd, env=env, stdout=open("batch_output.log","w"), stderr=subprocess.STDOUT)
with open("batch_pid.txt", "w") as f:
    f.write(str(proc.pid))
print(f"PID: {proc.pid} written to batch_pid.txt")
print("Monitor with: tail -f batch_output.log")
