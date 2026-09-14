"""rsi CLI（§7）：init / serve / query / knowledge 子命令。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .bootstrap import build_runtime, detect_user_id, rsi_home
from .common.logger import setup_cli_logging, setup_logging
from .common.stdio import ensure_utf8_stdio
from .core.models import KnowledgeItem
from .project import resolve_project_root
from .ux.lang import locale_lang
from .ux.messages import t


def _print_json(payload: object) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def cmd_init(args: argparse.Namespace) -> int:
    setup_cli_logging(getattr(args, "verbose", False))
    home = rsi_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "skills").mkdir(exist_ok=True)

    config_file = home / "config.yaml"
    if not config_file.exists():
        config_file.write_text(
            "# RSI Boot 用户级配置（合并于包内 default.yaml 之上，§2.5）\n"
            "# v3.0 主链路零 API Key；以下增强项按需开启（Spec v3.0 附录 C）：\n"
            "# enhance:\n"
            "#   embedding: true        # 混合检索\n"
            "#   extract_llm: true      # LLM 知识提取\n"
            "#   model:\n"
            "#     api_key: env:OPENAI_API_KEY\n",
            encoding="utf-8",
        )

    print(f"初始化完成：{home}（用户配置）。项目记忆在各工作目录 .rsi/，首次 serve/bootstrap 时创建")
    return 0


def on_memory_yaml_change(runtime: object, _path: Path) -> None:
    """Official-dir YAML change: rebuild index and rewrite injector (same as serve startup)."""
    runtime.invalidate_index()
    rewrite = runtime.injector.rewrite(runtime.project_id or "")
    if asyncio.iscoroutine(rewrite) or asyncio.isfuture(rewrite):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(rewrite)
        else:
            loop.create_task(rewrite)


async def run_serve(project_root: Path, *, watch: bool = False) -> int:
    """File-only serve: startup inject, optional YAML watch, no sqlite."""
    from .api.mcp_server import serve
    from .memory.watch import YamlWatcher
    from .scheduler.manager import SchedulerManager

    logging.getLogger(__name__).info("工作区 %s → %s", project_root, project_root / ".rsi")
    runtime = await build_runtime(project_root=project_root)
    await runtime.injector.rewrite(runtime.project_id or "")
    archive_dir = project_root / ".rsi" / "archive"
    scheduler = SchedulerManager(
        archive_dir, store=runtime.store, profiles=runtime.profiles,
        extractor_provider=lambda: runtime.extractor,
        proposal_engine_provider=lambda: runtime.proposal_engine,
        conflict_detector_provider=lambda: runtime.conflict_detector,
        project_ids=[runtime.project_id] if runtime.project_id else [],
    )
    scheduler.start()
    config_watch = asyncio.create_task(runtime.watcher.watch_loop())
    yaml_task = None
    if watch:
        watcher = YamlWatcher(
            project_root / ".rsi" / "memory",
            on_change=lambda path: on_memory_yaml_change(runtime, path),
        )
        yaml_task = asyncio.create_task(watcher.watch_loop())
    try:
        await serve(runtime, runtime.logs, runtime.feedback_secret)
    finally:
        config_watch.cancel()
        if yaml_task is not None:
            yaml_task.cancel()
        await scheduler.stop()
        await runtime.close()
    return 0


async def cmd_serve(args: argparse.Namespace) -> int:
    setup_logging("INFO" if getattr(args, "verbose", False) else "WARNING")
    explicit = Path(args.project_root) if getattr(args, "project_root", None) else None
    project_root = resolve_project_root(explicit=explicit)
    return await run_serve(project_root, watch=bool(getattr(args, "watch", False)))


async def cmd_recall(args: argparse.Namespace) -> int:
    """调试：执行一次记忆召回（v3.0 起替代原 query 冒烟——主链路无生成环节）"""
    setup_cli_logging(getattr(args, "verbose", False))
    runtime = await build_runtime(project_root=resolve_project_root())
    try:
        payload = await runtime.recall.recall(
            args.task, runtime.project_id or args.project,
            user_id=detect_user_id(), role=args.role,
        )
        _print_json(payload)
        return 0
    finally:
        await runtime.close()


async def cmd_migrate(args: argparse.Namespace) -> int:
    """把旧版 ~/.rsi/rsi.db 里的命名空间拆进当前项目库（default 须显式认领）"""
    setup_cli_logging(getattr(args, "verbose", False))
    from .project import legacy_shared_db_path
    from .services.legacy_migrate import adopt_namespaces, list_legacy_namespaces

    if args.migrate_action == "status":
        ns = await list_legacy_namespaces()
        _print_json({"legacy_db": str(legacy_shared_db_path()), "namespaces": ns})
        return 0
    if args.migrate_action == "adopt":
        from .data.migrate import migrate
        from .data.sqlite import SQLiteClient
        from .project import load_or_create_identity, project_db_path

        root = resolve_project_root(explicit=Path(args.project_root))
        identity = load_or_create_identity(root)
        if not identity.project_id:
            print("无法解析当前项目身份", file=sys.stderr)
            return 2
        db = SQLiteClient(project_db_path(root))
        await migrate(db)
        try:
            rows = await adopt_namespaces(
                legacy_shared_db_path(), db, [args.namespace], identity.project_id,
            )
            _print_json({
                "adopted": args.namespace,
                "dest_project_id": identity.project_id,
                "dest_db": str(db.db_path),
                "rows": rows,
            })
            from .ux.lang import locale_lang
            from .ux.messages import t

            print(t("MIGRATE_ADOPT_DEPRECATED", locale_lang()), file=sys.stderr)
            return 0
        finally:
            await db.close()
    print("未知 migrate 子命令", file=sys.stderr)
    return 2


async def cmd_knowledge(args: argparse.Namespace) -> int:
    setup_cli_logging(getattr(args, "verbose", False))
    runtime = await build_runtime(project_root=resolve_project_root())
    try:
        project = runtime.project_id or args.project
        if args.knowledge_action == "add":
            content = args.content
            if args.file:
                content = Path(args.file).read_text(encoding="utf-8")
            if not content:
                print("knowledge add 需要 --content 或 --file", file=sys.stderr)
                return 2
            item = KnowledgeItem(
                project_id=project, title=args.title, content=content,
                content_type=args.type,
                tags=[t for t in (args.tags or "").split(",") if t],
                roles=[r for r in (args.roles or "").split(",") if r],
            )
            item_id = await runtime.knowledge.add(item)
            print(f"已添加知识条目：{item_id}")
            return 0
        if args.knowledge_action == "list":
            _print_json(await runtime.knowledge.list(project))
            return 0
        if args.knowledge_action == "delete":
            ok = await runtime.knowledge.delete(args.id, project)
            print("已删除" if ok else "未找到该条目")
            return 0 if ok else 1
        if args.knowledge_action == "accept":
            from .cli.knowledge_accept import run_knowledge_accept

            return await run_knowledge_accept(
                runtime,
                run_id=getattr(args, "run", None),
                reject=bool(getattr(args, "reject", False)),
                conflicts=getattr(args, "conflicts", None),
            )
        print("未知 knowledge 子命令", file=sys.stderr)
        return 2
    finally:
        await runtime.close()


def _add_verbose(p: argparse.ArgumentParser) -> None:
    p.add_argument("--verbose", action="store_true",
                   help="恢复 INFO 级日志并走 stderr（默认 WARNING + stdout，避免 PowerShell 红块）")


def build_parser() -> argparse.ArgumentParser:
    loc = locale_lang()
    parser = argparse.ArgumentParser(prog="rsi", description="RSI Boot：个人本地 MCP 智能助手")
    parser.add_argument("--lang", choices=["zh", "en"], default=None, help=t("CLI_LANG_HELP", loc))
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化 ~/.rsi 用户配置目录")
    _add_verbose(p_init)
    p_serve = sub.add_parser("serve", help="启动 MCP stdio server")
    p_serve.add_argument(
        "--project-root", default=None,
        help="工作区根（全局 MCP 请用环境变量 RSI_PROJECT_ROOT；缺省读 WORKSPACE_FOLDER_PATHS / cwd）",
    )
    p_serve.add_argument("--watch", action="store_true", help="文件监听静默学习（§10.9.5）")
    _add_verbose(p_serve)  # 默认 WARNING；--verbose 才 INFO（Cursor 把 stderr INFO 标成 error）

    p_boot = sub.add_parser("bootstrap", help="项目自学习：扫描信号源生成知识与画像（§10.9）")
    p_boot.add_argument("--scope", default="", help="逗号分隔：docs,code,git,conversation,config")
    p_boot.add_argument("--project-root", default=".", help="项目根目录，默认当前目录")
    p_boot.add_argument("--dry-run", action="store_true", help="预览扫描计划，不写入")
    p_boot.add_argument("--consent", action="store_true", help="显式同意扫描对话上下文")
    p_boot.add_argument("--then-start", action="store_true", help="学习完成后启动服务")
    p_boot.add_argument("--force", action="store_true", help="忽略 manifest 指纹全量重学")
    p_boot.add_argument("--strict", action="store_true", help="严格模式：任何验证失败即中断")
    p_boot.add_argument("--allow-sensitive", action="store_true", help="敏感内容脱敏后写入（默认跳过）")
    p_boot.add_argument("--max-file-size", default="1MB", help="单文件大小上限，如 1MB")
    p_boot.add_argument("--max-commits", type=int, default=500, help="Git 历史上限（P2.6 生效）")
    p_boot.add_argument(
        "--include", action="append", default=[],
        help="把默认排除的目录加回学习，可重复或逗号分隔；"
             "按目录名匹配（artifacts 会加回任意名为 artifacts 的文件夹）",
    )
    p_boot.add_argument("--host-judge", action="store_true",
                        help="对话内由宿主模型裁决冲突工作包（agent 用）")
    p_boot.add_argument("--local-judge", action="store_true",
                        help="本地整条知识比对裁决冲突（人类 CLI 用）")
    _add_verbose(p_boot)

    p_recall = sub.add_parser("recall", help="调试：执行一次记忆召回（禁止项置顶 + 相关经验）")
    p_recall.add_argument("task", help="任务描述")
    p_recall.add_argument("--project", default=None, help="已废弃：由当前目录绑定")
    p_recall.add_argument("--role", default=None, help="调用方角色")
    _add_verbose(p_recall)

    p_kn = sub.add_parser("knowledge", help="知识库管理")
    _add_verbose(p_kn)
    kn_sub = p_kn.add_subparsers(dest="knowledge_action", required=True)
    p_add = kn_sub.add_parser("add", help="添加知识条目")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--content", default=None)
    p_add.add_argument("--file", default=None, help="从文件读取内容（UTF-8）")
    p_add.add_argument("--type", default="convention", dest="type",
                       choices=["convention", "prohibition", "documentation", "experience"],
                       help="内容类型（prohibition=禁止项，召回置顶并常驻注入）")
    p_add.add_argument("--tags", default="", help="逗号分隔")
    p_add.add_argument("--roles", default="", help="逗号分隔，空=通用")
    p_add.add_argument("--project", default=None, help="已废弃：由当前目录绑定")
    _add_verbose(p_add)
    p_list = kn_sub.add_parser("list", help="列出知识条目")
    p_list.add_argument("--project", default=None, help="已废弃：由当前目录绑定")
    _add_verbose(p_list)
    p_del = kn_sub.add_parser("delete", help="删除知识条目")
    p_del.add_argument("id")
    p_del.add_argument("--project", default=None, help="已废弃：由当前目录绑定")
    _add_verbose(p_del)
    p_acc = kn_sub.add_parser("accept", help="放行本轮 bootstrap 抽取（不碰 auto-extract）")
    p_acc.add_argument("--reject", action="store_true", help="拒绝本轮抽取")
    p_acc.add_argument(
        "--conflicts", choices=["tend", "coexist"], default=None,
        help="tend=按 recommended 裁决本轮冲突；coexist=两侧都留",
    )
    p_acc.add_argument("--run", default=None, help="bootstrap run id；缺省读 .rsi/bootstrap_run.json")
    _add_verbose(p_acc)

    p_wipe = sub.add_parser("wipe", help="一键清除本项目记忆文件（保留 identity.json）")
    p_wipe.add_argument(
        "--yes", action="store_true",
        help="确认删除 .rsi 下记忆文件与缓存（保留 identity.json）",
    )
    p_wipe.add_argument(
        "--project-root", default=None,
        help="项目根，默认当前工作区",
    )
    _add_verbose(p_wipe)

    p_mig = sub.add_parser("migrate", help="从旧版 ~/.rsi/rsi.db 认领命名空间到当前项目")
    _add_verbose(p_mig)
    mig_sub = p_mig.add_subparsers(dest="migrate_action", required=True)
    mig_sub.add_parser("status", help="列出遗留共享库中的 project_id 命名空间")
    p_adopt = mig_sub.add_parser(
        "adopt",
        help="把指定命名空间拷入当前项目库（请改用 rsi memory migrate）",
    )
    p_adopt.add_argument("namespace", help="旧库中的 project_id，如 default 或目录名")
    p_adopt.add_argument("--project-root", default=".", help="目标项目根，默认当前目录")
    _add_verbose(p_adopt)

    p_mem = sub.add_parser("memory", help=t("CLI_MEMORY_HELP", loc))
    _add_verbose(p_mem)
    p_mem.add_argument("argv", nargs=argparse.REMAINDER)

    p_learn = sub.add_parser("learn", help=t("CLI_LEARN_HELP", loc))
    _add_verbose(p_learn)
    p_learn.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


def main() -> None:
    ensure_utf8_stdio()
    args = build_parser().parse_args()
    if getattr(args, "lang", None) in ("zh", "en"):
        os.environ["RSI_LANG"] = args.lang
    handlers = {
        "init": cmd_init,
        "serve": cmd_serve,
        "recall": cmd_recall,
        "knowledge": cmd_knowledge,
        "migrate": cmd_migrate,
    }
    if args.command == "wipe":
        from .cli.wipe_command import run_wipe

        setup_cli_logging(getattr(args, "verbose", False))
        try:
            sys.exit(run_wipe(args))
        except KeyboardInterrupt:
            sys.exit(130)

    if args.command == "memory":
        from .cli.memory_command import run_memory

        setup_cli_logging(getattr(args, "verbose", False))
        try:
            sys.exit(run_memory(list(getattr(args, "argv", []) or [])))
        except KeyboardInterrupt:
            sys.exit(130)

    if args.command == "learn":
        from .cli.learn_command import run_learn

        setup_cli_logging(getattr(args, "verbose", False))
        try:
            sys.exit(run_learn(list(getattr(args, "argv", []) or [])))
        except KeyboardInterrupt:
            sys.exit(130)

    if args.command == "bootstrap":
        from .cli.bootstrap_command import run_bootstrap

        setup_cli_logging(getattr(args, "verbose", False))
        try:
            code = asyncio.run(run_bootstrap(args))
        except KeyboardInterrupt:
            code = 130
        sys.exit(code)
    try:
        code = asyncio.run(handlers[args.command](args))
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
