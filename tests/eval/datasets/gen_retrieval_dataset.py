"""检索评测集生成器（§12.2）：≥3 类项目 × 每类 ≥30 条 query→相关条目标注对。

合成语料：每类项目 10 篇主题文档（关键词富集、主题间词汇低重叠），
每篇 3 条查询（措辞改写但保留主题关键词）→ 30 条/类，共 90 条。
确定性输出。重新生成：
    python -X utf8 -m tests.eval.datasets.gen_retrieval_dataset
"""

from __future__ import annotations

import json
from pathlib import Path

OUT_DIR = Path(__file__).parent

# 项目类型 -> [(doc_id, title, 关键词集)]；文档正文由关键词展开
CORPUS: dict[str, list[tuple[str, str, list[str]]]] = {
    "web_backend": [
        ("wb_deploy", "部署指南", ["docker", "compose", "deploy", "container", "registry"]),
        ("wb_auth", "鉴权设计", ["jwt", "token", "oauth", "session", "credential"]),
        ("wb_db", "数据库规范", ["migration", "index", "transaction", "postgres", "schema"]),
        ("wb_cache", "缓存策略", ["redis", "cache", "ttl", "invalidation", "memcached"]),
        ("wb_api", "API 设计规范", ["rest", "endpoint", "pagination", "versioning", "status"]),
        ("wb_log", "日志规范", ["logging", "level", "rotation", "structured", "trace"]),
        ("wb_queue", "消息队列", ["kafka", "producer", "consumer", "partition", "offset"]),
        ("wb_test", "接口测试", ["pytest", "fixture", "mock", "integration", "assert"]),
        ("wb_sec", "安全基线", ["xss", "csrf", "injection", "encryption", "https"]),
        ("wb_perf", "性能优化", ["latency", "throughput", "profiling", "bottleneck", "n+1"]),
    ],
    "frontend": [
        ("fe_build", "构建配置", ["vite", "webpack", "bundle", "rollup", "esbuild"]),
        ("fe_state", "状态管理", ["redux", "zustand", "store", "action", "reducer"]),
        ("fe_css", "样式规范", ["tailwind", "css", "flexbox", "grid", "responsive"]),
        ("fe_comp", "组件设计", ["component", "props", "hooks", "render", "memo"]),
        ("fe_route", "路由约定", ["router", "route", "navigation", "redirect", "lazy"]),
        ("fe_test", "前端测试", ["vitest", "testing-library", "snapshot", "e2e", "playwright"]),
        ("fe_a11y", "可访问性", ["aria", "keyboard", "contrast", "screen-reader", "focus"]),
        ("fe_http", "请求封装", ["fetch", "axios", "interceptor", "retry", "abort"]),
        ("fe_i18n", "国际化", ["i18n", "locale", "translation", "plural", "format"]),
        ("fe_perf", "前端性能", ["lazy-loading", "chunk", "preload", "lighthouse", "cls"]),
    ],
    "cli_tool": [
        ("cl_arg", "参数解析", ["argparse", "flag", "subcommand", "option", "usage"]),
        ("cl_pkg", "打包发布", ["pyinstaller", "wheel", "pypi", "entrypoint", "binary"]),
        ("cl_log", "终端输出", ["color", "progress", "spinner", "verbose", "quiet"]),
        ("cl_cfg", "配置文件", ["ini", "dotenv", "xdg", "profile", "override"]),
        ("cl_pipe", "管道集成", ["stdin", "stdout", "pipe", "redirect", "tty"]),
        ("cl_test", "CLI 测试", ["click-testing", "capsys", "exit-code", "invoke", "runner"]),
        ("cl_up", "自动更新", ["version", "upgrade", "checksum", "release", "channel"]),
        ("cl_shell", "Shell 补全", ["bash", "zsh", "completion", "fish", "autocomplete"]),
        ("cl_err", "错误处理", ["exit-code", "stderr", "traceback", "friendly", "hint"]),
        ("cl_perf", "启动优化", ["startup", "lazy-import", "cold-start", "cache", "bytecode"]),
    ],
}

# 查询模板（保留主题关键词，措辞多样化）
QUERY_TEMPLATES = [
    "{k1} {k2} 怎么做",
    "how to configure {k1} {k2}",
    "{k1} and {k2} best practice",
]


def main() -> None:
    corpus_rows = []
    query_rows = []
    for project_type, docs in CORPUS.items():
        for doc_id, title, keywords in docs:
            content = (
                f"{title}。本文档覆盖 {'、'.join(keywords)}。"
                f"详细说明 {keywords[0]} 与 {keywords[1]} 的配置方法、"
                f"常见陷阱以及 {keywords[2]} 的最佳实践。"
                f"涉及 {keywords[3]} 和 {keywords[4]} 的完整流程。"
            )
            corpus_rows.append({
                "id": doc_id, "project_type": project_type, "title": title, "content": content,
            })
            for i, tpl in enumerate(QUERY_TEMPLATES):
                query_rows.append({
                    "query": tpl.format(k1=keywords[i], k2=keywords[(i + 1) % len(keywords)]),
                    "relevant": [doc_id],
                    "project_type": project_type,
                })

    (OUT_DIR / "retrieval_corpus.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in corpus_rows) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "retrieval_queries.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in query_rows) + "\n", encoding="utf-8"
    )
    print(f"corpus={len(corpus_rows)} queries={len(query_rows)}")


if __name__ == "__main__":
    main()
