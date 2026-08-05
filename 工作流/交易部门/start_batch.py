#!/usr/bin/env python3
"""Start batch processing - no API key in command line args"""
import os
import subprocess
import sys

env = os.environ.copy()
env["ZHIPU_API_KEY"] = "f6de857c9a5f45acafd3be75cdbb7e62.IQQmrpKGiX65q70J"
env["PYTHONIOENCODING"] = "utf-8"
env["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env.get("PATH", "")

# batch_process.py reads ZHIPU_API_KEY from env when not provided via --zhipu-api-key
cmd = [
    sys.executable, "工具链/脚本/batch_process.py",
    "--dir", "D:/BaiduNetdiskDownload/路肖南/钻潜交易内训",
    "--parallel", "2",
    "--frame-interval", "10",
    "--batch-size", "8",
    "--concurrency", "3",
    "--output-dir", "工具链/脚本/output",
]

log_file = open("batch_out.log", "w", encoding="utf-8")
proc = subprocess.Popen(cmd, env=env, stdout=log_file, stderr=subprocess.STDOUT)
with open("batch_pid.txt", "w") as f:
    f.write(str(proc.pid))
print(f"Batch PID: {proc.pid}")
