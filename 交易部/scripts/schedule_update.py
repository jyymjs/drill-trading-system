#!/usr/bin/env python3
"""设置 Windows 定时任务，每天自动更新数据

用法:
    python scripts/schedule_update.py                    # 创建定时任务（默认18:00）
    python scripts/schedule_update.py --time 17:30       # 自定义时间
    python scripts/schedule_update.py --remove           # 删除定时任务
    python scripts/schedule_update.py --status           # 查看任务状态
"""
import sys
import os
import subprocess
import argparse

TASK_NAME = "交易部-数据更新"


def get_script_path() -> str:
    """获取 run_update.py 的绝对路径"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_update.py")


def get_python_path() -> str:
    """获取 python 可执行文件路径"""
    return sys.executable or "python"


def create_task(time_str: str = "18:00") -> bool:
    """创建 Windows 定时任务"""
    python = get_python_path()
    script = get_script_path()

    cmd = [
        "schtasks", "/create", "/tn", TASK_NAME,
        "/tr", f'"{python}" "{script}"',
        "/sc", "daily", "/st", time_str,
        "/f",  # 强制覆盖
    ]

    print(f"创建定时任务: {TASK_NAME}")
    print(f"  执行: {python} {script}")
    print(f"  时间: 每天 {time_str}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            print("✅ 定时任务创建成功!")
            print(f"每天 {time_str} 将自动更新数据")
            return True
        else:
            print(f"❌ 创建失败: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ 出错: {e}")
        return False


def remove_task() -> bool:
    """删除定时任务"""
    cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            print(f"✅ 定时任务 '{TASK_NAME}' 已删除")
            return True
        else:
            print(f"❌ 删除失败: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ 出错: {e}")
        return False


def show_status():
    """查看定时任务状态"""
    cmd = ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST", "/v"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if result.returncode == 0:
            print(f"📋 定时任务 '{TASK_NAME}' 状态:")
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line:
                    print(f"  {line}")
        else:
            print(f"⚠️  任务 '{TASK_NAME}' 不存在")
    except Exception as e:
        print(f"❌ 查询失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="设置数据更新定时任务")
    parser.add_argument("--time", default="18:00", help="更新时间 (默认 18:00)")
    parser.add_argument("--remove", action="store_true", help="删除定时任务")
    parser.add_argument("--status", action="store_true", help="查看任务状态")

    args = parser.parse_args()

    if args.remove:
        remove_task()
    elif args.status:
        show_status()
    else:
        create_task(args.time)


if __name__ == "__main__":
    main()
