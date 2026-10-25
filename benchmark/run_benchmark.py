#!/usr/bin/env python3
"""RepoMem Bench 跑批器（v0.1）。

三组对照：
  A  无上下文（基线）
  B  仓库根注入任务文件里的 agents_md
  C  `rsi bootstrap` 就地学习 + 写 .mcp.json 接入同仓库记忆（rsi 可用时）

后端：
  manual  打印 prompt，由人/agent 在外部完成后回车，runner 收集测试结果与 diff
  claude  `claude -p` headless 驱动（--output-format json，采集真实 token/轮次/花费）
  cursor  预留

隔离：
  任务声明 `isolate: venv` 时，setup/verify 跑在仓库内 .repomem-venv/ 的独立
  Python 环境（PATH 前置注入），不污染调用者环境。克隆目录整体在
  benchmark/.workdir/（已 gitignore）。

用法：
  python benchmark/run_benchmark.py --task <task.yaml> --validate
  python benchmark/run_benchmark.py --task <task.yaml> --arm A --backend manual
  python benchmark/run_benchmark.py --task <task.yaml> --arm C --backend claude --model claude-sonnet-4-5
  python benchmark/run_benchmark.py --task <task.yaml> --print-task
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

BENCH_DIR = Path(__file__).resolve().parent
WORKDIR = BENCH_DIR / ".workdir"
RESULTS_DIR = BENCH_DIR / "results"

# claude headless 参数集（首轮实跑时按 CLI 实际行为校准；原始输出会入库备查）
CLAUDE_BASE_FLAGS = ["-p", "--output-format", "json", "--permission-mode", "acceptEdits"]


def load_task(path: Path) -> dict:
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    if task.get("schema") != "repomem-task/v1":
        raise SystemExit(f"不支持的 schema: {task.get('schema')!r}（需要 repomem-task/v1）")
    for key in ("id", "repo", "prompt", "verify"):
        if key not in task:
            raise SystemExit(f"任务缺少必填字段: {key}")
    task["_dir"] = path.resolve().parent   # 补丁等相对路径的解析基准
    return task


def _force_remove_readonly(func, path, _exc):
    """rmtree 的 onerror：Windows 下 .git 只读对象先去只读位再删。"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path: Path) -> None:
    shutil.rmtree(path, onerror=_force_remove_readonly)


def patch_path(task: dict, key: str) -> Path | None:
    """verify_patch / gold_patch：相对任务文件所在目录。"""
    rel = task.get(key)
    return (task["_dir"] / rel) if rel else None


def apply_patch(repo_dir: Path, patch: Path) -> None:
    run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=repo_dir)


def reset_repo(repo_dir: Path) -> None:
    """还原工作区到 HEAD（保留 venv）。"""
    run(["git", "checkout", "--", "."], cwd=repo_dir)
    run(["git", "clean", "-fdq", "-e", ".repomem-venv"], cwd=repo_dir)


def run(cmd: list[str] | str, cwd: Path, env: dict | None = None,
        check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
    printable = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"  $ {printable}", flush=True)
    proc = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str),
                          capture_output=True, env=env, timeout=timeout,
                          encoding="utf-8", errors="replace")  # 防 jest 等输出的非 GBK 字符炸 reader 线程
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-2000:])
        raise SystemExit(f"命令失败（{proc.returncode}）: {printable}")
    return proc


def clone_repo(task: dict) -> tuple[Path, str]:
    """克隆到 .workdir/<task-id>/ 并返回 (目录, resolved SHA)。

    已存在的目录若缺目标 ref（中断的残次克隆），整目录清掉重克隆。
    """
    dest = WORKDIR / task["id"]
    ref = task["repo"]["ref"]
    if dest.exists():
        probe = run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                    cwd=dest, check=False)
        if probe.returncode != 0:
            print("  ! 已存在克隆缺目标 ref，视为残次克隆，删除重来")
            rmtree(dest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        is_sha = len(ref) == 40 and all(c in "0123456789abcdef" for c in ref.lower())
        if is_sha:
            # 钉死 SHA 的 issue 级任务：浅取单提交即可，避开全量克隆（大仓库/弱网友好）
            dest.mkdir()
            run(["git", "init", "-q"], cwd=dest)
            run(["git", "remote", "add", "origin", task["repo"]["url"]], cwd=dest)
            run(["git", "fetch", "--depth", "1", "origin", ref], cwd=dest, timeout=600)
            run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest)
        else:
            run(["git", "clone", "--quiet", "--depth", "1", "--branch", ref,
                 task["repo"]["url"], dest.name], cwd=WORKDIR, timeout=600)
    run(["git", "checkout", "--quiet", ref], cwd=dest)
    run(["git", "clean", "-fdq", "-e", ".repomem-venv"], cwd=dest)  # 清残留但保留 venv
    run(["git", "checkout", "--", "."], cwd=dest)    # 还原被改动的跟踪文件
    # 组别纯洁性：上轮 C 组的 .rsi/.mcp.json 不得残留（ignore 会让 clean 漏掉 .rsi）
    rsi_dir = dest / ".rsi"
    if rsi_dir.exists():
        rmtree(rsi_dir)
    mcp_json = dest / ".mcp.json"
    if mcp_json.exists():
        mcp_json.unlink()
    sha = run(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip()
    return dest, sha


def make_venv(repo_dir: Path) -> dict:
    """在仓库内建独立 venv，返回注入后的 env（PATH 前置 Scripts/bin）。

    判活看 python 可执行文件而非目录——半成品 venv（创建被中断）清掉重建。
    """
    venv_dir = repo_dir / ".repomem-venv"
    scripts = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    exe = scripts / ("python.exe" if os.name == "nt" else "python")
    if not exe.exists():
        if venv_dir.exists():
            print("  ! 半成品 venv（缺 python 可执行文件），删除重建")
            rmtree(venv_dir)
        print(f"  $ python -m venv {venv_dir.name}")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    scripts = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    env = os.environ.copy()
    env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(venv_dir)
    return env


def prepare_env(task: dict, repo_dir: Path) -> dict | None:
    if task.get("isolate") == "venv":
        return make_venv(repo_dir)
    return None


def apply_arm(arm: str, task: dict, repo_dir: Path) -> str:
    """按组别布置上下文，返回记入结果的 arm_setup 描述。"""
    if arm == "A":
        return "none"
    if arm == "B":
        content = task.get("agents_md")
        if not content:
            raise SystemExit("任务缺少 agents_md 字段，无法跑 B 组")
        (repo_dir / "AGENTS.md").write_text(content, encoding="utf-8")
        return "agents_md injected"
    # C 组：就地 bootstrap + 写项目级 .mcp.json（claude 后端再用 --mcp-config 显式指认）
    rsi = shutil.which("rsi")
    if not rsi:
        print("  ! 未找到 rsi，可 `pip install rsi-boot`；本组降级为无 bootstrap", file=sys.stderr)
        return "rsi missing, degraded"
    mcp_cfg = {
        "mcpServers": {
            "rsi-boot": {
                "command": rsi,
                "args": ["serve"],
                "env": {"RSI_PROJECT_ROOT": str(repo_dir)},
            }
        }
    }
    (repo_dir / ".mcp.json").write_text(json.dumps(mcp_cfg, indent=2), encoding="utf-8")
    proc = run("rsi bootstrap --local-judge", cwd=repo_dir, check=False, timeout=1800)
    if proc.returncode == 0:
        return "rsi bootstrap --local-judge ok + .mcp.json"
    print("  ! rsi bootstrap 失败，仅保留 .mcp.json", file=sys.stderr)
    return "rsi bootstrap failed, .mcp.json only"


def backend_manual(task: dict, repo_dir: Path) -> dict:
    print("\n" + "=" * 72)
    print("【manual 后端】请在另一个 agent/终端中进入该目录完成任务：")
    print(f"  cd {repo_dir}")
    print("-" * 72)
    print(task["prompt"].strip())
    print("=" * 72)
    input("\n完成后回到这里按回车，runner 将执行验证并收集结果…")
    return {"backend_report": None}


def backend_claude(task: dict, repo_dir: Path, arm: str,
                   model: str | None, timeout: int) -> dict:
    """claude -p headless 驱动；返回 tokens/model/轮次/花费等真实数据。"""
    if not shutil.which("claude"):
        raise SystemExit("未找到 claude CLI（npm i -g @anthropic-ai/claude-code）")
    cmd = ["claude", *CLAUDE_BASE_FLAGS]
    if model:
        cmd += ["--model", model]
    if arm == "C" and (repo_dir / ".mcp.json").exists():
        cmd += ["--mcp-config", str(repo_dir / ".mcp.json"),
                "--allowedTools", "mcp__rsi-boot"]
    cmd += [task["prompt"].strip()]
    proc = run(cmd, cwd=repo_dir, check=False, timeout=timeout)
    report = {"raw_stdout_tail": proc.stdout[-4000:], "exit_code": proc.returncode}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("  ! claude 输出非 JSON，tokens 记 null（原始输出尾部已入库）", file=sys.stderr)
        return {"backend_report": report}
    usage = data.get("usage") or {}
    report.update({
        "tokens": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_create": usage.get("cache_creation_input_tokens"),
        },
        "model": data.get("model") or sorted((data.get("modelUsage") or {}).keys()) or None,
        "num_turns": data.get("num_turns"),
        "cost_usd": data.get("total_cost_usd"),
        "is_error": data.get("is_error"),
    })
    return {"backend_report": report}


def check_forbidden(task: dict, repo_dir: Path) -> list[str]:
    """forbidden_paths 支持 glob 与目录前缀两种写法（"tests/" 等价于 "tests/**"）。"""
    changed = run(["git", "diff", "--name-only", "HEAD"], cwd=repo_dir).stdout.split()

    def hit(path: str, pattern: str) -> bool:
        prefix = pattern.rstrip("/") + "/"
        return fnmatch.fnmatch(path, pattern) or path.startswith(prefix)

    hits = []
    for pattern in task.get("forbidden_paths", []):
        hits.extend(f for f in changed if hit(f, pattern))
    return sorted(set(hits))


def collect(task: dict, repo_dir: Path, arm: str, arm_setup: str, backend: str,
            backend_report: dict | None, sha: str, started: float,
            env: dict | None) -> dict:
    report = backend_report or {}
    # 先冻结 agent 的 diff（补丁注入前），再注入测试补丁执行 verify
    changed = run(["git", "diff", "--name-only", "HEAD"], cwd=repo_dir).stdout.split()
    forbidden = check_forbidden(task, repo_dir)
    vp = patch_path(task, "verify_patch")
    if vp:
        # 防「改测试糊分」弄崩打分：先把补丁目标文件还原到 HEAD 再注入
        # （违规已由 forbidden 记录在案，打分照常用隐藏测试）
        targets = [ln.split(" b/")[-1] for ln in vp.read_text(encoding="utf-8").splitlines()
                   if ln.startswith("diff --git ")]
        if targets:
            run(["git", "checkout", "--", *targets], cwd=repo_dir, check=False)
        print(f"  $ 注入测试补丁 {vp.name}")
        apply_patch(repo_dir, vp)
    t0 = time.time()
    proc = run(task["verify"], cwd=repo_dir, env=env, check=False)
    return {
        "run_id": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{task['id']}-{arm}",
        "benchmark": "repomem-bench/v0",
        "task": task["id"],
        "arm": arm,
        "arm_setup": arm_setup,
        "backend": backend,
        "repo": {"url": task["repo"]["url"], "ref": task["repo"]["ref"], "resolved_sha": sha},
        "model": report.get("model"),
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "duration_s": round(time.time() - started, 1),
        "verify": {"command": task["verify"], "exit_code": proc.returncode,
                   "passed": proc.returncode == 0,
                   "verify_duration_s": round(time.time() - t0, 1),
                   "verify_patch": vp.name if vp else None},
        "tokens": report.get("tokens"),          # 取不到即 null，绝不估算
        "num_turns": report.get("num_turns"),
        "cost_usd": report.get("cost_usd"),
        "files_changed": changed,
        "forbidden_violations": forbidden,
        "backend_raw_tail": report.get("raw_stdout_tail"),
    }


def validate_task(task: dict, repo_dir: Path, env: dict | None) -> bool:
    """校验任务。

    带 gold_patch（SWE-bench 式任务）：① 仅注入测试补丁必须 FAIL（区分度）；
    ② gold+测试补丁必须 PASS（可解性）。两步间还原工作区。
    无 gold_patch：pristine 仓库 verify 必须 PASS。
    """
    gold = patch_path(task, "gold_patch")
    vp = patch_path(task, "verify_patch")
    if gold:
        if not vp:
            print("[validate] 有 gold_patch 但缺 verify_patch——测试补丁是判分依据，必须提供")
            return False
        print("[validate] ① 仅注入测试补丁：期望 FAIL（证明区分度）")
        apply_patch(repo_dir, vp)
        pre = run(task["verify"], cwd=repo_dir, env=env, check=False)
        reset_repo(repo_dir)
        if pre.returncode == 0:
            print("  ! 测试补丁在未修复代码上通过——任务无区分度，不可入得分集")
            return False
        print("  [OK] 未修复代码上测试如期失败")
        print("[validate] ② 注入 gold+测试补丁：期望 PASS（证明可解）")
        apply_patch(repo_dir, gold)
        apply_patch(repo_dir, vp)
        post = run(task["verify"], cwd=repo_dir, env=env, check=False)
        reset_repo(repo_dir)
        if post.returncode != 0:
            print("  ! gold 补丁未能让测试通过——任务或补丁有问题")
            return False
        print("  [OK] gold 修复后测试通过 — 任务可入得分集")
        return True
    print("[validate] pristine 仓库执行 verify …")
    proc = run(task["verify"], cwd=repo_dir, env=env, check=False)
    ok = proc.returncode == 0
    print(f"[validate] {'通过 — 可将 status 改为 validated' if ok else '失败 — 任务尚不可入得分集'}")
    return ok


STATE_FILE = ".repomem-run.json"


def cmd_prepare(task: dict, args, repo_dir: Path, sha: str) -> None:
    """两阶段之准备：布置组别上下文，落状态文件， stdout 输出 workdir+prompt（JSON）。

    编排者随后让 agent 在 workdir 里干活，再调 --collect-only。
    """
    arm_setup = apply_arm(args.arm, task, repo_dir)
    state = {"arm": args.arm, "arm_setup": arm_setup, "backend": args.backend,
             "sha": sha, "started": time.time()}
    (repo_dir / STATE_FILE).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"workdir": str(repo_dir), "prompt": task["prompt"],
                      "arm_setup": arm_setup}, ensure_ascii=False))


def cmd_collect(task: dict, args) -> None:
    """两阶段之收集：不做任何 reset——agent 的改动就是待验收的现场。"""
    repo_dir = WORKDIR / task["id"]
    state_path = repo_dir / STATE_FILE
    if not state_path.exists():
        raise SystemExit(f"缺少状态文件 {STATE_FILE}——先跑 --prepare-only")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    env = prepare_env(task, repo_dir)   # venv 已存在则直接复用，不会重建
    result = collect(task, repo_dir, state["arm"], state["arm_setup"],
                     state["backend"], None, state["sha"], state["started"], env)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{result['run_id']}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    state_path.unlink()
    print(f"\n结果：{'PASS' if result['verify']['passed'] else 'FAIL'} "
          f"· 约定违反 {len(result['forbidden_violations'])} 处 → {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="RepoMem Bench runner (v0.3)")
    ap.add_argument("--task", required=True, type=Path, help="任务 YAML 路径")
    ap.add_argument("--arm", choices=["A", "B", "C"], default="A")
    ap.add_argument("--backend", choices=["manual", "claude", "cursor"], default="manual")
    ap.add_argument("--model", default=None, help="claude 后端透传 --model")
    ap.add_argument("--timeout", type=int, default=1800, help="agent 执行超时（秒）")
    ap.add_argument("--validate", action="store_true", help="克隆并在 pristine 仓库上跑 setup+verify")
    ap.add_argument("--print-task", action="store_true", help="仅解析打印任务，不触网")
    ap.add_argument("--prepare-only", action="store_true", help="两阶段：只做准备（clone+setup+arm），输出 workdir+prompt")
    ap.add_argument("--collect-only", action="store_true", help="两阶段：只收集（diff+补丁+verify+结果），不 reset")
    args = ap.parse_args()

    task = load_task(args.task)

    if args.print_task:
        print(json.dumps(task, ensure_ascii=False, indent=2))
        return

    if args.collect_only:
        cmd_collect(task, args)          # 不做任何 reset，直接验收现场
        return

    print(f"[1/5] 克隆 {task['repo']['url']} @ {task['repo']['ref']}")
    repo_dir, sha = clone_repo(task)

    print("[2/5] 准备隔离环境")
    env = prepare_env(task, repo_dir)

    print("[3/5] setup")
    for cmd in task.get("setup", []):
        run(cmd, cwd=repo_dir, env=env)

    if args.validate:
        ok = validate_task(task, repo_dir, env)
        raise SystemExit(0 if ok else 1)

    if args.prepare_only:
        cmd_prepare(task, args, repo_dir, sha)
        return

    if args.backend == "cursor":
        raise SystemExit("cursor 后端预留中，当前可用：manual / claude")

    print(f"[4/5] 布置 {args.arm} 组上下文并执行（backend={args.backend}）")
    started = time.time()
    arm_setup = apply_arm(args.arm, task, repo_dir)
    if args.backend == "manual":
        report = backend_manual(task, repo_dir)
    else:
        report = backend_claude(task, repo_dir, args.arm, args.model, args.timeout)

    print("[5/5] 验证并收集结果")
    result = collect(task, repo_dir, args.arm, arm_setup, args.backend,
                     report.get("backend_report"), sha, started, env)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{result['run_id']}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果：{'PASS' if result['verify']['passed'] else 'FAIL'} "
          f"· 约定违反 {len(result['forbidden_violations'])} 处 → {out}")


if __name__ == "__main__":
    main()
