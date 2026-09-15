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
    "ELAPSED": {"zh": "已用 {duration}", "en": "elapsed {duration}"},
    "REMAINING": {"zh": "约剩 {duration}", "en": "eta {duration}"},
    "LT_ONE_SEC": {"zh": "不到1秒", "en": "under 1s"},
    "MEMORY_NEED_SUB": {
        "zh": "请指定子命令：migrate / reindex / index / open / graph",
        "en": "Specify a subcommand: migrate / reindex / index / open / graph",
    },
    "KNOWLEDGE_TOO_LONG": {
        "zh": "知识正文不能超过 1500 字",
        "en": "Knowledge content cannot exceed 1500 characters",
    },
    "KNOWLEDGE_HARVEST_TITLE": {
        "zh": "不能使用采集物标题，请写自包含短知识",
        "en": "Harvest titles are not allowed; write self-contained knowledge",
    },
    "KNOWLEDGE_DISTILL_TAGS": {
        "zh": "蒸馏条必须带 bootstrap_run_id 标签",
        "en": "Distilled items need a bootstrap_run_id tag",
    },
    "LEARN_NEED_ACTION": {
        "zh": "请指定动作：teach_catch / teach_record / skip / rubric / audit_start / audit_probes / audit_report / audit_finish / extract / promote / pack_list / pack_open / pack_done",
        "en": "Specify an action: teach_catch / teach_record / skip / rubric / audit_start / audit_probes / audit_report / audit_finish / extract / promote / pack_list / pack_open / pack_done",
    },
    "PACK_NEED_BOOTSTRAP": {
        "zh": "还没有阅读包。请先运行 rsi bootstrap --consent --host-judge",
        "en": "No reading packs yet. Run rsi bootstrap --consent --host-judge first",
    },
    "PACK_NOT_FOUND": {
        "zh": "找不到该阅读包",
        "en": "Reading pack not found",
    },
    "PACK_NEED_ID": {
        "zh": "pack_open / pack_done 需要 id",
        "en": "pack_open / pack_done needs an id",
    },
    "PACK_BAD_STATUS": {
        "zh": "pack_done 的 status 只能是 done 或 skipped",
        "en": "pack_done status must be done or skipped",
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
    "AUDIT_STARTED": {
        "zh": "已打开审计会话 {scope}",
        "en": "Opened audit session {scope}",
    },
    "AUDIT_PROBES_SAVED": {
        "zh": "已收下探针结果 {n} 条",
        "en": "Stored {n} probe result(s)",
    },
    "AUDIT_REPORTED": {
        "zh": "已写入审计报告 {path}",
        "en": "Wrote audit report {path}",
    },
    "AUDIT_FINISHED": {
        "zh": "审计会话已关闭",
        "en": "Audit session closed",
    },
    "AUDIT_NEED_SCOPE_ID": {
        "zh": "full 审计必须带 scope_id",
        "en": "full audit requires scope_id",
    },
    "AUDIT_NEED_PROBES": {
        "zh": "请上传 probe_results，RSI 不会代跑探针",
        "en": "Upload probe_results. RSI does not run probes.",
    },
    "AUDIT_NEED_OVERALL": {
        "zh": "audit_report 需要 overall",
        "en": "audit_report needs overall",
    },
    "AUDIT_INVALID_SCOPE": {
        "zh": "scope 只能是 session 或 full",
        "en": "scope must be session or full",
    },
    "EXTRACT_NEED_ITEMS": {
        "zh": "extract 需要已分类条目 items",
        "en": "extract needs classified items",
    },
    "PROMOTE_DONE": {
        "zh": "已写入 {n} 条 pending 规范，转正请用 rsi_knowledge_review",
        "en": "Wrote {n} pending norm(s). Approve with rsi_knowledge_review.",
    },
    "PROMOTE_NONE": {
        "zh": "没有达到晋升门槛的案例",
        "en": "No case met the promote threshold.",
    },
    "MEMORY_GRAPH": {
        "zh": "关系图如下（mermaid 文本，未写入知识文件）",
        "en": "Relationship graph as mermaid text. No knowledge file was written.",
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
    "CLI_MEMORY_HELP": {"zh": "迁出/索引/打开", "en": "Migrate/index/open"},
    "CLI_LEARN_HELP": {"zh": "教学与收工", "en": "Teaching and closeout"},
    "CLI_LANG_HELP": {"zh": "叙述语言，覆盖环境", "en": "Narration language override"},
    "QUERY_REQUIRED": {"zh": "query 必填", "en": "query is required"},
    "FEEDBACK_ACTION_INVALID": {
        "zh": "action 必须是 {actions} 之一",
        "en": "action must be one of {actions}",
    },
    "FEEDBACK_RATING_RANGE": {"zh": "rating 须在 1-5 之间", "en": "rating must be between 1 and 5"},
    "FEEDBACK_TOKEN_MISSING": {
        "zh": "feedback_token 不存在",
        "en": "feedback_token not found",
    },
    "FEEDBACK_TOKEN_BAD_SIG": {
        "zh": "feedback_token 签名校验失败",
        "en": "feedback_token signature check failed",
    },
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
        "Teaching / audit / closeout learn / promote / reading-pack actions. "
        "The host agent writes the lesson (Catch→Teach→Fix) then teach_record; "
        "RSI does not write the lesson. pack_list / pack_open / pack_done walk reading packs. "
        "Unavailable actions return not_in_phase — follow message. "
        "教学/审计/收工学习/晋升/阅读包。Agent 自己 Catch→Teach→Fix 后 teach_record；"
        "不要让 RSI 代写 lesson。pack_list / pack_open / pack_done 串联阅读包。"
        "未开放的 action 返回 not_in_phase，按 message 改用其它工具。"
    ),
    "memory": (
        "Open or search memory files. index lists entries, open reads one id, reindex rebuilds cache, "
        "graph returns mermaid text. "
        "打开或检索记忆文件。index 列目录，open 按 id 读全文，reindex 重建缓存，graph 返回 mermaid 文本。"
    ),
    "review": (
        "Approve pending norms: approve moves to official dirs and injects prohibitions; "
        "reject archives. Does not create new bodies. Approved conventions are recall-only "
        "(no rsi-convention rule files). "
        "审批 pending 规范：approve 搬到正式目录并注入禁止项，reject 搬到 archive。"
        "不新建正文。约定获准后只进召回，不会写成 rsi-convention 规则文件。"
    ),
    "feedback": (
        "Submit feedback on a recall: explicit rating or IDE implicit action "
        "(accepted/applied/modified/copied/referenced/ignored/rejected). "
        "On rejected, a comment is extracted as a prohibition. "
        "对召回结果提交反馈：显式评分或 IDE 隐式行为"
        "（accepted/applied/modified/copied/referenced/ignored/rejected）；"
        "rejected 时填 comment 将提炼为禁止项。"
    ),
    "knowledge_add": (
        "Add a knowledge item (title + content required). "
        "添加知识条目（title + content 必填）。"
    ),
    "knowledge_search": (
        "Search the knowledge base (FTS5 + CJK bigram by default; embedding is optional). "
        "搜索知识库（默认纯 FTS5 + CJK bigram；embedding 为可选增强）。"
    ),
    "knowledge_delete": (
        "Delete a knowledge item (deletion right). "
        "删除指定知识条目（删除权）。"
    ),
    "harness": (
        "Harness improvement proposals: list/approve/reject/rollback/generate "
        "and config snapshots snapshot_list/snapshot_switch/snapshot_export. "
        "Harness 改进提案：list/approve/reject/rollback/generate "
        "及配置快照 snapshot_list/snapshot_switch/snapshot_export。"
    ),
    "stats": (
        "Memory usage stats: period=day/week/month summaries of reach, adoption, "
        "prohibition follow-through, and proposal pass rate. "
        "记忆使用统计：period=day/week/month 汇总触达/采纳/禁止项遵循/提案通过率。"
    ),
    "conflicts": (
        "List or resolve suspected conflicts. Three kinds: "
        "(1) learned memory vs handwritten rules (contradiction/stale/overlap) — "
        "user_wins keeps your rule (learned memory no longer injected), "
        "memory_wins keeps learned memory (edit the rule file yourself), "
        "coexist keeps both and stops reminding; "
        "(2) knowledge pairs (version/doc_code/incoherent) — "
        "keep_item keeps item_id and archives the peer source_url, "
        "keep_peer keeps the user_rule_path side, coexist keeps both; "
        "(3) explain (conflict_id required) expands both sides read-only. "
        "Call explain first when the user wants detail; do not close the card. "
        "查询疑似冲突并对冲突做出裁决。三类："
        "① 学习记忆 vs 手写规则（contradiction/stale/overlap）——"
        "user_wins=以你的规则为准（学习记忆不再注入）、memory_wins=以学习记忆为准"
        "（返回规则文件位置请手动修改）、coexist=两者共存不再提醒；"
        "② 知识对（version/doc_code/incoherent）——"
        "keep_item=保留 item_id 侧并归档对侧 source_url、keep_peer=保留 user_rule_path 侧、"
        "coexist=两侧转/保持可用且不再提醒；"
        "③ explain（conflict_id 必填）=只读展开两侧摘录与各 option 影响，不改库。"
        "用户要展开说明时先 explain，不要关掉抉择卡。"
    ),
}


def t(key: str, lang: str, **kwargs) -> str:
    pair = STRINGS[key]
    template = pair.get(lang, pair["en"])
    if kwargs:
        return template.format(**kwargs)
    return template
