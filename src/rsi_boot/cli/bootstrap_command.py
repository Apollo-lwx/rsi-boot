"""rsi bootstrap 引导命令（§10.9）。

流程：信号发现 → 扫描计划（--dry-run 预览）→ 并行采集（docs/code AST/git/
conversation/config）→ 关联推理 → 验证/去重 → 知识写入 + 画像生成 →
manifest 指纹（幂等/增量）+ 信号删除归档 → 报告输出。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

from ..bootstrap import build_runtime, detect_user_id
from ..project import load_or_create_identity
from ..core.masking import mask_text
from ..core.models import KnowledgeItem
from ..scanner.code_scanner import aggregate_imports, scan_code
from ..scanner.config_scanner import scan_configs
from ..scanner.conflict_gate import DraftItem, gate_drafts
from ..scanner.conversation_scanner import scan_conversations
from ..scanner.correlation_engine import (
    correlate_commit_files,
    correlate_doc_code,
    correlate_test_code,
    summarize_correlations,
)
from ..scanner.document_scanner import chunk_kwargs_from_config, slice_document
from ..scanner.git_analyzer import analyze_git
from ..scanner.incremental import archive_missing_signals, ingest_document, reconcile_scope
from ..scanner.profile_generator import (
    assess_doc_quality,
    assess_test_culture,
    build_profile,
    upsert_profile,
)
from ..injector.targets import discover_user_rule_files
from ..scanner.report import BootstrapReport
from ..scanner.rule_seed_scanner import scan_rule_seeds
from ..scanner.signal_discovery import discover_signals, plan_scopes
from ..scanner.validator import DedupSet, ErrorCollector, content_hash, read_text_tolerant, validate_chunk
from .progress import Progress

_LANE_B = frozenset({"conversation", "rules"})
_REVIEW_CAP_WARN = 500
_SIGNAL_SOURCE = {
    "config": "signal:config",
    "code": "signal:code",
    "git": "signal:git",
    "correlation": "signal:correlation",
}


def _norm_src(path: str) -> str:
    return (path or "").replace("\\", "/")


def _record_applied(report: BootstrapReport, kind: str, title: str, active: bool) -> None:
    if not active:
        return
    report.applied[kind] = report.applied.get(kind, 0) + 1
    samples = report.applied_samples.setdefault(kind, [])
    if len(samples) < 15:
        samples.append(title)


def _write_status(signal: str, source_url: str, hold_sources: Set[str]) -> str:
    src = _norm_src(source_url)
    if src and src in hold_sources:
        return "pending_review"
    if signal in _LANE_B:
        return "pending_review"
    return "active"


def _run_tags(signal: str, run_id: str, extra: List[str] | None = None) -> List[str]:
    tags = [f"signal:{signal}", f"bootstrap_run_id:{run_id}"]
    if extra:
        tags.extend(extra)
    return tags


def _save_bootstrap_run(rsi_dir: Path, run_id: str) -> None:
    rsi_dir.mkdir(parents=True, exist_ok=True)
    (rsi_dir / "bootstrap_run.json").write_text(
        json.dumps({"latest": run_id}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

logger = logging.getLogger(__name__)


def _phase_names(plan: Dict[str, bool], dry_run: bool) -> List[str]:
    names = ["扫描文件树"]
    if dry_run:
        return names
    names.append("初始化数据库")
    if plan.get("docs"):
        names.append("文档")
    if plan.get("config"):
        names.append("配置")
    if plan.get("code"):
        names.append("代码")
    if plan.get("git"):
        names.append("Git")
    if plan.get("conversation"):
        names.append("对话")
    if plan.get("config"):
        names.append("规则种子")
    names.extend(["关联", "冲突检测", "写入知识", "收尾"])
    return names


def _rel_src(path: Path, project_root: Path) -> str:
    return _norm_src(str(path.relative_to(project_root)))


def _estimate_dry_run_lanes(
    signals: Dict[str, Any], plan: Dict[str, bool], project_root: Path,
) -> tuple[List[str], List[str]]:
    """文件级预估：不切片、不跑冲突启发式。规则文件归将进确认。"""
    rule_paths = {p.resolve() for p in discover_user_rule_files(project_root)}
    will_apply: List[str] = []
    will_confirm: List[str] = []
    if plan.get("docs"):
        for path in signals["docs"].files:
            if path.resolve() in rule_paths:
                continue
            will_apply.append(_rel_src(path, project_root))
    if plan.get("config"):
        for kind in ("config", "conventions", "ci"):
            info = signals.get(kind)
            if info is None:
                continue
            will_apply.extend(_rel_src(p, project_root) for p in info.files)
    if plan.get("code"):
        n = signals["code"].file_count
        if n:
            will_apply.append(f"代码骨架 ({n} 个文件)")
    if plan.get("git") and signals.get("git") and signals["git"].present:
        will_apply.append("Git 摘要")
    if plan.get("conversation"):
        will_confirm.extend(
            _rel_src(p, project_root) for p in signals["conversation"].files
        )
    if plan.get("config"):
        will_confirm.extend(_rel_src(p, project_root) for p in discover_user_rule_files(project_root))
    return will_apply, will_confirm


def _signal_summary(signals: Dict[str, Any]) -> str:
    parts: List[str] = []
    for name, info in signals.items():
        if not info.present:
            continue
        parts.append(f"{name} {info.file_count}" if info.file_count else name)
    return " · ".join(parts) or "无"


def _parse_size(text: str) -> int:
    units = {"kb": 1024, "mb": 1024 * 1024}
    text = text.strip().lower()
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return int(text[: -len(suffix)]) * factor
    return int(text)


def _load_manifest(rsi_dir: Path) -> Dict[str, str]:
    path = rsi_dir / "manifest.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_manifest(rsi_dir: Path, manifest: Dict[str, str]) -> None:
    rsi_dir.mkdir(parents=True, exist_ok=True)
    (rsi_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _ensure_gitignore(project_root: Path) -> None:
    """§8.2：.rsi/ 加入项目 .gitignore（bootstrap 时自动写入）"""
    gitignore = project_root / ".gitignore"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
        if ".rsi" not in existing:
            with gitignore.open("a", encoding="utf-8") as fh:
                fh.write("\n# RSI Boot 本地数据\n.rsi/\n")
    except OSError as exc:
        logger.warning("写入 .gitignore 失败: %s", exc)


async def _write_code_batch(
    runtime: Any, project_id: str, batch: List[str], dedup: DedupSet,
    report: BootstrapReport, kept_hashes: Set[str],
    *, status: str, tags: List[str], source_url: str,
) -> None:
    """代码骨架批次写入（生成内容豁免 50 token 噪声下限，与配置摘要同口径）。
    无论写不写都记录内容哈希——供批次级 reconcile 区分「未变保留」与「旧版本」"""
    text = mask_text("\n\n".join(batch))
    kept_hashes.add(content_hash(text))
    if dedup.is_duplicate(text):
        report.duplicates_skipped += 1
        return
    await runtime.knowledge.add(KnowledgeItem(
        project_id=project_id, title="代码骨架摘要", content=text, status=status,
        content_type="architecture", domain="bootstrap", tags=tags,
        source_url=source_url,
    ))
    report.knowledge_written += 1


async def _source_to_item_id(runtime: Any, project_id: str) -> Dict[str, str]:
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT id, source_url FROM knowledge_items "
        "WHERE project_id = ? AND source_url IS NOT NULL AND source_url != '' "
        "ORDER BY created_at",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    mapping: Dict[str, str] = {}
    for row in rows:
        key = _norm_src(row["source_url"])
        mapping.setdefault(key, row["id"])
    return mapping


async def _existing_hashes(runtime: Any, project_id: str) -> Set[str]:
    # archived 参与去重：收敛/归档条目在 force/内容微调重跑时不得重新入队
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT content FROM knowledge_items WHERE project_id = ? AND status IN ('active', 'pending_review', 'archived')",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    return {content_hash(r["content"]) for r in rows}


def _signal_from_tags(tags_raw: Any) -> str:
    try:
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
    except (TypeError, ValueError):
        tags = []
    if not isinstance(tags, list):
        tags = []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("signal:"):
            return tag.split(":", 1)[1]
    return "docs"


def _peer_title(title: str, source_url: str) -> str:
    """ingest 标题是 `{filename}# {chunk.title}`；gate 按 chunk 标题比版本家族。"""
    name = Path(source_url).name
    prefix = f"{name}# "
    if title.startswith(prefix):
        return title[len(prefix):]
    return title


async def _load_existing_drafts(
    runtime: Any, project_id: str, already: Set[str],
) -> List[DraftItem]:
    """把库中 active/pending 行当成本轮 DraftItem 同伴，供 gate 看到指纹跳过的版本对。"""
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT title, content, content_type, source_url, tags FROM knowledge_items "
        "WHERE project_id = ? AND status IN ('active', 'pending_review') "
        "AND source_url IS NOT NULL AND source_url != ''",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    peers: List[DraftItem] = []
    for row in rows:
        src = _norm_src(row["source_url"] or "")
        if not src or src in already:
            continue
        if src == "auto-extract" or src.startswith("item:"):
            continue
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
        except (TypeError, ValueError):
            tags = []
        if not isinstance(tags, list):
            tags = []
        signal = _signal_from_tags(row["tags"])
        peers.append(DraftItem(
            title=_peer_title(row["title"] or "", src),
            content=row["content"] or "",
            content_type=row["content_type"] or "documentation",
            source_url=src,
            tags=tags or [f"signal:{signal}"],
            signal=signal,
        ))
    return peers


async def _demote_held_active(
    runtime: Any, project_id: str, hold_sources: Set[str],
) -> int:
    """hold 命中且已是 active 的存量条目 → pending_review（scan 不会改状态）。"""
    hold = {_norm_src(s) for s in hold_sources if s}
    if not hold:
        return 0
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT id, source_url FROM knowledge_items "
        "WHERE project_id = ? AND status = 'active' "
        "AND source_url IS NOT NULL AND source_url != ''",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    ids = [
        r["id"] for r in rows
        if _norm_src(r["source_url"]) in hold
        and _norm_src(r["source_url"]) != "auto-extract"
        and not _norm_src(r["source_url"]).startswith("item:")
    ]
    if not ids:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    cur = await conn.execute(
        f"UPDATE knowledge_items SET status = 'pending_review', updated_at = ? "
        f"WHERE id IN ({','.join('?' for _ in ids)})",
        (now, *ids),
    )
    await conn.commit()
    return cur.rowcount


async def _enforce_review_cap(
    runtime: Any, project_id: str, cap: int, report: BootstrapReport
) -> None:
    """只统计 tags 含 bootstrap_run_id 的 pending；超过 500 打 warning，不 archived。"""
    del cap
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT id FROM knowledge_items "
        "WHERE project_id = ? AND status = 'pending_review' "
        "AND tags LIKE '%bootstrap_run_id%'",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    if len(rows) > _REVIEW_CAP_WARN:
        report.review_queue_warning = (
            f"本轮待审 {len(rows)} 条，超过 {_REVIEW_CAP_WARN}（未归档）"
        )
        logger.warning(report.review_queue_warning)


async def run_bootstrap(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        print(f"项目目录不存在: {project_root}", file=sys.stderr)
        return 2
    project_id = load_or_create_identity(project_root).project_id
    max_file_size = _parse_size(args.max_file_size)
    rsi_dir = project_root / ".rsi"
    progress = Progress()
    mode = "（预览）" if args.dry_run else ""
    progress.start(10, title=f"开始学习{mode}：{project_root}")

    # ---- 阶段一：信号发现 + 扫描计划 ----
    progress.phase("扫描文件树")
    signals = discover_signals(project_root, max_file_size, on_progress=progress.tick)
    plan = plan_scopes(signals, args.scope or "", consent=args.consent)
    progress.total_phases = len(_phase_names(plan, args.dry_run))
    progress.writeln(f"发现信号：{_signal_summary(signals)}")
    active = [name for name, on in plan.items() if on]
    progress.writeln(f"将采集：{', '.join(active) or '（无）'}")
    report = BootstrapReport(
        project_root=str(project_root),
        signals={k: v.file_count for k, v in signals.items() if v.present},
        planned_scopes=plan,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        report.will_apply, report.will_confirm = _estimate_dry_run_lanes(
            signals, plan, project_root,
        )
        progress.finish("（仅预览，未写入知识）")
        print(report.render_terminal())
        return 0

    collector = ErrorCollector(strict=args.strict)
    progress.phase("初始化数据库")
    runtime = await build_runtime(project_root=project_root)
    try:
        manifest = {} if args.force else _load_manifest(rsi_dir)
        new_manifest = dict(manifest)
        dedup = DedupSet(await _existing_hashes(runtime, project_id))
        run_id = uuid.uuid4().hex
        drafts: List[DraftItem] = []
        doc_jobs: List[Dict[str, Any]] = []

        # ---- 先切片/摘要成草稿，不写库 ----
        doc_titles: Dict[str, str] = {}
        if plan.get("docs"):
            docs = signals["docs"].files
            progress.phase("文档", total=len(docs))
            for index, path in enumerate(docs, 1):
                rel = str(path.relative_to(project_root))
                rel_n = _norm_src(rel)
                try:
                    text = read_text_tolerant(path)
                    if text is None:
                        report.chunks_skipped += 1
                        continue
                    fingerprint = content_hash(text)
                    if manifest.get(rel) == fingerprint:
                        continue
                    slice_result = slice_document(
                        path, **chunk_kwargs_from_config(runtime.config),
                    )
                    stats = report.slice_stats
                    stats["source_files"] = stats.get("source_files", 0) + 1
                    stats["chunks"] = stats.get("chunks", 0) + len(slice_result.chunks)
                    stats["merged_tiny"] = stats.get("merged_tiny", 0) + slice_result.merged_tiny
                    stats["split_large"] = stats.get("split_large", 0) + slice_result.split_large
                    stats["index_written"] = stats.get("index_written", 0) + slice_result.index_written
                    stats["skipped_tiny"] = stats.get("skipped_tiny", 0) + slice_result.skipped_tiny
                    title = None
                    for chunk in slice_result:
                        if title is None:
                            title = chunk.title
                        chunk_tags = ["signal:docs"]
                        if chunk.kind == "index":
                            chunk_tags.append("signal:doc-index")
                        drafts.append(DraftItem(
                            title=chunk.title, content=chunk.content,
                            content_type="documentation", source_url=rel_n,
                            tags=chunk_tags, signal="docs",
                        ))
                    if title:
                        doc_titles[rel] = title
                    doc_jobs.append({"path": path, "rel": rel, "text": text, "fp": fingerprint})
                except Exception as exc:
                    collector.report(f"文档 {rel}", exc)
                progress.tick(index)

        if plan.get("config"):
            progress.phase("配置")
        insights = scan_configs(
            project_root,
            signals["config"].files, signals["conventions"].files,
            signals["ci"].files, signals["code"].files,
        )
        config_summary = None
        config_src = _SIGNAL_SOURCE["config"]
        if plan.get("config"):
            config_summary = mask_text(insights.summary_text())
            drafts.append(DraftItem(
                title="项目配置与规范摘要", content=config_summary,
                content_type="convention", source_url=config_src,
                tags=["signal:config"], signal="config",
            ))

        skeletons = []
        code_batches: List[List[str]] = []
        if plan.get("code"):
            progress.phase("代码", total=len(signals["code"].files))
            skeletons = scan_code(
                project_root, signals["code"].files,
                on_progress=lambda done, _total: progress.tick(done),
            )
            report.code_modules = len(skeletons)
            batch: List[str] = []
            batch_chars = 0
            for skeleton in skeletons:
                batch.append(skeleton.to_text())
                batch_chars += len(skeleton.to_text())
                if batch_chars >= 3000:
                    code_batches.append(batch)
                    drafts.append(DraftItem(
                        title="代码骨架摘要", content="\n\n".join(batch),
                        content_type="architecture", source_url=_SIGNAL_SOURCE["code"],
                        tags=["signal:code"], signal="code",
                    ))
                    batch, batch_chars = [], 0
            if batch:
                code_batches.append(batch)
                drafts.append(DraftItem(
                    title="代码骨架摘要", content="\n\n".join(batch),
                    content_type="architecture", source_url=_SIGNAL_SOURCE["code"],
                    tags=["signal:code"], signal="code",
                ))

        git_insights = None
        git_summary = None
        if plan.get("git"):
            progress.phase(f"Git（最多 {args.max_commits} 次提交）")
            git_insights = analyze_git(project_root, max_commits=args.max_commits)
            if git_insights and git_insights.total_commits:
                report.git_summary = (
                    f"{git_insights.total_commits} commits, "
                    f"{len(git_insights.authors)} 人, conventional {git_insights.conventional_ratio:.0%}"
                )
                git_summary = mask_text(git_insights.to_summary())
                drafts.append(DraftItem(
                    title="Git 历史分析", content=git_summary,
                    content_type="architecture", source_url=_SIGNAL_SOURCE["git"],
                    tags=["signal:git"], signal="git",
                ))

        convo_drafts: List[DraftItem] = []
        if plan.get("conversation"):
            progress.phase("对话", total=max(len(signals["conversation"].files), 1))
            patterns = scan_conversations(project_root, signals["conversation"].files)
            progress.tick(len(signals["conversation"].files) or 1)
            report.conversation_patterns = len(patterns)
            for pattern in patterns:
                result = validate_chunk(pattern.text, allow_sensitive=args.allow_sensitive)
                if not result.ok:
                    continue
                item = DraftItem(
                    title=f"{'决策记录' if pattern.kind == 'decision' else '常见问题'}（{Path(pattern.source).name}）",
                    content=result.content, content_type="faq",
                    source_url=pattern.source,
                    tags=["signal:conversation", f"kind:{pattern.kind}"],
                    signal="conversation",
                )
                drafts.append(item)
                convo_drafts.append(item)

        rule_drafts: List[DraftItem] = []
        if plan.get("config"):
            progress.phase("规则种子")
            for seed in scan_rule_seeds(project_root):
                item = DraftItem(
                    title=seed.title, content=seed.content,
                    content_type="prohibition", source_url=seed.source,
                    tags=["signal:rules"], signal="rules",
                )
                drafts.append(item)
                rule_drafts.append(item)

        progress.phase("关联")
        correlations = correlate_test_code(signals["tests"].files, signals["code"].files, project_root)
        correlations += correlate_doc_code(doc_titles, skeletons)
        correlations += correlate_commit_files(git_insights)
        corr_summary = None
        if correlations:
            for c in correlations:
                report.correlations[c.kind] = report.correlations.get(c.kind, 0) + 1
            corr_summary = mask_text(summarize_correlations(correlations))
            drafts.append(DraftItem(
                title="跨信号关联图谱", content=corr_summary,
                content_type="architecture", source_url=_SIGNAL_SOURCE["correlation"],
                tags=["signal:correlation"], signal="correlation",
            ))

        already = {_norm_src(d.source_url) for d in drafts if d.source_url}
        drafts.extend(await _load_existing_drafts(runtime, project_id, already))
        progress.phase("冲突检测", total=max(len(drafts) * 2, 1))
        gate = gate_drafts(
            drafts, skeletons, project_root, on_progress=progress.tick,
        )
        hold = {_norm_src(s) for s in gate.hold_sources}
        report.extracts = [
            {"title": item.title, "source": item.source_url}
            for item in convo_drafts + rule_drafts
        ]
        report.conflicts = [
            {
                "type": c.conflict_type,
                "left": c.left_source,
                "right": c.right_source,
                "reason": c.reason,
            }
            for c in gate.conflicts
        ]

        # ---- 过闸后再写库 ----
        write_total = (
            len(doc_jobs) + (1 if config_summary is not None else 0)
            + len(code_batches) + (1 if git_summary is not None else 0)
            + len(convo_drafts) + len(rule_drafts)
            + (1 if corr_summary is not None else 0)
        )
        progress.phase("写入知识", total=max(write_total, 1))
        write_done = 0
        for job in doc_jobs:
            status = _write_status("docs", job["rel"], hold)
            active = status == "active"
            try:
                stats = await ingest_document(
                    runtime, project_root, project_id, job["path"], dedup,
                    status=status, tags=_run_tags("docs", run_id),
                    allow_sensitive=args.allow_sensitive, text=job["text"],
                    require_run_tag_to_revive=True,
                )
                if active and stats["written"]:
                    sample = f"{Path(job['rel']).name}# {doc_titles.get(job['rel'], stats.get('title') or job['rel'])}"
                    report.applied["docs"] = report.applied.get("docs", 0) + stats["written"]
                    samples = report.applied_samples.setdefault("docs", [])
                    if len(samples) < 15:
                        samples.append(sample)
                report.knowledge_written += stats["written"]
                report.chunks_skipped += stats["skipped"]
                report.duplicates_skipped += stats["duplicates"]
                report.superseded += stats["superseded"]
                report.revived += stats["revived"]
                blocked = int(stats.get("blocked_untagged_archive") or 0)
                report.blocked_untagged_archive += blocked
                if blocked and not stats["written"] and not stats["revived"]:
                    continue
                new_manifest[job["rel"]] = job["fp"]
            except Exception as exc:
                collector.report(f"文档 {job['rel']}", exc)
            write_done += 1
            progress.tick(write_done)

        if config_summary is not None:
            config_hashes = {content_hash(config_summary)}
            config_status = _write_status("config", config_src, hold)
            if not dedup.is_duplicate(config_summary):
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id, title="项目配置与规范摘要",
                    content=config_summary,
                    status=config_status,
                    content_type="convention", domain="bootstrap",
                    tags=_run_tags("config", run_id),
                    source_url=config_src,
                ))
                report.knowledge_written += 1
                _record_applied(report, "config", "项目配置与规范摘要", config_status == "active")
            report.superseded += await reconcile_scope(
                runtime, project_id, "tags LIKE ?", ['%"signal:config"%'], config_hashes
            )
            write_done += 1
            progress.tick(write_done)

        if code_batches:
            code_src = _SIGNAL_SOURCE["code"]
            code_hashes: Set[str] = set()
            code_status = _write_status("code", code_src, hold)
            code_tags = _run_tags("code", run_id)
            wrote_code = 0
            for batch in code_batches:
                before = report.knowledge_written
                await _write_code_batch(
                    runtime, project_id, batch, dedup, report, code_hashes,
                    status=code_status, tags=code_tags, source_url=code_src,
                )
                wrote_code += report.knowledge_written - before
                write_done += 1
                progress.tick(write_done)
            if wrote_code and code_status == "active":
                report.applied["code"] = report.applied.get("code", 0) + wrote_code
                samples = report.applied_samples.setdefault("code", [])
                if len(samples) < 15:
                    samples.append("代码骨架摘要")
            report.superseded += await reconcile_scope(
                runtime, project_id, "tags LIKE ?", ['%"signal:code"%'], code_hashes
            )

        if git_summary is not None:
            git_src = _SIGNAL_SOURCE["git"]
            git_hashes = {content_hash(git_summary)}
            git_status = _write_status("git", git_src, hold)
            if not dedup.is_duplicate(git_summary):
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id, title="Git 历史分析", content=git_summary,
                    status=git_status,
                    content_type="architecture", domain="bootstrap",
                    tags=_run_tags("git", run_id),
                    source_url=git_src,
                ))
                report.knowledge_written += 1
                _record_applied(report, "git", "Git 历史分析", git_status == "active")
            report.superseded += await reconcile_scope(
                runtime, project_id, "tags LIKE ?", ['%"signal:git"%'], git_hashes
            )
            write_done += 1
            progress.tick(write_done)

        for item in convo_drafts:
            if not dedup.is_duplicate(item.content):
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id, title=item.title, content=item.content,
                    status=_write_status("conversation", item.source_url, hold),
                    content_type=item.content_type, domain="bootstrap",
                    tags=_run_tags("conversation", run_id, [t for t in item.tags if t.startswith("kind:")]),
                    source_url=item.source_url,
                ))
                report.knowledge_written += 1
            write_done += 1
            progress.tick(write_done)

        seed_hashes: Set[str] = set()
        for item in rule_drafts:
            seed_hashes.add(content_hash(item.content))
            if dedup.is_duplicate(item.content):
                report.duplicates_skipped += 1
            else:
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id, title=item.title, content=item.content,
                    status=_write_status("rules", item.source_url, hold),
                    content_type="prohibition", domain="bootstrap",
                    tags=_run_tags("rules", run_id), source_url=item.source_url,
                ))
                report.knowledge_written += 1
                report.prohibition_seeds += 1
            write_done += 1
            progress.tick(write_done)
        if rule_drafts or plan.get("config"):
            report.superseded += await reconcile_scope(
                runtime, project_id, "tags LIKE ?", ['%"signal:rules"%'], seed_hashes
            )

        if corr_summary is not None:
            corr_src = _SIGNAL_SOURCE["correlation"]
            corr_hashes = {content_hash(corr_summary)}
            corr_status = _write_status("correlation", corr_src, hold)
            if not dedup.is_duplicate(corr_summary):
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id, title="跨信号关联图谱", content=corr_summary,
                    status=corr_status,
                    content_type="architecture", domain="bootstrap",
                    tags=_run_tags("correlation", run_id),
                    source_url=corr_src,
                ))
                report.knowledge_written += 1
                _record_applied(report, "correlation", "跨信号关联图谱", corr_status == "active")
            report.superseded += await reconcile_scope(
                runtime, project_id, "tags LIKE ?", ['%"signal:correlation"%'], corr_hashes
            )
            write_done += 1
            progress.tick(write_done)

        if gate.conflicts:
            mapping = await _source_to_item_id(runtime, project_id)
            persist_n = await runtime.conflict_detector.persist_knowledge_conflicts(
                project_id, gate.conflicts, mapping,
            )
            report.version_conflicts += persist_n

        await _demote_held_active(runtime, project_id, hold)
        await runtime.conflict_detector.scan(project_id)

        _save_bootstrap_run(rsi_dir, run_id)

        doc_quality = assess_doc_quality(signals["docs"].files, project_root)
        test_culture = assess_test_culture(signals["tests"].files, signals["code"].files)
        profile = build_profile(
            detect_user_id(), project_id, insights, doc_quality, test_culture,
            git=git_insights, import_counter=aggregate_imports(skeletons),
        )
        await upsert_profile(runtime.db, profile)
        report.profile_summary = {
            "language": insights.language or "", "framework": insights.framework or "",
            "test_framework": insights.test_framework or "", "doc_quality": doc_quality,
            "test_culture": test_culture,
            "expertise": ",".join(profile.expertise[:5]),
        }

        progress.phase("收尾")
        current_files = {
            str(p.relative_to(project_root))
            for info in signals.values() for p in info.files
        }
        report.archived = await archive_missing_signals(runtime, project_id, manifest, current_files)

        cap = int(runtime.config.get("bootstrap", {}).get("review_queue_cap", 50))
        await _enforce_review_cap(runtime, project_id, cap, report)

        if report.superseded or report.archived or report.revived:
            await runtime.knowledge.notify_changed(project_id)

        if args.force and report.blocked_untagged_archive:
            report.wipe_hint = (
                "未复活无 bootstrap_run_id 的归档。"
                "清库重学请删除 .rsi/rsi.db* 与 manifest.json 后再跑 rsi bootstrap。"
            )

        _save_manifest(rsi_dir, new_manifest)
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
        from ..api.mcp_server import serve
        import asyncio

        runtime = await build_runtime(project_root=project_root)
        try:
            await serve(runtime, runtime.logs, runtime.feedback_secret)
        finally:
            await runtime.close()
    return 0
