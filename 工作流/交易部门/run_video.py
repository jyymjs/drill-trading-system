#!/usr/bin/env python3
"""
Run a single video through extract_knowledge.py.
Usage: python run_video.py <video_path> [temp_suffix]
"""
import subprocess, sys, os

video = sys.argv[1]
suffix = sys.argv[2] if len(sys.argv) > 2 else "0"

key = "f6de857c9a5f45acafd3be75cdbb7e62.IQQmrpKGiX65q70J"

env = os.environ.copy()
env["ZHIPU_API_KEY"] = key
env["PYTHONIOENCODING"] = "utf-8"
env["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env.get("PATH", "")
env["TEMP_ROOT"] = f"产出/临时/extract_knowledge/batch_{suffix}"
# 限制 CPU 占用：PyTorch/SenseVoice 用 4 个核心
env["OMP_NUM_THREADS"] = "4"
env["MKL_NUM_THREADS"] = "4"
env["OPENBLAS_NUM_THREADS"] = "4"
env["NUMEXPR_NUM_THREADS"] = "4"
env["VECLIB_MAXIMUM_THREADS"] = "4"

script = os.path.join(os.path.dirname(__file__), "scripts", "extract_knowledge.py")
output = os.path.join(os.path.dirname(__file__), "scripts", "output")

cmd = [
    sys.executable, script,
    "--video", video,
    "--output-dir", output,
    "--batch-size", "8",
    "--concurrency", "3",
]

name = os.path.basename(video)[:30]
log_path = os.path.join(os.path.dirname(__file__), f"log_{suffix}.txt")
creation_flags = 0
if sys.platform == "win32":
    # BELOW_NORMAL_PRIORITY_CLASS = 0x4000, CREATE_NO_WINDOW = 0x08000000
    creation_flags = 0x08004000
with open(log_path, "w", encoding="utf-8") as log:
    proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=creation_flags)

with open(os.path.join(os.path.dirname(__file__), f"pid_{suffix}.txt"), "w") as f:
    f.write(f"{name}\n{proc.pid}")

print(f"[{name}] started PID={proc.pid}")
