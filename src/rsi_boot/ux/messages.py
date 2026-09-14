"""Locked user-facing copy. Narration localizes; symbols and terms stay as-is."""

from __future__ import annotations

STRINGS = {
    "MIGRATE_TITLE": {"zh": "开始迁出记忆：{root}", "en": "Migrating memory: {root}"},
    "MIGRATE_PHASE_OPEN": {"zh": "打开旧库", "en": "Open legacy database"},
    "MIGRATE_PHASE_KNOWLEDGE": {"zh": "导出知识", "en": "Export knowledge"},
    "MIGRATE_PHASE_LOGS": {"zh": "导出日志", "en": "Export logs"},
    "MIGRATE_PHASE_STATE": {"zh": "导出状态", "en": "Export state"},
    "MIGRATE_PHASE_WRITE": {"zh": "写入文件", "en": "Write files"},
    "MIGRATE_PHASE_BAK": {"zh": "备份旧库", "en": "Rename legacy database"},
    "MIGRATE_PHASE_INDEX": {"zh": "建立索引", "en": "Build index"},
    "MIGRATE_PHASE_INJECT": {"zh": "注入禁止项", "en": "Inject prohibitions"},
    "MIGRATE_DONE": {"zh": "迁出完成", "en": "Migration complete"},
    "MIGRATE_DONE_DRY": {"zh": "（仅预览，未改磁盘）", "en": "(dry-run, nothing written)"},
    "MIGRATE_NO_DB": {
        "zh": "没有找到可迁出的 rsi.db。当前目录已是文件记忆，或尚未初始化。",
        "en": "No rsi.db to migrate. This workspace already uses file memory, or is not initialized.",
    },
    "MIGRATE_HALF": {
        "zh": "迁出未完成，已停止。未改名 rsi.db。查看 .rsi/logs/errors.jsonl",
        "en": "Migration stopped. rsi.db was not renamed. See .rsi/logs/errors.jsonl",
    },
    "MIGRATE_SUMMARY": {
        "zh": "知识 {knowledge} 条，日志 {logs} 行，臂 {arms} 条，未映射 retrieved {unmapped} 条。旧库已改名为 {bak}",
        "en": "{knowledge} knowledge files, {logs} log lines, {arms} arms, {unmapped} unmapped retrieved. Legacy DB renamed to {bak}",
    },
    "MIGRATE_ADOPT_DEPRECATED": {
        "zh": "请改用 rsi memory migrate",
        "en": "Use rsi memory migrate instead",
    },
    "REINDEX_TITLE": {"zh": "重建索引：{root}", "en": "Rebuilding index: {root}"},
    "REINDEX_DONE": {"zh": "索引已重建", "en": "Index rebuilt"},
    "BOOTSTRAP_DONE": {"zh": "学习完成", "en": "Learning complete"},
    "DURATION": {"zh": "总耗时 {duration}", "en": "elapsed {duration}"},
    "MEMORY_NEED_SUB": {
        "zh": "请指定子命令：migrate / reindex / index / open / graph",
        "en": "Specify a subcommand: migrate / reindex / index / open / graph",
    },
    "LEARN_NEED_ACTION": {
        "zh": "请指定动作：teach_catch / teach_record / skip / rubric（其余动作按期开放）",
        "en": "Specify an action: teach_catch / teach_record / skip / rubric (other actions come later)",
    },
    "TEACH_RECORDED": {"zh": "已写入教学案例 {path}", "en": "Wrote teaching case {path}"},
    "TEACH_CATCH_SAVED": {
        "zh": "已记下现场 {id}，写好 lesson 后调用 teach_record",
        "en": "Saved catch {id}. Call teach_record after you write the lesson.",
    },
    "TEACH_NEED_FIX": {
        "zh": "lesson.correct_fix 不能为空",
        "en": "lesson.correct_fix must not be empty",
    },
    "TEACH_SKIPPED": {"zh": "已跳过现场 {id}", "en": "Skipped catch {id}"},
    "MEMORY_INDEX": {"zh": "共 {n} 条记忆文件", "en": "{n} memory file(s)"},
    "MEMORY_OPENED": {"zh": "已打开记忆 {id}", "en": "Opened memory {id}"},
    "REVIEW_APPROVED": {
        "zh": "已获准 {n} 条，禁止项已注入规则文件",
        "en": "Approved {n} item(s). Prohibitions were injected.",
    },
    "REVIEW_REJECTED": {"zh": "已归档 {n} 条", "en": "Archived {n} item(s)"},
    "EXTRACT_CLOSEOUT": {
        "zh": "本轮学习清单如下，请在最终总结中照抄路径或写跳过原因",
        "en": "Closeout list below. Copy the paths into your final summary, or write why you skipped.",
    },
    "PHASE_PROMOTE": {
        "zh": "promote 下一期才开放。规范转正请用 rsi_knowledge_review。",
        "en": "promote is not available yet. Approve pending norms with rsi_knowledge_review.",
    },
    "PHASE_GRAPH": {
        "zh": "graph 下一期才开放。先用 rsi_memory action=index 看目录。",
        "en": "graph is not available yet. List files with rsi_memory action=index.",
    },
    "PHASE_AUDIT": {
        "zh": "审计动作下一期才开放。本轮请用 teach_record 记录可复用修法。",
        "en": "Audit actions are not available yet. Use teach_record for reusable fixes this wave.",
    },
    "HINT_TEACH": {
        "zh": "若本轮修失败或用户纠正，先 rsi_learn teach_catch",
        "en": "If this repair fails or the user corrects you, call rsi_learn teach_catch first.",
    },
    "SEC": {"zh": "{n}秒", "en": "{n}s"},
    "MIN": {"zh": "{n}分", "en": "{n}m"},
    "HOUR": {"zh": "{n}小时", "en": "{n}h"},
    "NOT_FOUND": {"zh": "未找到记忆 {id}", "en": "Memory {id} not found"},
    "YAML_INVALID": {"zh": "YAML 字段不合法：{path}", "en": "Invalid YAML fields: {path}"},
    "WIPE_HINT": {
        "zh": "清掉本项目 .rsi 下的记忆文件与缓存，保留 identity.json。确认请加 --yes。",
        "en": "This deletes memory files and cache under .rsi, and keeps identity.json. Pass --yes to confirm.",
    },
    "WIPE_DONE": {"zh": "已删除记忆文件", "en": "Deleted memory files"},
    "WIPE_KEPT": {"zh": "已保留: {path}", "en": "Kept: {path}"},
    "BOOTSTRAP_PHASE_INIT": {"zh": "初始化记忆目录", "en": "Initialize memory directories"},
}

# list_tools：中英并列，不走 t()。改字必须同步测试。
TOOL_DESC = {
    "recall": (
        "Call before any coding, adaptation, debugging, or design task. "
        "Returns prohibitions you must follow, related items, and a skill catalog "
        "(name/description/path only). Use feedback_token with rsi_feedback before you wrap up. "
        "If decisions is non-empty, call rsi_conflicts / rsi_knowledge_review immediately; "
        "do not ask the user to run rsi in a terminal. "
        "任务开始前调用。返回必须遵守的 prohibitions、相关 items，以及技能目录。"
        "用 feedback_token 在收工前调用 rsi_feedback。"
        "有 decisions 时立刻调 rsi_conflicts / rsi_knowledge_review，不要让用户去终端跑 rsi。"
    ),
    "learn": (
        "Teaching / audit / closeout learn / promote. The host agent writes the lesson "
        "(Catch→Teach→Fix) then teach_record; RSI does not write the lesson. "
        "Unavailable actions return not_in_phase — follow message. "
        "教学/审计/收工学习/晋升。Agent 自己 Catch→Teach→Fix 后 teach_record；"
        "不要让 RSI 代写 lesson。未开放的 action 返回 not_in_phase，按 message 改用其它工具。"
    ),
    "memory": (
        "Open or search memory files. index lists entries, open reads one id, reindex rebuilds cache. "
        "graph comes later. "
        "打开或检索记忆文件。index 列目录，open 按 id 读全文，reindex 重建缓存。graph 下一期才开放。"
    ),
    "review": (
        "Approve pending norms: approve moves to official dirs and injects prohibitions; "
        "reject archives. Does not create new bodies. Approved conventions are recall-only "
        "(no rsi-convention rule files). "
        "审批 pending 规范：approve 搬到正式目录并注入禁止项，reject 搬到 archive。"
        "不新建正文。约定获准后只进召回，不会写成 rsi-convention 规则文件。"
    ),
}


def t(key: str, lang: str, **kwargs) -> str:
    pair = STRINGS[key]
    template = pair.get(lang, pair["en"])
    if kwargs:
        return template.format(**kwargs)
    return template
