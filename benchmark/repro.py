"""一键可复现（T1.4）：校验数据集哈希 → 跑主榜 → 对照参考数字。

用法：
    python -X utf8 benchmark/repro.py --verify           # 只校验数据集哈希
    python -X utf8 benchmark/repro.py --run              # 全量主榜（两数据集）
    python -X utf8 benchmark/repro.py --run --limit=2    # 冒烟
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark" / "repro" / "manifest.yaml"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify() -> bool:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    ok = True
    for name, spec in manifest["datasets"].items():
        path = ROOT / spec["path"]
        if not path.is_file():
            print(f"MISSING {name}: {path}（按 manifest.source 自行获取）")
            ok = False
            continue
        digest = _sha(path)
        match = digest == spec["sha256"]
        ok = ok and match
        print(f"{'OK  ' if match else 'MISMATCH'} {name}: sha256={digest[:16]}…")
    return ok


_sha = _sha256


def run(limit: int | None) -> int:
    if not verify():
        print("数据集未通过校验，拒绝复现（避免口径漂移）")
        return 2
    run_date = datetime.now().strftime("%Y%m%d")
    out = f"recall-{run_date}"
    board = ROOT / "benchmark" / "recall_board.py"
    limit_args = [f"--limit={limit}"] if limit else []
    cmd = [sys.executable, "-X", "utf8", str(board), "--out", out, *limit_args]
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        return proc.returncode
    summary = ROOT / "benchmark" / "results" / out / "SUMMARY.md"
    print(f"\n复现完成 -> {summary}")
    if limit is None:
        reference = ROOT / "benchmark" / "results" / "recall-20261103" / "SUMMARY.md"
        print(f"参考榜（±0.5pp 内视为一致）-> {reference}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    limit = None
    for arg in args:
        if arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
    if "--verify" in args:
        return 0 if verify() else 1
    if "--run" in args:
        return run(limit)
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
