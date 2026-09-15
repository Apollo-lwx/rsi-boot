"""rsi bootstrap：采集阅读包，不把原文当知识入库。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..bootstrap import build_runtime, detect_user_id
from ..project import load_or_create_identity
from ..scanner.code_scanner import aggregate_imports, scan_code
from ..scanner.config_scanner import scan_configs
from ..scanner.git_analyzer import analyze_git
from ..scanner.git_fix import list_fix_commits
from ..scanner.profile_generator import (
    assess_doc_quality,
    assess_test_culture,
    build_profile,
)
from ..injector.targets import discover_user_rule_files
from ..memory.harvest import is_harvest_doc
from ..scanner import reading_packs as reading_packs_mod
from ..scanner.reading_packs import build_packs, unlink_host_judge_queue, write_packs
from ..scanner.report import BootstrapReport
from ..scanner.signal_discovery import (
    EXCLUDED_DIRS,
    discover_signals,
    parse_include_dirs,
    plan_scopes,
    should_skip_dir,
)
from ..scanner.version_conflict import detect_version_families
from ..scanner.validator import ErrorCollector
from ..ux.lang import locale_lang
from ..ux.messages import t
from .progress import Progress

logger = logging.getLogger(__name__)

_H1 = re.compile(r"^#\s+(.+)", re.MULTILINE)
_EXCLUDED_CF = frozenset(name.casefold() for name in EXCLUDED_DIRS)


def _norm_src(path: str) -> str:
    return (path or "").replace("\\", "/")


def _rel_src(path: Path, project_root: Path) -> str:
    return _norm_src(str(path.relative_to(project_root)))


def _save_bootstrap_run(rsi_dir: Path, run_id: str) -> None:
    rsi_dir.mkdir(parents=True, exist_ok=True)
    (rsi_dir / "bootstrap_run.json").write_text(
        json.dumps({"latest": run_id}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _phase_names(plan: Dict[str, bool], dry_run: bool, lang: str) -> List[str]:
    del lang
    names = ["扫描文件树"]
    if dry_run:
        return names
    if plan.get("docs"):
        names.append("文档索引")
    if plan.get("code"):
        names.append("代码索引")
    if plan.get("git"):
        names.append("Git fix")
    if plan.get("config") or plan.get("conversation"):
        names.append("规则与技能")
    names.append("分包")
    return names


def _signal_summary(signals: Dict[str, Any]) -> str:
    parts: List[str] = []
    for name, info in signals.items():
        if not info.present:
            continue
        if name == "git" and info.present:
            parts.append(f"{name} {info.file_count}")
        elif info.file_count:
            parts.append(f"{name} {info.file_count}")
        else:
            parts.append(name)
    return " · ".join(parts) or "无"


def _parse_size(text: str) -> int:
    units = {"kb": 1024, "mb": 1024 * 1024}
    text = text.strip().lower()
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return int(text[: -len(suffix)]) * factor
    return int(text)


def _ensure_gitignore(project_root: Path) -> None:
    gitignore = project_root / ".gitignore"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
        if ".rsi" not in existing:
            with gitignore.open("a", encoding="utf-8") as fh:
                fh.write("\n# RSI Boot 本地数据\n.rsi/\n")
    except OSError as exc:
        logger.warning("写入 .gitignore 失败: %s", exc)


def _first_h1(path: Path) -> str:
    try:
        text = path.read_bytes()[:2048].decode("utf-8", errors="replace")
    except OSError:
        return ""
    match = _H1.search(text)
    return match.group(1).strip() if match else ""


def _iter_skill_md(project_root: Path) -> List[Path]:
    found: List[Path] = []
    root = Path(project_root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_parent = Path(dirpath).relative_to(root)
        keep: List[str] = []
        for name in dirnames:
            child = name if str(rel_parent) == "." else (rel_parent / name).as_posix()
            if should_skip_dir(name, child, ()):
                continue
            if name.casefold() in _EXCLUDED_CF:
                continue
            keep.append(name)
        dirnames[:] = keep
        if "SKILL.md" in filenames:
            found.append(Path(dirpath) / "SKILL.md")
    return found


def _version_hints(project_root: Path) -> Dict[str, str]:
    hints: Dict[str, str] = {}
    for family in detect_version_families(project_root):
        hints[family.legacy_rel] = "version-family"
        hints[family.current_rel] = "version-family"
    return hints


def _collect_pack_inputs(
    project_root: Path,
    signals: Dict[str, Any],
    plan: Dict[str, bool],
    args: argparse.Namespace,
    progress: Progress,
    collector: ErrorCollector,
) -> tuple[dict[str, list[dict]], Any, list, Any]:
    docs: List[dict] = []
    code: List[dict] = []
    git_fix: List[dict] = []
    rules: List[dict] = []
    skills: List[dict] = []
    conversations: List[dict] = []
    skeletons: list = []
    git_insights = None

    if plan.get("docs"):
        files = list(signals["docs"].files)
        progress.phase("文档索引", total=max(len(files), 1))
        for index, path in enumerate(files, 1):
            rel = _rel_src(path, project_root)
            try:
                heading = _first_h1(path)
            except Exception as exc:
                collector.report(f"文档 {rel}", exc)
                heading = ""
            item: dict[str, Any] = {"path": rel}
            if heading:
                item["heading"] = heading
            docs.append(item)
            progress.tick(index)

    insights = scan_configs(
        project_root,
        signals["config"].files, signals["conventions"].files,
        signals["ci"].files, signals["code"].files,
    )

    if plan.get("code"):
        files = list(signals["code"].files)
        progress.phase("代码索引", total=max(len(files), 1))
        try:
            skeletons = scan_code(
                project_root, files,
                on_progress=lambda done, _total: progress.tick(done),
            )
        except Exception as exc:
            collector.report("代码索引", exc)
            skeletons = []
        by_rel = {_norm_src(s.rel_path): s for s in skeletons}
        for path in files:
            rel = _rel_src(path, project_root)
            item = {"path": rel}
            sk = by_rel.get(rel)
            if sk and sk.symbols:
                item["heading"] = str(sk.symbols[0])[:80]
            code.append(item)
        if not files:
            progress.tick(1)

    if plan.get("git"):
        progress.phase("Git fix")
        git_insights = analyze_git(project_root, max_commits=args.max_commits)
        try:
            git_fix = list_fix_commits(project_root, max_commits=args.max_commits)
        except Exception as exc:
            collector.report("Git fix", exc)
            git_fix = []
        n = git_insights.total_commits if git_insights else 0
        if n:
            progress.retarget(f"Git fix（{n} 次提交）", total=n)
            progress.tick(n)
        else:
            progress.retarget("Git fix（无提交）")

    if plan.get("config") or plan.get("conversation"):
        progress.phase("规则与技能")
        if plan.get("config"):
            for path in discover_user_rule_files(project_root):
                rel = _rel_src(path, project_root)
                item = {"path": rel}
                heading = _first_h1(path)
                if heading:
                    item["heading"] = heading
                rules.append(item)
            for path in _iter_skill_md(project_root):
                rel = _rel_src(path, project_root)
                skills.append({"path": rel, "name": path.parent.name})
        if plan.get("conversation"):
            for path in signals["conversation"].files:
                conversations.append({"path": _rel_src(path, project_root)})

    return (
        {
            "docs": docs,
            "code": code,
            "git_fix": git_fix,
            "rules": rules,
            "skills": skills,
            "conversations": conversations,
        },
        git_insights,
        skeletons,
        insights,
    )


def _validate_judge_flags(args: argparse.Namespace) -> Optional[str]:
    if getattr(args, "dry_run", False):
        return None
    host = bool(getattr(args, "host_judge", False))
    local = bool(getattr(args, "local_judge", False))
    if host and local:
        return "不能同时使用 --host-judge 与 --local-judge"
    if not host and not local:
        return "必须指定 --host-judge 或 --local-judge"
    return None


async def run_bootstrap(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        print(f"项目目录不存在: {project_root}", file=sys.stderr)
        return 2
    judge_error = _validate_judge_flags(args)
    if judge_error:
        print(judge_error, file=sys.stderr)
        return 2
    judge = "host" if getattr(args, "host_judge", False) else "local"
    project_id = load_or_create_identity(project_root).project_id
    max_file_size = _parse_size(args.max_file_size)
    rsi_dir = project_root / ".rsi"
    lang = getattr(args, "lang", None) or locale_lang()
    progress = Progress()
    mode = "（预览）" if args.dry_run else ""
    progress.start(10, title=f"开始学习{mode}：{project_root}")

    progress.phase("扫描文件树")
    include = parse_include_dirs(getattr(args, "include", None))
    signals = discover_signals(
        project_root, max_file_size, on_progress=progress.tick, include=include,
    )
    plan = plan_scopes(signals, args.scope or "", consent=args.consent)
    progress.total_phases = len(_phase_names(plan, args.dry_run, lang))
    progress.writeln(f"发现信号：{_signal_summary(signals)}")
    if include:
        progress.writeln("加回目录：" + ", ".join(include))
    active = [name for name, on in plan.items() if on]
    progress.writeln(f"将采集：{', '.join(active) or '（无）'}")
    report = BootstrapReport(
        project_root=str(project_root),
        signals={k: v.file_count for k, v in signals.items() if v.present},
        planned_scopes=plan,
        dry_run=args.dry_run,
    )

    collector = ErrorCollector(strict=args.strict)
    sources, git_insights, skeletons, insights = _collect_pack_inputs(
        project_root, signals, plan, args, progress, collector,
    )
    progress.phase("分包")
    packs, omitted = build_packs(
        docs=sources["docs"],
        code=sources["code"],
        git_fix=sources["git_fix"],
        rules=sources["rules"],
        skills=sources["skills"],
        conversations=sources["conversations"],
        version_hints=_version_hints(project_root),
    )
    report.pack_count = len(packs)
    report.pack_omitted_sources = omitted[:200]
    report.will_apply = [f"{p.get('domain', '')} ({p.get('id', '')})" for p in packs]
    domains = sorted({str(p.get("domain") or "") for p in packs if p.get("domain")})
    if args.dry_run:
        progress.finish("（仅预览，未写入阅读包）")
        print(f"将生成阅读包 {len(packs)} 个：{', '.join(domains) or '（无）'}")
        print(report.render_terminal())
        return 0

    progress.phase(t("BOOTSTRAP_PHASE_INIT", lang))
    runtime = await build_runtime(project_root=project_root)
    try:
        run_id = uuid.uuid4().hex
        report.knowledge_written = 0
        report.judge = judge
        try:
            write_packs(
                rsi_dir,
                bootstrap_run_id=run_id,
                packs=packs,
                force=bool(args.force),
            )
        except OSError as exc:
            print(f"写入阅读包失败: {exc}", file=sys.stderr)
            return 1

        unlink_host_judge_queue(rsi_dir)
        leftover = sum(1 for doc in runtime.store.list_official() if is_harvest_doc(doc))
        if leftover:
            report.harvest_warning = f"库存仍有 {leftover} 条旧采集物"
        write_relearn = getattr(reading_packs_mod, "write_relearn_skill", None)
        if callable(write_relearn):
            try:
                write_relearn(runtime.store)
            except Exception as exc:
                logger.warning("写入 rsi-relearn 技能失败: %s", exc)

        if judge != "local":
            await runtime.conflict_detector.scan(project_id)

        _save_bootstrap_run(rsi_dir, run_id)
        doc_quality = assess_doc_quality(signals["docs"].files, project_root)
        test_culture = assess_test_culture(signals["tests"].files, signals["code"].files)
        profile = build_profile(
            detect_user_id(), project_id, insights, doc_quality, test_culture,
            git=git_insights, import_counter=aggregate_imports(skeletons),
        )
        await runtime.profiles.upsert(profile)
        report.profile_summary = {
            "language": insights.language or "", "framework": insights.framework or "",
            "test_framework": insights.test_framework or "", "doc_quality": doc_quality,
            "test_culture": test_culture,
            "expertise": ",".join(profile.expertise[:5]),
        }
        if git_insights and git_insights.total_commits:
            report.git_summary = (
                f"{git_insights.total_commits} commits, "
                f"{len(git_insights.authors)} 人, conventional {git_insights.conventional_ratio:.0%}"
            )
        report.code_modules = len(skeletons)

        _ensure_gitignore(project_root)
        report.errors = collector.errors
        md_path = rsi_dir / "bootstrap_report.md"
        report.write_json(rsi_dir / "bootstrap_report.json")
        report.write_markdown(md_path)
        progress.finish()
        print(report.render_terminal())
        print(f"Markdown 报告: {md_path}")
        print(f"JSON 报告: {rsi_dir / 'bootstrap_report.json'}")
    finally:
        await runtime.close()

    if args.then_start:
        from ..__main__ import run_serve

        return await run_serve(project_root, watch=False)
    return 0
