"""意图评测集生成器（§12.1：模板生成来源，人工标注/日志回流后续追加）。

确定性生成（seed 固定），输出 intent_general.jsonl / intent_role.jsonl。
每意图 ≥ 50 条；通用意图与角色意图分开统计。重新生成：
    python -X utf8 -m tests.eval.datasets.gen_intent_dataset
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT_DIR = Path(__file__).parent

# (模板列表, 槽位填充列表)；每模板 × 每填充 = 1 条样本
GENERAL: dict[str, tuple[list[str], list[str]]] = {
    "code_review": ([
        "帮我 review 这段{lang}代码",
        "审查一下这个{thing}有没有问题",
        "review my {thing} changes",
        "这段代码有什么潜在缺陷，帮我审阅",
        "code review: {thing}",
        "看看这个 PR 的{thing}写得对不对",
        "帮我检查{thing}的代码质量",
        "请审查以下实现是否合理",
        "can you review this {lang} snippet",
        "审阅这段{lang}函数的逻辑",
    ], ["Python", "Go", "SQL", "接口", "模块", "补丁"]),
    "code_gen": ([
        "帮我实现一个{thing}",
        "生成一个{thing}的代码",
        "write a {thing} in {lang}",
        "创建一个{thing}函数",
        "implement {thing} with retry",
        "帮我写{thing}的单元工具",
        "generate a pydantic model for {thing}",
        "用{lang}写一个{thing}",
        "造一个{thing}的脚手架",
        "给我实现{thing}功能",
    ], ["缓存", "HTTP 客户端", "分页", "鉴权", "Python", "解析器"]),
    "debug": ([
        "这个{thing}一直报错，帮我看看",
        "why does {thing} fail with {err}",
        "帮我 debug 这个{thing}",
        "修复这个{err}错误",
        "{thing} 偶发失败，排查一下",
        "程序抛出 {err}，什么原因",
        "帮我定位{thing}的 bug",
        "this test is broken, fix it",
        "线上{thing} panic 了，帮忙分析",
        "为什么{thing}结果不对",
    ], ["接口", "任务", "脚本", "KeyError", "TimeoutError", "空指针"]),
    "explain": ([
        "解释一下{thing}是什么",
        "什么是{thing}",
        "how does {thing} work",
        "{thing}的原理是什么",
        "说明一下这段{thing}的逻辑",
        "what is {thing} used for",
        "给我讲讲{thing}的机制",
        "为什么{thing}要这样设计",
        "explain the difference between {thing} and {thing2}",
        "{thing} 的底层实现讲一下",
    ], ["GIL", "WAL 模式", "协程", "索引", "缓存穿透", "索引"]),
    "general_assist": ([
        "帮我把这段话翻译成{lang}",
        "写一封{thing}邮件",
        "今天{thing}要准备什么",
        "帮我润色这段{thing}",
        "总结一下这篇{thing}的要点",
        "帮我想几个{thing}的名字",
        "整理一下{thing}的清单",
        "这个{thing}怎么描述更好",
        "帮我列一个{thing}计划",
        "把{thing}转成表格",
    ], ["英文", "周报", "项目", "会议", "文档", "演示"]),
}

ROLE_INTENTS: dict[str, dict[str, tuple[list[str], list[str]]]] = {
    "test": {
        "test_gen": ([
            "为{thing}生成测试用例",
            "帮我写{thing}的 pytest 单测",
            "generate test cases for {thing}",
            "补一下{thing}的边界测试",
            "给{thing}写测试场景",
            "create unit tests for {thing}",
            "{thing}的测试用例帮我生成",
            "为{thing}设计参数化测试",
            "写{thing}的集成测试",
            "帮我补{thing}的异常路径用例",
        ], ["登录接口", "支付回调", "分页函数", "缓存模块", "鉴权中间件"]),
        "test_review": ([
            "评审一下这组{thing}用例",
            "review my test plan for {thing}",
            "这个{thing}测试有没有遗漏场景",
            "帮我审查{thing}的测试覆盖",
            "测试评审：{thing}",
            "看看这些用例质量怎么样",
            "审查{thing}的测试设计",
            "这份测试方案帮我把关",
            "evaluate my test cases for {thing}",
            "检查{thing}测试的断言是否充分",
        ], ["登录", "订单", "支付", "搜索", "导入"]),
        "bug_analyze": ([
            "分析这个{thing}崩溃的根因",
            "这个 bug 偶发，帮我整理复现条件",
            "root cause analysis for {thing}",
            "定位{thing}失败的原因",
            "帮我分析这个缺陷的引入点",
            "why did {thing} fail in CI",
            "分析{thing}的报错堆栈",
            "这个{thing}缺陷的影响面",
            "排查{thing}的回归原因",
            "帮我归类这个{thing}问题",
        ], ["崩溃", "超时", "脏数据", "并发", "内存"]),
        "test_data": ([
            "帮我造{count}条{thing}测试数据",
            "生成{thing}的 mock 数据",
            "构造{thing}的测试 fixture",
            "造一些{thing}的边界值数据",
            "prepare test data for {thing}",
            "给我{thing}的测试数据集",
            "生成符合{thing}规则的数据",
            "mock {thing} 的响应报文",
            "构造{thing}的异常数据",
            "帮我准备{thing}的压测数据",
        ], ["用户", "订单", "身份证", "100", "500"]),
        "coverage_check": ([
            "看看{thing}还有哪些分支没覆盖",
            "分析{thing}的覆盖率缺口",
            "which branches of {thing} are not tested",
            "这次变更影响哪些用例",
            "检查{thing}的测试覆盖率",
            "{thing}的覆盖率为什么上不去",
            "帮我找出{thing}的漏测点",
            "coverage report for {thing}",
            "哪些{thing}路径没有测试",
            "统计{thing}的用例覆盖",
        ], ["支付模块", "鉴权", "订单流", "缓存", "消息队列"]),
    },
    "pm": {
        "req_analyze": ([
            "帮我分析{thing}的用户场景",
            "把{thing}拆成用户故事",
            "analyze the requirement for {thing}",
            "{thing}的需求价值评估一下",
            "这个{thing}需求的核心痛点是什么",
            "帮我梳理{thing}的需求边界",
            "评估{thing}的优先级",
            "{thing}需求的依赖关系分析",
            "拆解{thing}这个 epic",
            "分析{thing}需求的验收标准",
        ], ["收藏功能", "导出", "消息通知", "搜索", "会员体系"]),
        "prd_gen": ([
            "帮我写一份{thing}的 PRD",
            "draft a PRD for {thing}",
            "生成{thing}的需求文档",
            "写{thing}的功能说明书",
            "帮我起草{thing}的 PRD 初稿",
            "{thing}的产品文档帮我整理",
            "输出{thing}的需求规格",
            "写{thing}的功能清单",
            "帮我完善{thing}的 PRD 结构",
            "生成{thing}的原型说明",
        ], ["导出功能", "签到", "积分", "搜索", "分享"]),
        "doc_review": ([
            "评审这份{thing} PRD 有没有漏洞",
            "check {thing} spec for missing edge cases",
            "帮我审查{thing}文档的一致性",
            "这份{thing}需求文档评审一下",
            "看看{thing} PRD 的逻辑是否自洽",
            "review the {thing} requirements doc",
            "{thing}文档里的矛盾点帮我找一下",
            "审查{thing}方案的完整性",
            "这份{thing}文档还缺什么",
            "帮我检查{thing} PRD 的边界条件",
        ], ["支付", "登录", "导出", "搜索", "通知"]),
        "data_analysis": ([
            "分析一下{metric}下降的原因",
            "帮我设计{thing}的数据看板指标",
            "{metric}波动帮我解读一下",
            "分析{thing}的转化漏斗",
            "这周{metric}为什么涨了",
            "build a metrics framework for {thing}",
            "{thing}的留存数据帮我分析",
            "给我{thing}的周报数据解读",
            "对比两个版本的{metric}",
            "{thing}的 A/B 结果显著吗",
        ], ["留存率", "DAU", "转化率", "新功能", "活动"]),
        "stakeholder_comm": ([
            "帮我写一封{thing}的说明邮件",
            "准备给{who}的{thing}汇报大纲",
            "写一份{thing}的会议纪要",
            "帮我起草{thing}的同步文档",
            "给{who}解释{thing}的口径",
            "写{thing}的周报",
            "帮我准备{thing}评审会材料",
            "起草{thing}的延期公告",
            "给{who}写{thing}的进展同步",
            "整理{thing}的 FAQ 给客服",
        ], ["项目延期", "老板", "客户", "新功能", "上线"]),
    },
}


def _materialize(templates: list[str], slots: list[str], min_per_intent: int = 50) -> list[str]:
    """模板 × 槽位确定性展开（每模板循环取槽位，补足 min_per_intent）"""
    out: list[str] = []
    i = 0
    while len(out) < min_per_intent:
        tpl = templates[i % len(templates)]
        slot = slots[(i // len(templates)) % len(slots)]
        slot2 = slots[(i // len(templates) + 1) % len(slots)]
        text = tpl.replace("{thing2}", slot2)
        for key in ("{thing}", "{lang}", "{err}", "{count}", "{metric}", "{who}"):
            text = text.replace(key, slot)
        if text not in out:
            out.append(text)
        i += 1
        if i > min_per_intent * 4:  # 防御：模板去重后不足时封顶
            break
    return out


def main() -> None:
    random.seed(42)
    general_rows = []
    for intent, (templates, slots) in GENERAL.items():
        for text in _materialize(templates, slots):
            general_rows.append({"input": text, "intent": intent})
    (OUT_DIR / "intent_general.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in general_rows) + "\n", encoding="utf-8"
    )

    role_rows = []
    for role, intents in ROLE_INTENTS.items():
        for intent, (templates, slots) in intents.items():
            for text in _materialize(templates, slots):
                role_rows.append({"input": text, "intent": intent, "role": role})
    (OUT_DIR / "intent_role.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in role_rows) + "\n", encoding="utf-8"
    )
    print(f"general={len(general_rows)} role={len(role_rows)}")


if __name__ == "__main__":
    main()
