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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

from ..bootstrap import build_runtime, detect_user_id
from ..project import load_or_create_identity
from ..core.masking import mask_text
from ..core.models import KnowledgeItem
from ..scanner.code_scanner import aggregate_imports, group_by_directory, scan_code
from ..scanner.config_scanner import scan_configs
from ..scanner.conversation_scanner import scan_conversations
from ..scanner.correlation_engine import (
    correlate_commit_files,
    correlate_doc_code,
    correlate_test_code,
    summarize_correlations,
)
from ..scanner.git_analyzer import analyze_git
from ..scanner.incremental import archive_missing_signals, ingest_document, reconcile_scope
from ..scanner.profile_generator import (
    assess_doc_quality,
    assess_test_culture,
    build_profile,
    upsert_profile,
)
from ..scanner.report import BootstrapReport
from ..scanner.rule_seed_scanner import scan_rule_seeds
from ..scanner.signal_discovery import discover_signals, plan_scopes
from ..scanner.validator import DedupSet, ErrorCollector, content_hash, read_text_tolerant, validate_chunk

logger = logging.getLogger(__name__)


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
) -> None:
    """代码骨架批次写入（生成内容豁免 50 token 噪声下限，与配置摘要同口径）。
    无论写不写都记录内容哈希——供批次级 reconcile 区分「未变保留」与「旧版本」"""
    text = mask_text("\n\n".join(batch))
    kept_hashes.add(content_hash(text))
    if dedup.is_duplicate(text):
        report.duplicates_skipped += 1
        return
    await runtime.knowledge.add(KnowledgeItem(
        project_id=project_id, title="代码骨架摘要", content=text, status="pending_review",
        content_type="architecture", domain="bootstrap", tags=["signal:code"],
    ))
    report.knowledge_written += 1


async def _existing_hashes(runtime: Any, project_id: str) -> Set[str]:
    # archived 参与去重：限量溢出的条目在 force/内容微调重跑时不得重新入队
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT content FROM knowledge_items WHERE project_id = ? AND status IN ('active', 'pending_review', 'archived')",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    return {content_hash(r["content"]) for r in rows}


#: 审批队列限量优先级（值越小越优先保留在 pending_review）；
#: prohibition 仅次 convention——禁止项召回置顶/常驻注入，冷启动价值最高
_REVIEW_PRIORITY = {"convention": 0, "prohibition": 1, "architecture": 2, "faq": 3, "documentation": 4}


async def _enforce_review_cap(
    runtime: Any, project_id: str, cap: int, report: BootstrapReport
) -> None:
    """审批队列限量（bootstrap.review_queue_cap）：pending_review 超上限时，
    按 content_type 优先级 + 写入先后保留前 cap 条，溢出置 archived（可批量审批恢复）"""
    conn = await runtime.db.connect()
    async with conn.execute(
        "SELECT id, content_type, created_at FROM knowledge_items "
        "WHERE project_id = ? AND status = 'pending_review'",
        (project_id,),
    ) as cur:
        rows = await cur.fetchall()
    if len(rows) <= cap:
        return
    rows.sort(key=lambda r: (_REVIEW_PRIORITY.get(r["content_type"], 9), r["created_at"]))
    overflow = [r["id"] for r in rows[cap:]]
    now = datetime.now(timezone.utc).isoformat()
    for i in range(0, len(overflow), 500):  # SQLite 宿主变量上限 999，分批 UPDATE
        chunk = overflow[i:i + 500]
        await conn.execute(
            f"UPDATE knowledge_items SET status = 'archived', updated_at = ? "
            f"WHERE id IN ({','.join('?' for _ in chunk)})",
            [now, *chunk],
        )
    await conn.commit()
    report.review_queue_archived = len(overflow)


async def run_bootstrap(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        print(f"项目目录不存在: {project_root}", file=sys.stderr)
        return 2
    project_id = load_or_create_identity(project_root).project_id
    max_file_size = _parse_size(args.max_file_size)
    rsi_dir = project_root / ".rsi"

    # ---- 阶段一：信号发现 + 扫描计划 ----
    signals = discover_signals(project_root, max_file_size)
    plan = plan_scopes(signals, args.scope or "", consent=args.consent)
    report = BootstrapReport(
        project_root=str(project_root),
        signals={k: v.file_count for k, v in signals.items() if v.present},
        planned_scopes=plan,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(report.render_terminal())
        return 0

    collector = ErrorCollector(strict=args.strict)
    runtime = await build_runtime(project_root=project_root)
    try:
        manifest = {} if args.force else _load_manifest(rsi_dir)
        new_manifest = dict(manifest)
        dedup = DedupSet(await _existing_hashes(runtime, project_id))

        # ---- 阶段二/四：文档采集 + 知识写入（含变更收敛/复活，§10.9.10） ----
        doc_titles: Dict[str, str] = {}  # 供 doc↔code 关联推理
        if plan.get("docs"):
            for path in signals["docs"].files:
                rel = str(path.relative_to(project_root))
                try:
                    text = read_text_tolerant(path)
                    if text is None:
                        report.chunks_skipped += 1
                        continue
                    fingerprint = content_hash(text)
                    if manifest.get(rel) == fingerprint:
                        continue  # 增量：指纹未变跳过（§10.9.10 幂等）
                    stats = await ingest_document(
                        runtime, project_root, project_id, path, dedup,
                        status="pending_review", tags=["signal:docs"],
                        allow_sensitive=args.allow_sensitive, text=text,
                    )
                    if stats["title"]:
                        doc_titles[rel] = stats["title"]
                    report.knowledge_written += stats["written"]
                    report.chunks_skipped += stats["skipped"]
                    report.duplicates_skipped += stats["duplicates"]
                    report.superseded += stats["superseded"]
                    report.revived += stats["revived"]
                    new_manifest[rel] = fingerprint
                except Exception as exc:
                    collector.report(f"文档 {rel}", exc)

        # ---- 配置/规范解析 → 知识 + 画像 ----
        insights = scan_configs(
            project_root,
            signals["config"].files, signals["conventions"].files,
            signals["ci"].files, signals["code"].files,
        )
        if plan.get("config"):
            # 配置摘要是生成的结构化知识，不适用文档切片的 50 token 噪声下限（§10.9.7 针对切片）
            summary = mask_text(insights.summary_text())
            config_hashes = {content_hash(summary)}
            if not dedup.is_duplicate(summary):
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id, title="项目配置与规范摘要", content=summary, status="pending_review",
                    content_type="convention", domain="bootstrap", tags=["signal:config"],
                ))
                report.knowledge_written += 1
            # 依赖/规范演进 → 旧版本摘要收敛
            report.superseded += await reconcile_scope(
                runtime, project_id, "tags LIKE ?", ['%"signal:config"%'], config_hashes
            )

        # ---- 代码骨架（AST，P2.6）：按大小批次聚合写入（碎片合并，过 50 token 噪声下限） ----
        skeletons = []
        if plan.get("code"):
            skeletons = scan_code(project_root, signals["code"].files)
            report.code_modules = len(skeletons)
            code_hashes: Set[str] = set()
            batch: List[str] = []
            batch_chars = 0
            for skeleton in skeletons:
                batch.append(skeleton.to_text())
                batch_chars += len(skeleton.to_text())
                if batch_chars >= 3000:
                    await _write_code_batch(runtime, project_id, batch, dedup, report, code_hashes)
                    batch, batch_chars = [], 0
            if batch:
                await _write_code_batch(runtime, project_id, batch, dedup, report, code_hashes)
            if skeletons:
                # 代码演进 → 旧批次收敛（未变批次哈希在 kept 集中，不受波及）
                report.superseded += await reconcile_scope(
                    runtime, project_id, "tags LIKE ?", ['%"signal:code"%'], code_hashes
                )

        # ---- Git 历史（P2.6）：风格/贡献/归属 → 知识 + 画像 ----
        git_insights = None
        if plan.get("git"):
            git_insights = analyze_git(project_root, max_commits=args.max_commits)
            if git_insights and git_insights.total_commits:
                report.git_summary = (
                    f"{git_insights.total_commits} commits, "
                    f"{len(git_insights.authors)} 人, conventional {git_insights.conventional_ratio:.0%}"
                )
                summary = mask_text(git_insights.to_summary())
                git_hashes = {content_hash(summary)}
                if not dedup.is_duplicate(summary):
                    await runtime.knowledge.add(KnowledgeItem(
                        project_id=project_id, title="Git 历史分析", content=summary, status="pending_review",
                        content_type="architecture", domain="bootstrap", tags=["signal:git"],
                    ))
                    report.knowledge_written += 1
                # commit 持续累积 → 旧摘要收敛
                report.superseded += await reconcile_scope(
                    runtime, project_id, "tags LIKE ?", ['%"signal:git"%'], git_hashes
                )

        # ---- 对话上下文（P2.6，--consent 门控）：决策/问题模式 ----
        if plan.get("conversation"):
            patterns = scan_conversations(project_root, signals["conversation"].files)
            report.conversation_patterns = len(patterns)
            for pattern in patterns:
                result = validate_chunk(pattern.text, allow_sensitive=args.allow_sensitive)
                if not result.ok or dedup.is_duplicate(result.content):
                    continue
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id,
                    title=f"{'决策记录' if pattern.kind == 'decision' else '常见问题'}（{Path(pattern.source).name}）",
                    content=result.content,
                    status="pending_review",
                    content_type="faq",
                    domain="bootstrap",
                    tags=["signal:conversation", f"kind:{pattern.kind}"],
                    source_url=pattern.source,
                ))
                report.knowledge_written += 1

        # ---- 用户规则禁止句式 → prohibition 种子（ISSUE-6 冷启动，随 config 维度门控） ----
        if plan.get("config"):
            seed_hashes: Set[str] = set()
            for seed in scan_rule_seeds(project_root):
                seed_hashes.add(content_hash(seed.content))
                if dedup.is_duplicate(seed.content):
                    report.duplicates_skipped += 1
                    continue
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id, title=seed.title, content=seed.content,
                    status="pending_review", content_type="prohibition",
                    domain="bootstrap", tags=["signal:rules"], source_url=seed.source,
                ))
                report.knowledge_written += 1
                report.prohibition_seeds += 1
            # 规则文件改写/删除 → 旧种子收敛（规范推翻场景）
            report.superseded += await reconcile_scope(
                runtime, project_id, "tags LIKE ?", ['%"signal:rules"%'], seed_hashes
            )

        # ---- 阶段三：跨信号关联推理（§10.9.8） ----
        correlations = correlate_test_code(signals["tests"].files, signals["code"].files, project_root)
        correlations += correlate_doc_code(doc_titles, skeletons)
        correlations += correlate_commit_files(git_insights)
        if correlations:
            for c in correlations:
                report.correlations[c.kind] = report.correlations.get(c.kind, 0) + 1
            summary = mask_text(summarize_correlations(correlations))
            corr_hashes = {content_hash(summary)}
            if not dedup.is_duplicate(summary):
                await runtime.knowledge.add(KnowledgeItem(
                    project_id=project_id, title="跨信号关联图谱", content=summary, status="pending_review",
                    content_type="architecture", domain="bootstrap", tags=["signal:correlation"],
                ))
                report.knowledge_written += 1
            report.superseded += await reconcile_scope(
                runtime, project_id, "tags LIKE ?", ['%"signal:correlation"%'], corr_hashes
            )

        # ---- 初始画像（§3.8 推理规则，低于阈值留空） ----
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

        # ---- 增量收敛：信号源删除 → 知识归档（§10.9.10） ----
        current_files = {
            str(p.relative_to(project_root))
            for info in signals.values() for p in info.files
        }
        report.archived = await archive_missing_signals(runtime, project_id, manifest, current_files)

        # ---- 审批队列限量：超出 cap 的 pending_review 置 archived（ISSUE-2） ----
        cap = int(runtime.config.get("bootstrap", {}).get("review_queue_cap", 50))
        await _enforce_review_cap(runtime, project_id, cap, report)

        # 多版本文档并排 → version 冲突（只开冲突，不替用户归档）
        conflict_stats = await runtime.conflict_detector.scan(project_id)
        report.version_conflicts = int(conflict_stats.get("version_detected") or 0)

        # 收敛/归档/复活走裸 SQL（绕过 add 的变更回调），统一补触发注入重写与缓存失效
        if report.superseded or report.archived or report.revived:
            await runtime.knowledge.notify_changed(project_id)

        _save_manifest(rsi_dir, new_manifest)
        _ensure_gitignore(project_root)
        report.errors = collector.errors
        report.write_json(rsi_dir / "bootstrap_report.json")
        print(report.render_terminal())
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
