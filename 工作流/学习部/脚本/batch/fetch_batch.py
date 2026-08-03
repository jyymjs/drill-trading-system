# -*- coding: utf-8 -*-
"""
批量抓取器（学习部 · 子任务12）
- 来源清单 → 顺序抓取 → 单条失败不中断整批 → 产出抓取报告（成功/失败/原因）
- B站 URL（含 bilibili.com / BV号）→ 调 scripts/bilibili/fetch.py；网页/论文 → scripts/web/fetch_web.py
- SESSDATA 过期只汇总提示一次，不逐条轰炸；风控：条间间隔 --sleep，B站子脚本自带请求间隔
- 输出位置由子脚本决定（--out 可整体覆盖，默认 知识库/{主题}/raw/）

用法：
  python fetch_batch.py --list 清单.txt [--topic 主题] [--out 目录] [--sleep 2.0] [--report 报告.md]
  python fetch_batch.py --urls "url1 url2" [--topic 主题] [--out 目录]
清单格式：每行一条「URL [可选类型 bilibili|web]」，# 开头为注释行；不标类型时自动识别（bilibili.com/BV号 → bilibili，其余 → web）
退出码：0 = 全部成功或有失败（报告为准）；2 = 参数错误
"""
import sys, os, re, time, argparse, subprocess

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 学习部/
FETCH_BILI = os.path.join(BASE, "scripts", "bilibili", "fetch.py")
FETCH_WEB = os.path.join(BASE, "scripts", "web", "fetch_web.py")


def load_list(path):
    """解析清单文件：每行 URL [类型]，# 注释、空行跳过"""
    entries = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            url, kind = parts[0], None
            if len(parts) >= 2 and parts[-1].lower() in ("bilibili", "web"):
                kind = parts[-1].lower()
            entries.append((url, kind))
    return entries


def guess_kind(url, explicit):
    """类型判定：显式类型优先；否则 bilibili.com/BV号 → bilibili，其余 → web"""
    if explicit:
        return explicit
    if "bilibili.com" in url.lower() or re.search(r"BV[0-9A-Za-z]{10}", url):
        return "bilibili"
    return "web"


def run_one(kind, url, topic, out, sleep):
    """执行单条抓取 → (输出路径或完成说明, 错误, SESSDATA过期标记)"""
    script = FETCH_BILI if kind == "bilibili" else FETCH_WEB
    cmd = [sys.executable, script, "--url", url, "--topic", topic]
    if out:
        cmd += ["--out", out]
    if kind == "bilibili":
        cmd += ["--sleep", str(sleep)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
    except subprocess.TimeoutExpired:
        return None, "执行超时（300s）", False
    output = (r.stdout or "") + (r.stderr or "")
    expired = "SESSDATA 已过期" in output
    if r.returncode == 0:
        if "已存在" in output:
            m = re.search(r"已存在: (\S+)", output)
            return (m.group(1) if m else "已存在"), None, expired
        m = re.search(r"✔ 已保存: (\S+?\.md)", output)  # 只取路径（后续可能有 （标题:…）等附加信息）
        return (m.group(1) if m else "完成"), None, expired
    # 失败原因：优先取子脚本「错误：」行，其次警告行，再取输出末尾
    err = None
    for ln in output.splitlines():
        s = ln.strip()
        if s.startswith("错误："):
            err = s
            break
        if "⚠" in s and not err:
            err = s
    if not err:
        tail = [l.strip() for l in output.splitlines()[-3:] if l.strip()]
        err = "；".join(tail[-2:]) if tail else "未知错误（子脚本无输出）"
    return None, err, expired


def build_report(results, sessdata_hint, elapsed):
    ok = sum(1 for r in results if r[2] is None)
    fail = len(results) - ok
    lines = [
        f"批量抓取报告（{time.strftime('%Y-%m-%d %H:%M:%S')}）",
        "=" * 46,
        f"任务：{len(results)} 条 → 成功 {ok} / 失败 {fail}（耗时 {elapsed:.0f}s）",
        "",
    ]
    for url, kind, path, err in results:
        if err is None:
            lines.append(f"[成功] {kind} {url} → {path}")
        else:
            lines.append(f"[失败] {kind} {url} → 原因：{err}")
    if sessdata_hint:
        lines += ["", "注：B站 SESSDATA 已过期（汇总提示一次）——升级字幕需更新 scripts/bilibili/config.local.json"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="批量抓取器（来源清单顺序抓取，单条失败不中断，产出报告）")
    ap.add_argument("--list", help="来源清单文件（每行 URL [bilibili|web]，# 注释）")
    ap.add_argument("--urls", help="空格分隔的 URL 列表（如 \"url1 url2\"）")
    ap.add_argument("--topic", default="未分类", help="主题分类（传给子脚本，入库 知识库/{主题}/raw/）")
    ap.add_argument("--out", help="输出目录（传给子脚本 --out，覆盖默认知识库路径）")
    ap.add_argument("--sleep", type=float, default=2.0, help="条间间隔秒数 + 传给 B站子脚本 --sleep（风控保护）")
    ap.add_argument("--report", help="报告输出文件路径（默认打印到 stdout）")
    args = ap.parse_args()

    entries = []
    if args.list:
        entries.extend(load_list(args.list))
    if args.urls:
        entries.extend((u, None) for u in args.urls.split())
    if not entries:
        print("错误：请提供 --list 或 --urls"); sys.exit(2)
    if not os.path.isfile(FETCH_BILI) or not os.path.isfile(FETCH_WEB):
        print(f"错误：子脚本缺失（bilibili: {os.path.isfile(FETCH_BILI)} / web: {os.path.isfile(FETCH_WEB)}）")
        sys.exit(2)

    results, sessdata_hint = [], False
    t0 = time.time()
    for i, (url, kind) in enumerate(entries, 1):
        k = guess_kind(url, kind)
        print(f"[{i}/{len(entries)}] {k}: {url}")
        path, err, expired = run_one(k, url, args.topic, args.out, args.sleep)
        sessdata_hint = sessdata_hint or expired
        results.append((url, k, path, err))
        if err:
            print(f"    ✗ 失败：{err}")
        else:
            print(f"    ✔ {path}")
        if i < len(entries):
            time.sleep(args.sleep)  # 风控：条间间隔

    report_text = build_report(results, sessdata_hint, time.time() - t0)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"\n报告已写入: {args.report}")
        print(f"摘要：{len(entries)} 条 → 成功 {sum(1 for r in results if r[2] is None)} / 失败 {sum(1 for r in results if r[2] is not None)}")
    else:
        print("\n" + report_text)


if __name__ == "__main__":
    main()
