"""回测重放加速公共件（R-061 · 2026-08-12 老板拍板）

背景：r57 全实验 ~30 分钟，实测 85% 耗时 = read_kline 无缓存重复读
（~60,000 次读只有 628 个唯一代码，平均每只被重读 95 次）+ 重放 65% 重复。

三个可复用件（未来实验脚本 import 即用，模式见 回测系统/README.md）：
  KlineCache        K 线内存缓存——get(code) 惰性读入（loader），preload(codes) 批量
  ReplayResultCache 重放结果缓存——(switches_fp, hold) → {code_date: result}，
                    key 含开关指纹防错配；window() 按 min_date 裁剪输出行集
  parallel_replay   多进程分片并行——Windows spawn 安全（调用方须 __main__ 保护），
                    worker 进程内独立 K 线缓存（分片自然局部化）

用法：
  kc = KlineCache(loader=read_kline)
  df = kc.get(code)                      # 首次读入，之后 O(1)
  rc = ReplayResultCache()
  rc.set(key, rep_map); rc.window(key, min_date)
  results = parallel_replay(codes, worker_fn, n_workers=8)
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd


class KlineCache:
    """K 线内存缓存（进程内）。worker 进程各自持有 → 分片自然局部化。

    loader: callable(code) -> pd.DataFrame | None（缺省用 数据基础.duckdb.reader.read_kline）
    """

    def __init__(self, loader: Callable[[str], pd.DataFrame | None] | None = None):
        self._store: dict[str, pd.DataFrame] = {}
        self._loader = loader

    @staticmethod
    def _default_loader(code: str) -> pd.DataFrame | None:
        from 数据基础.duckdb.reader import read_kline
        return read_kline(code, shared=True)

    def get(self, code: str) -> pd.DataFrame | None:
        if code not in self._store:
            df = (self._loader or self._default_loader)(code)
            if df is not None and not df.empty:
                self._store[code] = df
        return self._store.get(code)

    def preload(self, codes: Iterable[str]) -> None:
        """批量预热（复用 confirm_replay.load_kline_cache 单连接批量读）"""
        miss = [c for c in set(codes) if c not in self._store]
        if not miss:
            return
        try:
            from 回测系统.confirm_replay import load_kline_cache
            self._store.update(load_kline_cache(miss))
        except Exception:  # noqa: BLE001 - 批量读失败退回逐只惰性读
            for c in miss:
                self.get(c)

    def __len__(self) -> int:
        return len(self._store)


def _switches_fp(switches: dict) -> frozenset:
    """开关组合指纹（顺序无关）——作为重放结果缓存 key 的一部分防错配"""
    return frozenset(switches.items())


class ReplayResultCache:
    """重放结果缓存：key = (switches_fp, hold) → {f"{code}_{date}": result}

    key 含开关指纹 + 观察窗防错配（不同规则组合/窗口绝不混用）；
    window() 按 min_date 裁剪输出行集（重放全量一次、窗口只过滤输出——
    等价性已由 R-061 探索逐字节确认）。

    R-061 磁盘持久化：set_persist_dir() 后 save/load_persisted()——同一信号集
    （sig_hash 校验）下跨进程/跨实验复用重放结果（未来实验秒开）；信号集更新
    （每日 18:00 数据）→ 哈希变 → 自动失效重建。
    """

    def __init__(self, persist_dir: str | Path | None = None):
        self._data: dict[tuple[frozenset, object], dict[str, dict]] = {}
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._fp_cache: dict[str, Path] = {}

    def set_persist_dir(self, path: str | Path) -> None:
        self._persist_dir = Path(path)

    def key(self, switches: dict, hold: int | None) -> tuple[frozenset, object]:
        return (_switches_fp(switches), hold)

    def set(self, key: tuple, rep_map: dict[str, dict]) -> None:
        self._data[key] = rep_map

    def get(self, key: tuple) -> dict[str, dict] | None:
        return self._data.get(key)

    def window(self, key: tuple, min_date: str | None = None) -> dict[str, dict]:
        """从缓存取全量或按信号日裁剪（min_date 只过滤输出行集）"""
        m = self.get(key) or {}
        if min_date is None:
            return m
        md = str(min_date)[:10]
        return {k: v for k, v in m.items() if k.split("_", 1)[1][:10] >= md}

    def __len__(self) -> int:
        return len(self._data)

    # ── 磁盘持久化（跨进程/跨实验复用）──

    def _fp(self, key: tuple, sig_hash: str) -> Path:
        import hashlib as _hl
        ck = f"{sorted(key[0])}_{key[1]}"
        h = _hl.sha256(ck.encode("utf-8")).hexdigest()[:12]
        return (self._persist_dir or Path(".")) / f"replay_{sig_hash}_{h}.json"

    def load_persisted(self, key: tuple, sig_hash: str) -> dict[str, dict] | None:
        """磁盘缓存命中 → 载入内存并返回；miss/损坏 → None（触发全量重放）"""
        if self._persist_dir is None:
            return None
        fp = self._fp(key, sig_hash)
        try:
            if not fp.exists():
                return None
            data = json.loads(fp.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None
            self._data[key] = data
            return data
        except Exception:  # noqa: BLE001 - 损坏缓存安全回退
            return None

    def save_persisted(self, key: tuple, sig_hash: str) -> None:
        if self._persist_dir is None:
            return
        data = self.get(key)
        if not data:
            return
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        fp = self._fp(key, sig_hash)
        tmp = fp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(fp)


def parallel_replay(codes: list[str], worker: Callable[[list[str]], list],
                    n_workers: int | None = None) -> list:
    """多进程分片并行重放（Windows spawn 安全——调用方脚本须有 __main__ 保护）。

    worker(codes_chunk) → list[result]（worker 内自建 KlineCache 局部缓存）。
    返回扁平结果列表（顺序不保证）。n_workers 缺省 = max(1, min(cpu-2, 8))。
    """
    import os
    if n_workers is None:
        n_workers = max(1, min(os.cpu_count() - 2 or 1, 8))
    if len(codes) <= n_workers:
        return worker(codes)
    chunks = [codes[i::n_workers] for i in range(n_workers)]
    chunks = [c for c in chunks if c]
    if len(chunks) <= 1:
        return worker(chunks[0])
    with ProcessPoolExecutor(max_workers=len(chunks)) as ex:
        results = list(ex.map(worker, chunks))
    return [r for chunk in results for r in chunk]
