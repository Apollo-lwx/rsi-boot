"""状态栏情绪文案：手写池（分场景）+ 模板造句器，中英成对。

手写池是质量下限；造句器用「模板 × 槽位词库」组合出几百种句子，
解决固定池轮动必然重复的问题——同一模板内任意组合都保证语法通顺、
语气一致（槽位词库按模板专属定制，不混用）。

与 messages.py 的「锁定功能文案」不同，这里是可自由扩充的展示文案；
口径由 tests/test_statusline.py 锁定：产出 zh 必含汉字、en 不得含汉字。

防闪烁：宿主每 ~300ms 调一次 statusline，文案按 45s 时间桶 + session_id 播种，
同一桶内渲染结果稳定，跨桶才换一条。
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from pathlib import Path

#: 文案稳定窗口（秒）：桶内不变，跨桶换文案
QUIP_BUCKET_SECONDS = 45

# ---------------------------------------------------------------------------
# 主题与风格
#
# 每条文案归属一个主题（theme）；风格（style）是用户可选的主题子集——
# 律师选 serious 就不会看到猫咪和玄学。core 主题（陪伴/养生/安抚/祝福）全风格通用。
# 解析链：--style > RSI_STATUSLINE_STYLE > ~/.rsi/config.yaml 的 statusline_style > mixed。
# ---------------------------------------------------------------------------

#: 全部主题名（用户文案文件的 theme 字段也用这个词汇表）
THEME_NAMES = frozenset({
    "core", "motto", "kudos", "fortune", "geek", "dry", "healing",
    "food", "cat", "game", "hype", "slack", "offwork", "weather",
})

#: 风格预设 → 允许的主题；mixed 不做过滤
STYLE_THEMES: dict[str, frozenset[str] | None] = {
    "mixed": None,
    "serious": frozenset({"core", "motto"}),
    "healing": frozenset({"core", "healing", "cat", "food", "weather", "offwork"}),
    "geek": frozenset({"core", "geek", "dry", "game", "motto", "slack"}),
    "lucky": frozenset({"core", "fortune", "hype", "kudos"}),
    "buddy": frozenset({"core", "healing"}),
}

STYLE_NAMES = tuple(STYLE_THEMES)

#: 常规池按主题分组（REGULAR 为展开后的全量，向后兼容）
REGULAR_THEMES: dict[str, tuple[dict[str, str], ...]] = {
    "core": (
        {"zh": "给你比个心❤", "en": "sending you a heart ❤"},
        {"zh": "今天也是高效的一天", "en": "another productive day"},
        {"zh": "咖啡续命，代码如诗", "en": "coffee up, code like poetry"},
        {"zh": "思路如泉涌", "en": "ideas flowing"},
        {"zh": "写的都对，测的都过", "en": "may your tests all pass"},
        {"zh": "心想事成，编译一次过", "en": "may it compile on the first try"},
        {"zh": "灵感正在路上", "en": "inspiration is on its way"},
        {"zh": "先喝口水再战", "en": "hydrate, then conquer"},
        {"zh": "问题不大，都能解决", "en": "no big deal, we'll solve it"},
        {"zh": "世界需要你的代码", "en": "the world needs your code"},
        {"zh": "别忘了起来活动一下", "en": "don't forget to stretch"},
        {"zh": "眼睛歇一下，看看远处", "en": "rest your eyes, look far away"},
        {"zh": "你负责创意，我负责记忆", "en": "you bring ideas, I keep them"},
        {"zh": "搭档配合无间", "en": "partners in perfect sync"},
        {"zh": "每一次提交都算数", "en": "every commit counts"},
        {"zh": "陪你写代码中…", "en": "right here, coding with you"},
        {"zh": "我在呢，随时叫我", "en": "I'm here — call me anytime"},
        {"zh": "陪你到项目上线", "en": "with you until launch day"},
        {"zh": "你的编程搭子已上线", "en": "your coding buddy is online"},
        {"zh": "一起把这块硬骨头啃下来", "en": "let's crack this tough nut together"},
        {"zh": "有难题就丢给我", "en": "toss the hard problems my way"},
        {"zh": "慢慢来，token 够用", "en": "take your time — plenty of tokens"},
        {"zh": "上下文还很宽敞", "en": "plenty of context room left"},
        {"zh": "token 管够，放开写", "en": "tokens on the house — write freely"},
        {"zh": "不急，我们有的是上下文", "en": "no rush — context galore"},
        {"zh": "累了就歇会儿", "en": "rest when you're tired"},
        {"zh": "效率比时长重要", "en": "efficiency beats hours"},
        {"zh": "状态好就多干点", "en": "ride the flow while it lasts"},
        {"zh": "别忘了吃饭", "en": "don't forget to eat"},
    ),
    "motto": (
        {"zh": "保持热爱，奔赴山海", "en": "keep the passion, chase the horizon"},
        {"zh": "慢慢来，比较快", "en": "slow is smooth, smooth is fast"},
    ),
    "kudos": (
        {"zh": "你超棒的", "en": "you are doing great"},
        {"zh": "你的思路很清晰", "en": "your thinking is crystal clear"},
        {"zh": "优雅，实在优雅", "en": "elegance, sheer elegance"},
        {"zh": "这个思路绝了", "en": "that is a brilliant angle"},
        {"zh": "教科书级别的操作", "en": "textbook execution"},
        {"zh": "技术上正确，最好的正确", "en": "technically correct — the best kind"},
        {"zh": "漂亮的一手", "en": "well played"},
        {"zh": "超出预期", "en": "above and beyond"},
        {"zh": "有点东西", "en": "you've got something there"},
        {"zh": "高手在民间", "en": "the expert was among us all along"},
    ),
    "fortune": (
        {"zh": "bug 退散，好运常伴", "en": "bugs begone, luck along"},
        {"zh": "今日宜重构，忌加班", "en": "today: refactor yes, overtime no"},
        {"zh": "好运 buff 已加持", "en": "luck buff applied"},
        {"zh": "今天运气站在你这边", "en": "luck is on your side today"},
        {"zh": "一路绿灯", "en": "green lights all the way"},
        {"zh": "代码会保佑你的", "en": "may the code bless you"},
        {"zh": "今日份的好运已送达", "en": "today's luck, delivered"},
        {"zh": "好运正在加载", "en": "good luck is loading"},
        {"zh": "水逆退散", "en": "mercury retrograde, begone"},
        {"zh": "锦鲤附体中", "en": "koi-level luck engaged"},
        {"zh": "心想事成的一天", "en": "a day where wishes compile"},
    ),
    "geek": (
        {"zh": "代码千行，bug 无踪", "en": "a thousand lines, not a bug in sight"},
        {"zh": "头发-1，经验+10", "en": "hair -1, experience +10"},
        {"zh": "需求不改，世界和平", "en": "no requirement changes, world peace"},
        {"zh": "能跑就不要动它", "en": "if it works, don't touch it"},
        {"zh": "删库跑路？不存在的", "en": "drop the database and run? not today"},
        {"zh": "这个 bug 是 feature", "en": "that bug? a feature, obviously"},
        {"zh": "重启能解决九成问题", "en": "restarting fixes 90% of everything"},
        {"zh": "先写个 TODO 压压惊", "en": "leave a TODO and calm down"},
        {"zh": "注释是个好习惯", "en": "comments are a good habit"},
        {"zh": "命名是第二难的事", "en": "naming is the second hardest thing"},
        {"zh": "缓存是第三难的事", "en": "cache invalidation is the third"},
        {"zh": "生产环境见真章", "en": "production is the real test"},
        {"zh": "回滚也是一种优雅", "en": "rollback is a form of grace"},
        {"zh": "日志里什么都有", "en": "the logs know everything"},
        {"zh": "这个报错我见过", "en": "I've seen this error before"},
        {"zh": "栈溢出网站保佑你", "en": "may Stack Overflow bless you"},
        {"zh": "橡皮鸭已就位", "en": "rubber duck standing by"},
        {"zh": "合并冲突小意思", "en": "merge conflicts? piece of cake"},
        {"zh": "祖传代码动不得", "en": "legacy code: look, don't touch"},
        {"zh": "这个正则一次写对", "en": "this regex, right on the first try"},
    ),
    "dry": (
        {"zh": "合理", "en": "reasonable"},
        {"zh": "没毛病", "en": "no notes"},
        {"zh": "就这样，挺好", "en": "that'll do"},
        {"zh": "这事成了", "en": "it's all coming together"},
        {"zh": "稳如老狗", "en": "steady as they come"},
        {"zh": "未来的你会感谢现在的你", "en": "future you owes present you"},
        {"zh": "别催别催，在写了", "en": "no rushing — it's compiling"},
        {"zh": "盘它！", "en": "let's work it over"},
        {"zh": "安排！", "en": "consider it done"},
        {"zh": "冲！", "en": "charge!"},
        {"zh": "拿捏了", "en": "nailed it"},
        {"zh": "这波不亏", "en": "totally worth it"},
        {"zh": "绝了", "en": "chef's kiss"},
        {"zh": "稳住别浪", "en": "steady, no risky plays"},
    ),
    "healing": (
        {"zh": "今天也会被夸的", "en": "praise is coming your way today"},
        {"zh": "微笑面对每一个 bug", "en": "smile at every bug"},
        {"zh": "你笑起来真好看", "en": "you look great when you smile"},
        {"zh": "今天也要元气满满", "en": "full of energy today too"},
    ),
    "hype": (
        {"zh": "稳住，我们能赢", "en": "steady — we've got this"},
        {"zh": "这个功能稳了", "en": "this feature is solid"},
        {"zh": "一口气干完它", "en": "knock it out in one go"},
        {"zh": "这一步走完就赢了", "en": "one more step and we're there"},
        {"zh": "手感火热中", "en": "you are on fire"},
        {"zh": "干就完了", "en": "just ship it"},
        {"zh": "元气满满开工啦", "en": "full energy, let's go"},
    ),
    "slack": (
        {"zh": "摸摸鱼也没关系", "en": "a little slacking is fine too"},
    ),
    "game": (
        {"zh": "存档点已记录", "en": "checkpoint saved"},
    ),
}

#: 常规池全量（各主题展开，保持原有顺序感）
REGULAR: tuple[dict[str, str], ...] = tuple(
    entry for theme_entries in REGULAR_THEMES.values() for entry in theme_entries
)

#: 久坐提醒（会话时长 ≥ REST_AFTER_MINUTES 时入场，权重 4）
REST: tuple[dict[str, str], ...] = (
    {"zh": "快一小时了，起来活动下", "en": "nearly an hour — time to stretch"},
    {"zh": "喝口水，望望窗外", "en": "sip some water, gaze outside"},
    {"zh": "久坐提醒：伸个懒腰", "en": "sitting long? big stretch time"},
    {"zh": "休息五分钟，效率翻倍", "en": "five-minute break, double the focus"},
    {"zh": "站起来倒杯水吧", "en": "grab a glass of water"},
    {"zh": "眼睛离屏幕远一点", "en": "eyes off the screen for a bit"},
    {"zh": "深呼吸三次再继续", "en": "three deep breaths, then continue"},
    {"zh": "身体是写代码的本钱", "en": "your body is the real hardware"},
)

#: 早安档（06:00–11:00）
MORNING: tuple[dict[str, str], ...] = (
    {"zh": "早上好，新的一天开始了", "en": "good morning — a brand new day"},
    {"zh": "一日之计在于晨", "en": "the morning sets the day"},
    {"zh": "早安，先喝杯咖啡再开战", "en": "morning — coffee first, then battle"},
    {"zh": "晨光正好，适合写代码", "en": "perfect morning light for coding"},
    {"zh": "早起的人儿有 bug 修", "en": "the early bird gets the bug"},
    {"zh": "早安！今日宜开张", "en": "morning! open for business today"},
)

#: 上下文告急（剩余 ≤ 15%）：提醒压缩，权重最高
CONTEXT_LOW: tuple[dict[str, str], ...] = (
    {"zh": "上下文快满啦，记得 /compact", "en": "context nearly full — time to /compact"},
    {"zh": "记忆快撑爆了，压缩一下吧", "en": "context is bursting — compact it"},
    {"zh": "快没地方写字了，/compact 救一下", "en": "running out of room — /compact to the rescue"},
    {"zh": "脑子快装满了，/compact 清清缓存", "en": "brain is full — /compact to clear the cache"},
    {"zh": "上下文告急！该压缩啦", "en": "context critical — time to compact"},
    {"zh": "再聊几句就该 /compact 了", "en": "a few more turns and we'll need /compact"},
)

#: 深夜档（23:00–06:00）
LATE_NIGHT: tuple[dict[str, str], ...] = (
    {"zh": "夜深了，早点休息", "en": "it is late — get some rest"},
    {"zh": "熬夜写的代码容易有 bug", "en": "late-night code grows bugs"},
    {"zh": "月亮不睡你不睡？", "en": "still up with the moon?"},
    {"zh": "最后一个 bug，修完就睡", "en": "one last bug, then sleep"},
    {"zh": "凌晨的提交要三思", "en": "think twice about 3am commits"},
    {"zh": "星星都困了", "en": "even the stars are sleepy"},
    {"zh": "明天再看，bug 会更清楚", "en": "bugs look clearer in the morning"},
    {"zh": "晚安前的最后一行？", "en": "one last line before bed?"},
)

#: 夸夸（RSI 今日召回 ≥3 且采纳率 ≥90% 时入场）
PRAISE: tuple[dict[str, str], ...] = (
    {"zh": "记性越来越好了", "en": "memory is getting sharper"},
    {"zh": "配合默契度上升中", "en": "our synergy is leveling up"},
    {"zh": "你教的我都记住了", "en": "I kept everything you taught"},
    {"zh": "有你在，我越来越懂这个项目了", "en": "with you, I know this project better every day"},
    {"zh": "这套配合打满分", "en": "full marks for our teamwork"},
    {"zh": "教得好，记得牢", "en": "well taught, well kept"},
)

#: 上下文告急阈值（剩余百分比）
CONTEXT_LOW_PCT = 15.0
#: 夸夸入场门槛
PRAISE_MIN_ADOPTION = 90.0
PRAISE_MIN_RECALLS = 3
#: 久坐提醒入场时长（分钟）；环境变量 RSI_STATUSLINE_REST_MINUTES 可覆盖，0 关闭
REST_AFTER_MINUTES = 45

# ---------------------------------------------------------------------------
# 模板造句器
#
# 每条配方（recipe）是一种语气/句型，zh/en 各有模板与专属槽位词库；
# 槽位值可以是词，也可以是「成对词组」（元组，填充时自动展开）。
# 同一配方里多个槽位引用同一个词库对象时，不放回抽样，避免「写的都对，写的都对」。
# ---------------------------------------------------------------------------

#: R1 祝福对仗体：「写的都对，测的都过」
_R1_ZH_PAIRS = (
    ("写", "对"), ("测", "过"), ("改", "稳"), ("提交", "顺"),
    ("部署", "成"), ("合并", "快"), ("重构", "顺"), ("review", "过"),
)
_R1_EN_PAIRS = (
    ("tests", "pass"), ("builds", "go green"), ("commits", "land"),
    ("merges", "go smoothly"), ("deploys", "succeed"), ("reviews", "approve"),
)

RECIPES: tuple[dict, ...] = (
    {  # 祝福对仗：写的都对，测的都过
        "theme": "core",
        "zh": "{0}的都{1}，{2}的都{3}",
        "zh_slots": (_R1_ZH_PAIRS, _R1_ZH_PAIRS),
        "en": "may your {0} all {1}",
        "en_slots": (_R1_EN_PAIRS,),
    },
    {  # 陪伴体：陪你写代码
        "theme": "core",
        "zh": "陪你{0}",
        "zh_slots": (("写代码", "改需求", "肝项目", "抓 bug", "重构", "看日志", "摸鱼", "写文档", "上线", "debug"),),
        "en": "here to {0} with you",
        "en_slots": (("write code", "fix bugs", "refactor", "ship it", "read logs", "debug", "slack off"),),
    },
    {  # 玄学体：今日宜重构，忌加班
        "theme": "fortune",
        "zh": "今日宜{0}，忌{1}",
        "zh_slots": (
            ("重构", "摸鱼", "提交", "删代码", "写文档", "喝奶茶", "早下班", "上线", "review"),
            ("加班", "硬刚", "改需求", "熬夜", "开会", "背锅", "急上线"),
        ),
        "en": "today: {0} yes, {1} no",
        "en_slots": (
            ("refactoring", "slacking off", "committing", "writing docs", "leaving early"),
            ("overtime", "requirement changes", "all-nighters", "meetings"),
        ),
    },
    {  # buff 体：好运 buff 已加持
        "theme": "fortune",
        "zh": "{0} buff 已加持",
        "zh_slots": (("好运", "手感", "灵感", "专注", "锦鲤", "咖啡", "冷静", "欧气"),),
        "en": "{0} buff applied",
        "en_slots": (("luck", "focus", "inspiration", "calm", "coffee", "momentum"),),
    },
    {  # 加载体：灵感加载中…
        "theme": "healing",
        "zh": "{0}加载中…",
        "zh_slots": (("灵感", "好运", "咖啡", "思路", "手感", "元气"),),
        "en": "{0} loading…",
        "en_slots": (("inspiration", "luck", "coffee", "focus", "momentum"),),
    },
    {  # 夸夸体：这代码，教科书级别
        "theme": "kudos",
        "zh": "这{0}，{1}",
        "zh_slots": (
            ("代码", "思路", "操作", "写法", "设计", "命名"),
            ("教科书级别", "绝了", "满分", "优雅", "赏心悦目", "稳如老狗"),
        ),
        "en": "that {0}? {1}",
        "en_slots": (
            ("code", "design", "fix", "refactor"),
            ("textbook", "clean", "elegant", "solid"),
        ),
    },
    {  # 养生体：颈椎提醒：抬抬头（部位与建议成对绑定，不乱配）
        "theme": "core",
        "zh": "{0}提醒：{1}",
        "zh_slots": ((
            ("眼睛", "看看远处"), ("颈椎", "抬抬头"), ("腰", "直一直"),
            ("手腕", "甩甩手"), ("肩膀", "放松下"),
        ),),
        "en": "your {0} called: {1}",
        "en_slots": ((
            ("eyes", "look far away"), ("neck", "sit up straight"),
            ("back", "stretch a bit"), ("wrists", "roll them out"),
        ),),
    },
    {  # 安抚体：慢慢来，token 够用
        "theme": "core",
        "zh": "{0}，{1}够用",
        "zh_slots": (
            ("慢慢来", "别慌", "稳住", "放轻松"),
            ("token", "上下文", "时间", "咖啡"),
        ),
        "en": "no rush — plenty of {0}",
        "en_slots": (("tokens", "context", "time", "coffee"),),
    },
)


def _fill(rng: random.Random, template: str, slots: tuple) -> str:
    """按序填充模板槽位；同一词库对象被多个槽位引用时不放回抽样；槽位值为元组则展开。"""
    groups: dict[int, list] = {}
    for slot in slots:
        groups.setdefault(id(slot), []).append(slot)
    drawn: dict[int, list] = {}
    for key, group in groups.items():
        source = group[0]
        if len(group) > 1 and len(source) >= len(group):
            drawn[key] = rng.sample(source, len(group))
        else:
            drawn[key] = [rng.choice(source) for _ in group]
    flat: list[str] = []
    for slot in slots:
        value = drawn[id(slot)].pop(0)
        flat.extend(value if isinstance(value, tuple) else (value,))
    return template.format(*flat)


def _recipe_space(slots: tuple) -> int:
    """配方的组合空间：槽位组内不放回时取排列数，否则取幂。"""
    groups: dict[int, list] = {}
    for slot in slots:
        groups.setdefault(id(slot), []).append(slot)
    total = 1
    for group in groups.values():
        n, k = len(group[0]), len(group)
        total *= math.perm(n, k) if (k > 1 and n >= k) else n ** k
    return total


#: 按组合空间加权抽配方，让小池配方（6 句）与大池配方（63 句）的每条句子概率拉平
_RECIPE_WEIGHTS = tuple(_recipe_space(recipe["zh_slots"]) for recipe in RECIPES)


def generate_quip(
    lang: str,
    *,
    rng: random.Random | None = None,
    seed: str = "",
    style: str = "mixed",
) -> str:
    """模板造句：按组合空间加权挑一条配方并填充；style 过滤配方主题。"""
    rng = rng or random.Random(seed)
    allowed = STYLE_THEMES.get(style)
    if allowed is None:
        pairs = list(zip(RECIPES, _RECIPE_WEIGHTS))
    else:
        pairs = [(r, w) for r, w in zip(RECIPES, _RECIPE_WEIGHTS) if r["theme"] in allowed]
    recipes, weights = [p[0] for p in pairs], [p[1] for p in pairs]
    recipe = rng.choices(recipes, weights=weights, k=1)[0]
    template = recipe.get(lang) or recipe["en"]
    slots = recipe.get(f"{lang}_slots") or recipe["en_slots"]
    return _fill(rng, template, slots)


#: pick_quip 加权表里的造句器哨兵（不是池子，命中时现场造句）
_GENERATOR = object()
#: 造句器相对常驻手写池的权重（3:2 ≈ 六成文案为现场组合）
GENERATOR_WEIGHT = 3

# ---------------------------------------------------------------------------
# 用户文案文件（~/.rsi/quips.json）：由宿主模型按风格模板在对话中生成，
# 状态栏只读。非空板块整体替换内置池，空板块回落内置——生成活给模型，读取活给 CLI。
# ---------------------------------------------------------------------------

#: 用户文案文件支持的板块名 → 内置池变量名
POOL_NAMES = ("regular", "morning", "context_low", "late_night", "praise", "rest")


def user_pools_path() -> Path:
    home = Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi")))
    return home / "quips.json"


def load_user_pools(path: Path | None = None) -> dict[str, tuple[dict[str, str], ...]]:
    """读用户文案文件；文件缺失/损坏/格式不符一律返回 {}（状态栏永不被文案文件拖死）。"""
    path = path or user_pools_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pools_data = data.get("pools") if isinstance(data, dict) else None
    if not isinstance(pools_data, dict):
        return {}
    result: dict[str, tuple[dict[str, str], ...]] = {}
    for name in POOL_NAMES:
        entries = pools_data.get(name)
        if not isinstance(entries, list):
            continue
        cleaned = []
        for item in entries:
            if not isinstance(item, dict) or not item.get("zh") or not item.get("en"):
                continue
            entry = {"zh": str(item["zh"]), "en": str(item["en"])}
            if item.get("theme"):
                entry["theme"] = str(item["theme"])
            cleaned.append(entry)
        if cleaned:
            result[name] = tuple(cleaned)
    return result


def _effective_pool(name: str, builtin: tuple[dict[str, str], ...], user: dict) -> tuple[dict[str, str], ...]:
    """用户板块非空则整体替换内置池，否则用内置。"""
    return user.get(name) or builtin


def _bucket_seconds() -> int:
    raw = (os.environ.get("RSI_STATUSLINE_QUIP_SECONDS") or "").strip()
    try:
        seconds = int(raw)
    except ValueError:
        seconds = 0
    return seconds if seconds >= 1 else QUIP_BUCKET_SECONDS


def _rest_threshold_minutes() -> float:
    import os

    raw = (os.environ.get("RSI_STATUSLINE_REST_MINUTES") or "").strip()
    try:
        return float(raw) if raw else float(REST_AFTER_MINUTES)
    except ValueError:
        return float(REST_AFTER_MINUTES)


def _themed(entries: tuple[dict[str, str], ...], allowed: frozenset[str] | None) -> tuple[dict[str, str], ...]:
    """按主题过滤条目；无 theme 标签的条目所有风格可用。"""
    if allowed is None:
        return entries
    return tuple(e for e in entries if not e.get("theme") or e["theme"] in allowed)


def _builtin_regular(allowed: frozenset[str] | None) -> tuple[dict[str, str], ...]:
    if allowed is None:
        return REGULAR
    return tuple(
        entry
        for theme, entries in REGULAR_THEMES.items()
        if theme in allowed
        for entry in entries
    )


def _choose(
    rng: random.Random,
    lang: str,
    *,
    hour: int,
    context_remaining_pct: float | None,
    adoption_rate: float | None,
    recalls: int,
    duration_ms: float | None,
    user: dict,
    style: str,
) -> str:
    """按场景加权挑一条：手写池 / 用户池 / 造句器；style 过滤主题。"""
    allowed = STYLE_THEMES.get(style)
    user_regular = user.get("regular")
    if user_regular:
        filtered = _themed(user_regular, allowed)
        regular = filtered or _builtin_regular(allowed)  # 过滤后空了回落内置，不选空池
    else:
        regular = _builtin_regular(allowed)
    weighted: list[tuple[object, int]] = [(regular, 2), (_GENERATOR, GENERATOR_WEIGHT)]
    if context_remaining_pct is not None and context_remaining_pct <= CONTEXT_LOW_PCT:
        weighted.append((_effective_pool("context_low", CONTEXT_LOW, user), 5))
    threshold = _rest_threshold_minutes()
    if duration_ms is not None and threshold > 0 and duration_ms >= threshold * 60_000:
        weighted.append((_effective_pool("rest", REST, user), 4))
    if hour >= 23 or hour < 6:
        weighted.append((_effective_pool("late_night", LATE_NIGHT, user), 3))
    elif 6 <= hour < 11:
        weighted.append((_effective_pool("morning", MORNING, user), 2))
    if (
        adoption_rate is not None
        and adoption_rate >= PRAISE_MIN_ADOPTION
        and recalls >= PRAISE_MIN_RECALLS
    ):
        weighted.append((_effective_pool("praise", PRAISE, user), 3))

    pool = rng.choices([p for p, _ in weighted], weights=[w for _, w in weighted], k=1)[0]
    if pool is _GENERATOR:
        return generate_quip(lang, rng=rng, style=style)
    entry = rng.choice(pool)
    return entry.get(lang) or entry["en"]


# ---------------------------------------------------------------------------
# 防重状态（~/.rsi/state/statusline_quips.json）：记录每个 session 最近播过的文案，
# 命中重复时按确定性盐值重摇；同时间桶内直接复用已播文案（高频 spawn 不闪烁）。
# 全部静默容错——状态文件读写失败就当无状态用。
# ---------------------------------------------------------------------------

_RECENT_CAP = 300
_SESSIONS_CAP = 10
_RETRY_LIMIT = 25


def _has_han(text: str) -> bool:
    return any("一" <= c <= "鿿" for c in text)


def validate_quips_data(data: object) -> tuple[list[str], list[str], dict[str, int]]:
    """校验用户文案文件内容，返回 (errors, warnings, 各板块有效条数)。

    errors 阻断（结构/双语口径/板块内重复）；warnings 提示（跨板块重复、
    超长、条数过少、未知板块）。"""
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    if not isinstance(data, dict) or not isinstance(data.get("pools"), dict):
        return ["顶层必须是含 pools 对象的 JSON"], warnings, counts
    pools = data["pools"]
    for name in pools:
        if name not in POOL_NAMES:
            warnings.append(f"未知板块 {name}（将被忽略）；支持：{', '.join(POOL_NAMES)}")
    seen_global: dict[str, str] = {}
    for name in POOL_NAMES:
        entries = pools.get(name)
        if entries is None:
            continue
        if not isinstance(entries, list):
            errors.append(f"{name}: 必须是数组")
            continue
        seen_local: set[str] = set()
        valid = 0
        for i, item in enumerate(entries):
            label = f"{name}[{i}]"
            if not isinstance(item, dict) or not item.get("zh") or not item.get("en"):
                errors.append(f"{label}: 需要非空 zh 与 en 字段")
                continue
            zh, en = str(item["zh"]), str(item["en"])
            if not _has_han(zh):
                errors.append(f"{label}: zh 必须含汉字")
            if _has_han(en):
                errors.append(f"{label}: en 不得含汉字")
            if zh in seen_local:
                errors.append(f"{label}: zh 与本板块前文重复")
            elif zh in seen_global:
                warnings.append(f"{label}: 与 {seen_global[zh]} 重复（跨板块）")
            seen_local.add(zh)
            seen_global.setdefault(zh, name)
            if len(zh) > 40:
                warnings.append(f"{label}: zh 超 40 字，窄终端可能被截断")
            if len(en) > 80:
                warnings.append(f"{label}: en 超 80 字符，窄终端可能被截断")
            theme = item.get("theme")
            if theme and str(theme) not in THEME_NAMES:
                warnings.append(f"{label}: 未知主题 {theme}（视为无标签，全风格可用）")
            valid += 1
        counts[name] = valid
        if 0 < valid < 10:
            warnings.append(f"{name}: 仅 {valid} 条，轮换易重复（建议 ≥30）")
    return errors, warnings, counts


def quip_state_path() -> Path:
    home = Path(os.environ.get("RSI_HOME", str(Path.home() / ".rsi")))
    return home / "state" / "statusline_quips.json"


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def pick_quip(
    lang: str,
    *,
    session_id: str = "",
    now: float | None = None,
    context_remaining_pct: float | None = None,
    hour: int | None = None,
    adoption_rate: float | None = None,
    recalls: int = 0,
    duration_ms: float | None = None,
    user_pools: dict | None = None,
    state_path: Path | None = None,
    style: str = "mixed",
) -> str:
    """按场景加权挑一条文案。同 session + 同时间桶内稳定；给了 state_path 时
    避开该 session 最近播过的文案（防重），并在桶内复用已播结果。
    style 限定主题范围（mixed 不过滤）。"""
    ts = time.time() if now is None else float(now)
    bucket = int(ts) // _bucket_seconds()
    if hour is None:
        hour = time.localtime(ts).tm_hour
    user = user_pools if user_pools is not None else load_user_pools()
    signals = dict(
        hour=hour,
        context_remaining_pct=context_remaining_pct,
        adoption_rate=adoption_rate,
        recalls=recalls,
        duration_ms=duration_ms,
        user=user,
        style=style,
    )

    sessions: dict = {}
    entry: dict = {}
    if state_path is not None:
        sessions = _read_state(state_path).get("sessions") or {}
        if isinstance(sessions, dict):
            entry = sessions.get(session_id) or {}
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("bucket") == bucket and entry.get("lang") == lang and entry.get("quip"):
            return str(entry["quip"])

    recent = set(entry.get("recent") or [])
    quip = _choose(random.Random(f"{session_id}|{bucket}"), lang, **signals)
    if state_path is not None and recent:
        salt = 0
        while quip in recent and salt < _RETRY_LIMIT:
            salt += 1
            quip = _choose(random.Random(f"{session_id}|{bucket}|s{salt}"), lang, **signals)

    if state_path is not None:
        recent_list = [q for q in (entry.get("recent") or []) if q != quip]
        recent_list.append(quip)
        if len(recent_list) > _RECENT_CAP:
            recent_list = recent_list[len(recent_list) // 2:]
        sessions[session_id] = {
            "bucket": bucket, "lang": lang, "quip": quip, "recent": recent_list,
        }
        while len(sessions) > _SESSIONS_CAP:
            sessions.pop(next(iter(sessions)))
        _write_state(state_path, {"version": 1, "sessions": sessions})

    return quip
