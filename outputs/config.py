"""
config.py
----------
全局配置模块：环境变量加载、状态枚举、状态流转规则、时区常量。

约定：
- 所有密钥/凭据均通过 os.getenv() 读取，不在代码中硬编码。
- load_dotenv(find_dotenv(), override=True) 保证本地 .env 文件优先级最高，
  便于本地开发时覆盖系统环境变量。
"""

import os
from dotenv import load_dotenv, find_dotenv

# 必须在读取任何 os.getenv 之前调用
load_dotenv(find_dotenv(), override=True)

# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("JOB_TRACKER_DB_PATH", "job_tracker.db")

# ---------------------------------------------------------------------------
# 时区
# ---------------------------------------------------------------------------
# 目标展示时区：美国达拉斯所在时区（自动处理夏令时，不要硬编码 UTC-5/UTC-6）
TARGET_TIMEZONE = "America/Chicago"
# 邮件中面试时间若未显式标注时区，默认按国内招聘场景假设为北京时间
DEFAULT_SOURCE_TIMEZONE = "Asia/Shanghai"

# ---------------------------------------------------------------------------
# 状态枚举与流转规则
# ---------------------------------------------------------------------------
# 正常推进链路（严格顺序）
STATUS_FLOW = [
    "已投递",
    "简历筛选中",
    "笔试",
    "一面",
    "二面",
    "三面",
    "HR面",
    "已收offer",
]

# 任意阶段都可以转入的终止态
TERMINAL_STATUSES = ["已拒绝", "已终止"]

# 全部合法状态
ALL_STATUSES = STATUS_FLOW + TERMINAL_STATUSES

# 记录来源
SOURCES = ["screenshot", "email", "manual"]

# 面试轮次的常见别名 -> 标准状态映射（供 LLM 提示词与解析结果归一化使用）
ROUND_ALIASES = {
    "笔试": "笔试",
    "在线测评": "笔试",
    "初试": "一面",
    "一面": "一面",
    "第一轮面试": "一面",
    "复试": "二面",
    "二面": "二面",
    "第二轮面试": "二面",
    "三面": "三面",
    "第三轮面试": "三面",
    "终面": "三面",
    "hr面": "HR面",
    "hr面试": "HR面",
    "人事面": "HR面",
    "offer": "已收offer",
    "录用通知": "已收offer",
}


def normalize_status(raw_status: str) -> str:
    """将 LLM/规则解析出的原始文本归一化为 ALL_STATUSES 中的合法值。

    如果无法识别，回退到 "已投递"，避免脏数据写入数据库。
    """
    if not raw_status:
        return "已投递"
    text = raw_status.strip()
    if text in ALL_STATUSES:
        return text
    lowered = text.lower()
    if lowered in ROUND_ALIASES:
        return ROUND_ALIASES[lowered]
    if text in ROUND_ALIASES:
        return ROUND_ALIASES[text]
    return "已投递"


def get_valid_next_statuses(current_status: str):
    """返回给定当前状态下，UI 中允许选择的下一状态列表。

    规则：
    - 终止态（已拒绝/已终止）不再允许流转。
    - 正常链路上的状态，只能前进到下一个正常状态，或随时转入终止态。
    - 已到达链路终点（已收offer）时，只能转入终止态。
    """
    if current_status in TERMINAL_STATUSES:
        return []
    if current_status not in STATUS_FLOW:
        # 异常兜底：未知状态，允许从头开始选择
        return STATUS_FLOW[:1] + TERMINAL_STATUSES
    idx = STATUS_FLOW.index(current_status)
    options = []
    if idx + 1 < len(STATUS_FLOW):
        options.append(STATUS_FLOW[idx + 1])
    options.extend(TERMINAL_STATUSES)
    return options


# 面试阶段起点：达到该状态即视为"进入面试环节"（用于统计口径备用）
INTERVIEW_START_STATUS = "笔试"

# ---------------------------------------------------------------------------
# 状态颜色（面试月历、投递看板等可视化模块统一取色，避免各处重复定义/不一致）
# ---------------------------------------------------------------------------
# STATUS_FLOW 上的 8 个阶段使用同一蓝色系顺序色阶，阶段越靠后颜色越深，符合
# "漏斗越往下越深" 的直觉；已收offer/已拒绝/已终止是流转终点而非阶段，
# 分别用固定的成功/严重/中性状态色，避免和阶段色混淆。
STATUS_COLOR = {
    "已投递": "#86b6ef",
    "简历筛选中": "#6da7ec",
    "笔试": "#5598e7",
    "一面": "#3987e5",
    "二面": "#2a78d6",
    "三面": "#256abf",
    "HR面": "#1c5cab",
    "已收offer": "#0ca30c",
    "已拒绝": "#ec835a",  # 用状态色板里偏柔和的"serious"珊瑚色，不用刺眼的警示红
    "已终止": "#898781",
}

# 停滞提醒的标注色（暖黄色，实际渲染时会叠加透明度做成柔和的色块，见 ui_pipeline.py）
STALE_WARNING_COLOR = "#fab219"

# 某一状态停留超过这个天数（相邻两次状态流转的间隔），投递看板中会标黄提醒
STALE_DAYS_THRESHOLD = 7

# ---------------------------------------------------------------------------
# 岗位分类
# ---------------------------------------------------------------------------
CATEGORIES = ["开发", "测试", "产品", "运营", "数据", "算法/AI", "设计", "市场/销售", "人力/财务/行政", "其他"]

DEFAULT_CATEGORY = "其他"


def normalize_category(raw_category: str) -> str:
    """将 LLM/表单输入的岗位分类归一化为 CATEGORIES 中的合法值。

    无法识别时回退到"其他"，避免脏数据写入数据库（做法与 normalize_status 一致）。
    """
    if not raw_category:
        return DEFAULT_CATEGORY
    text = raw_category.strip()
    return text if text in CATEGORIES else DEFAULT_CATEGORY
