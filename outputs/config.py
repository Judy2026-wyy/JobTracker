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
# 展示时区的出厂默认值。真正生效的时区由用户在侧边栏「时区设置」里选，存在
# 数据库的 app_settings 表里（见 db.get_setting / timezone_utils.get_display_timezone），
# 这里只是没设置过时的兜底。一律用 IANA 时区名，不硬编码 UTC±N 偏移量——
# zoneinfo 会按具体日期自动处理夏令时切换。
DEFAULT_DISPLAY_TIMEZONE = "America/Chicago"
# 邮件中面试时间若未显式标注时区，默认按国内招聘场景假设为北京时间
DEFAULT_SOURCE_TIMEZONE = "Asia/Shanghai"

# app_settings 表里的键名
SETTING_DISPLAY_TIMEZONE = "display_timezone"
SETTING_DISPLAY_TIMEZONE_LABEL = "display_timezone_label"

# 中国全境行政上统一使用北京时间，所以选了中国就不必再问城市
CHINA_COUNTRY = "中国"
CHINA_TIMEZONE = "Asia/Shanghai"
CHINA_CITY_LABEL = "北京时间（全国统一）"
# 列表里没有的地方，让用户直接填 IANA 时区名
CUSTOM_COUNTRY = "其他（手动填时区名）"

# 国家/地区 -> {城市档位: IANA 时区名}
# 只收录求职者常去的国家，跨时区的国家按"时区档位"列城市（同一档位的城市共用
# 一个时区，写在一起便于用户按自己所在城市对号入座），单时区国家只有一项。
COUNTRY_TIMEZONES: dict[str, dict[str, str]] = {
    CHINA_COUNTRY: {CHINA_CITY_LABEL: CHINA_TIMEZONE},
    "美国": {
        "纽约 / 波士顿 / 华盛顿（东部时间）": "America/New_York",
        "芝加哥 / 达拉斯 / 休斯顿（中部时间）": "America/Chicago",
        "丹佛 / 盐湖城（山地时间）": "America/Denver",
        "凤凰城（山地时间，不实行夏令时）": "America/Phoenix",
        "洛杉矶 / 旧金山 / 西雅图（太平洋时间）": "America/Los_Angeles",
        "安克雷奇（阿拉斯加时间）": "America/Anchorage",
        "檀香山（夏威夷时间）": "Pacific/Honolulu",
    },
    "加拿大": {
        "多伦多 / 渥太华 / 蒙特利尔（东部时间）": "America/Toronto",
        "温尼伯（中部时间）": "America/Winnipeg",
        "卡尔加里 / 埃德蒙顿（山地时间）": "America/Edmonton",
        "温哥华 / 维多利亚（太平洋时间）": "America/Vancouver",
        "哈利法克斯（大西洋时间）": "America/Halifax",
    },
    "英国": {"伦敦 / 曼彻斯特 / 爱丁堡": "Europe/London"},
    "爱尔兰": {"都柏林": "Europe/Dublin"},
    "德国": {"柏林 / 慕尼黑 / 法兰克福": "Europe/Berlin"},
    "法国": {"巴黎": "Europe/Paris"},
    "荷兰": {"阿姆斯特丹": "Europe/Amsterdam"},
    "瑞士": {"苏黎世 / 日内瓦": "Europe/Zurich"},
    "澳大利亚": {
        "悉尼 / 墨尔本 / 堪培拉（东部时间）": "Australia/Sydney",
        "布里斯班（东部时间，不实行夏令时）": "Australia/Brisbane",
        "阿德莱德（中部时间）": "Australia/Adelaide",
        "珀斯（西部时间）": "Australia/Perth",
    },
    "新西兰": {"奥克兰 / 惠灵顿": "Pacific/Auckland"},
    "新加坡": {"新加坡": "Asia/Singapore"},
    "日本": {"东京 / 大阪": "Asia/Tokyo"},
    "韩国": {"首尔": "Asia/Seoul"},
    "马来西亚": {"吉隆坡": "Asia/Kuala_Lumpur"},
    "印度": {"班加罗尔 / 孟买 / 新德里": "Asia/Kolkata"},
    "阿联酋": {"迪拜 / 阿布扎比": "Asia/Dubai"},
    CUSTOM_COUNTRY: {},
}


def find_timezone_location(tz_name: str) -> tuple[str, str] | None:
    """IANA 时区名 -> (国家/地区, 城市档位)；列表里没有则返回 None。

    用于把已保存的时区反查回下拉框该选哪两项。
    """
    for country, cities in COUNTRY_TIMEZONES.items():
        for city, name in cities.items():
            if name == tz_name:
                return country, city
    return None


def build_timezone_label(country: str, city: str) -> str:
    """给用户看的时区说明，如「中国 · 北京时间（全国统一）」。"""
    return f"{country} · {city}" if city else country

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


def is_forward_status(current_status: str, new_status: str) -> bool:
    """new_status 相对 current_status 是不是「往前推进」（含转入终止态）。

    与 get_valid_next_statuses 的区别：那个函数管的是「用户在界面上手动流转」，
    只允许一步一步走；这个函数管的是「外部信息（邮件）告诉我们进度已经到哪了」，
    允许一次跨多个阶段（投完简历直接收到二面通知是常事），但绝不允许倒退，
    免得一封旧邮件把已经推进到 HR 面的记录打回「已投递」。
    """
    if not new_status or new_status == current_status:
        return False
    if current_status in TERMINAL_STATUSES:
        return False  # 已拒绝/已终止是终点，不再变
    if new_status in TERMINAL_STATUSES:
        return True
    if current_status not in STATUS_FLOW or new_status not in STATUS_FLOW:
        return False
    return STATUS_FLOW.index(new_status) > STATUS_FLOW.index(current_status)


def status_depth(status: str) -> int:
    """状态在推进链路上的深度（STATUS_FLOW 下标）；不在链路上的返回 -1。

    终止态（已拒绝/已终止）返回 -1：它们不是"走到了哪一步"，而是"结束了"。
    一条投递到底走到过多深，要看它的历史轨迹里非终止状态的最大深度，
    不能看当前状态——状态一旦转成已拒绝就把之前的进度覆盖掉了。
    """
    return STATUS_FLOW.index(status) if status in STATUS_FLOW else -1


# ---------------------------------------------------------------------------
# 投递漏斗（投递看板的漏斗分析图）
# ---------------------------------------------------------------------------
# 每一层： (显示名, 进入该层所需达到的最低状态)。
# 口径是"至少走到了这一层"：某条投递只要历史上到过 >= 该状态的深度就计入，
# 所以层层递减，天然构成漏斗。
#
# 与 STATUS_FLOW 的三点差异（按需求裁剪，只影响这张图，不影响状态流转本身）：
# 1. 「已投递」与「简历筛选中」合并成同一层——简历筛选是投递之后的被动等待，
#    分成两层看不出转化，合并后第一层就等于"总投递数"。
# 2. 去掉「HR面」层。但 HR面 仍然计入深度：走到 HR面 的投递在漏斗里会被算进
#    「三面」层（它确实已经走过了三面这一层），只是不单独占一层。
# 3. 去掉「已拒绝」层。被拒不是漏斗的一层，而是每一层的流失；它体现在
#    下一层的人数变少上，单独列一层反而会把转化率算重。
FUNNEL_STAGES = [
    ("已投递", "已投递"),
    ("笔试", "笔试"),
    ("一面", "一面"),
    ("二面", "二面"),
    ("三面", "三面"),
    ("已收offer", "已收offer"),
]

# 面试阶段起点：达到该状态即视为"进入面试环节"（用于统计口径备用）
INTERVIEW_START_STATUS = "笔试"

# ---------------------------------------------------------------------------
# 邮件类型（邮箱同步页面按类型分组展示候选记录）
# ---------------------------------------------------------------------------
# 这是"邮件讲的是哪一类事"，跟 CATEGORIES（岗位属于哪个职能方向）是两回事，
# 不要混用。类型直接由 LLM 解析出的 status / 是否含面试时间推导，不额外调用
# 大模型，避免多花一次 token 又引入新的不确定性。
EMAIL_TYPE_WRITTEN_TEST = "笔试"
EMAIL_TYPE_INTERVIEW = "面试"
EMAIL_TYPE_OFFER = "offer通知"
EMAIL_TYPE_OTHER = "其他"

EMAIL_TYPES = [
    EMAIL_TYPE_WRITTEN_TEST,
    EMAIL_TYPE_INTERVIEW,
    EMAIL_TYPE_OFFER,
    EMAIL_TYPE_OTHER,
]

# 属于"面试"这一类的具体轮次状态
INTERVIEW_ROUND_STATUSES = ["一面", "二面", "三面", "HR面"]


def classify_email_type(status: str = "", has_interview_time: bool = False) -> str:
    """把解析出的投递状态归到邮箱同步页面展示用的邮件类型。

    归类优先级：已收offer > 笔试 > 面试（含"邮件里带了具体面试时间"）> 其他。
    识别不出明确进度的求职邮件一律进"其他"。
    """
    if status == "已收offer":
        return EMAIL_TYPE_OFFER
    if status == "笔试":
        return EMAIL_TYPE_WRITTEN_TEST
    if status in INTERVIEW_ROUND_STATUSES or has_interview_time:
        return EMAIL_TYPE_INTERVIEW
    return EMAIL_TYPE_OTHER

# ---------------------------------------------------------------------------
# 状态颜色（面试月历、投递看板等可视化模块统一取色，避免各处重复定义/不一致）
# ---------------------------------------------------------------------------
# 10 个状态的颜色用 OKLab 色差（ΔE，感知均匀色彩空间，能反映人眼真实可辨识度，
# 而不是简单比较 RGB 数值）逐对校验过，确保彼此都能明显区分：
# - 按 STATUS_FLOW 实际流转顺序，相邻两个阶段的色差最小值 17.1（0~100 量级，
#   ≥15 视为可清晰区分），完全覆盖时间线图里"前一阶段色块紧挨着下一阶段色块"
#   的场景。
# - 全部 45 种两两组合里，仅剩 2 对低于 15（14.6 / 14.8，均为流程上不相邻、
#   较少同时出现的状态），且这些颜色本身在色相上仍明显不同，加上界面里颜色
#   始终和状态文字一起展示（从不单独靠颜色传达信息），足够区分。
# 已收offer 固定用绿色（成功语义）、已拒绝固定用红色（终止/负面语义）、
# 已终止用中性灰，三者与其余 7 个流程阶段色刻意避开，不会互相撞色
# （之前版本 笔试/三面/已收offer 三种颜色都是"绿色"、简历筛选中和已拒绝的
# 珊瑚色几乎撞色，是本次重新设计要解决的具体问题）。
STATUS_COLOR = {
    "已投递": "#7454d6",  # 紫罗兰
    "简历筛选中": "#dd6cd6",  # 品红
    "笔试": "#1da6ff",  # 天蓝
    "一面": "#d98b00",  # 琥珀橙
    "二面": "#b43694",  # 洋红/梅红
    "三面": "#0079b3",  # 钢蓝
    "HR面": "#3e3f7c",  # 靛蓝
    "已收offer": "#2f9f3d",  # 状态色·成功（绿）
    "已拒绝": "#d24c49",  # 状态色·负面（红）
    "已终止": "#8a8a86",  # 中性灰
}

# 停滞提醒的标注色（暖黄色，实际渲染时会叠加透明度做成柔和的色块，见 ui_pipeline.py）
STALE_WARNING_COLOR = "#fab219"

# 某一状态停留超过这个天数（相邻两次状态流转的间隔），投递看板中会标黄提醒
STALE_DAYS_THRESHOLD = 7

# ---------------------------------------------------------------------------
# 岗位分类体系 v2
#
# 设计原则：
#   1. 单一维度——只按「职能」切分，不混入行业/技术栈/职级。
#   2. 互斥优先——每个类目给出边界说明，减少模型在相邻类目间摇摆。
#   3. 可扩展——两级结构，新岗位先进子类，子类膨胀了再升为一级类目。
#   4. 兜底保留——「其他」永远排在最后，且不给关键词，强制模型先尝试其他类目。
# ---------------------------------------------------------------------------

# 一级类目 -> 典型岗位/关键词（既作为文档，也直接喂给 LLM 做 few-shot，
# 同时被 normalize_category() 当作倒排表用于兜底纠偏）
JOB_TAXONOMY = {
    # ---------- 技术 ----------
    "后端开发": [
        "后端工程师", "服务端开发", "Java", "Go", "Python", "C++", "C#/.NET",
        "PHP", "Node.js", "Rust", "全栈工程师", "架构师", "中间件", "分布式系统",
        "存储/数据库内核", "区块链", "高性能计算",
    ],
    "前端开发": [
        "前端工程师", "JavaScript", "TypeScript", "React", "Vue", "Web开发",
        "小程序开发", "H5", "可视化开发", "Node全栈（偏前端）",
    ],
    "移动/客户端": [
        "Android", "iOS", "鸿蒙开发", "Flutter", "React Native",
        "桌面客户端", "Unity/U3D", "Unreal/UE", "Cocos", "图形/引擎开发", "音视频开发",
    ],
    "测试/质量保障": [
        "测试工程师", "软件测试", "自动化测试", "测试开发(SDET)", "功能测试",
        "性能测试", "渗透测试", "游戏测试", "硬件测试", "QA经理",
    ],
    "运维/SRE": [
        "运维工程师", "SRE", "系统工程师", "网络工程师", "DBA", "运维开发",
        "云平台工程师", "IT技术支持", "系统管理员", "DevOps", "平台工程",
    ],
    "安全": [
        "安全工程师", "网络安全", "系统安全", "应用安全", "安全研究员",
        "红队/蓝队", "数据安全", "安全合规(技术侧)", "反欺诈技术",
    ],
    "硬件/嵌入式": [
        "嵌入式软件", "驱动开发", "单片机", "FPGA", "IC设计/验证", "芯片",
        "硬件工程师", "电子/电路", "PCB", "射频", "光电", "机械结构", "机器人",
    ],
    "算法": [
        "算法工程师", "推荐算法", "搜索算法", "广告算法", "风控算法",
        "图像/CV算法", "语音算法", "SLAM", "规控算法", "自动驾驶", "运筹优化",
        "机器学习", "深度学习", "数据挖掘",
    ],
    "AI/大模型": [
        "大模型算法", "LLM", "NLP", "多模态", "AIGC", "Agent开发",
        "Prompt工程师", "RAG", "模型微调/训练", "推理加速", "AI应用开发",
        "AI产品工程", "数据标注/AI训练师",
    ],
    "数据": [
        "数据工程师", "大数据开发", "数仓工程师", "ETL", "数据平台",
        "数据分析师", "BI工程师", "商业分析", "数据科学家", "数据治理", "数据产品经理",
    ],
    # ---------- 产品与设计 ----------
    "产品": [
        "产品经理", "产品专员", "B端产品", "C端产品", "策略产品", "供应链产品",
        "硬件产品经理", "游戏策划", "产品运营（偏产品）", "产品总监",
    ],
    "设计": [
        "UI设计师", "UX/交互设计", "用户研究", "视觉设计", "平面设计",
        "品牌设计", "插画", "3D/建模", "动效设计", "游戏美术", "工业设计", "空间/室内设计",
    ],
    # ---------- 业务 ----------
    "运营": [
        "用户运营", "内容运营", "活动运营", "社群运营", "电商运营", "商家运营",
        "直播运营", "新媒体运营", "增长运营", "策略运营", "游戏运营", "运营总监",
    ],
    "市场/品牌/公关": [
        "市场专员", "市场经理", "品牌策划", "公关", "媒介", "广告投放", "SEM/SEO",
        "市场调研", "会展策划", "整合营销", "CMO",
    ],
    "销售/BD": [
        "销售代表", "大客户销售", "渠道销售", "解决方案销售", "售前工程师",
        "商务拓展(BD)", "渠道管理", "外贸销售", "电话销售", "销售总监",
    ],
    "客服/客户成功": [
        "客服专员", "在线客服", "呼叫中心", "客户成功经理(CSM)", "售后服务",
        "技术支持（面客）", "投诉处理", "客服主管",
    ],
    "项目/交付管理": [
        "项目经理", "PMO", "实施工程师", "交付经理", "解决方案架构师",
        "敏捷教练/Scrum Master", "技术项目经理(TPM)",
    ],
    # ---------- 职能 ----------
    "人力资源": [
        "HRBP", "招聘", "薪酬绩效", "员工关系", "组织发展(OD)", "培训（对内）",
        "企业文化", "猎头顾问", "HRD/CHO",
    ],
    "财务": [
        "会计", "出纳", "总账/成本/税务会计", "财务分析/财务BP", "审计",
        "资金管理", "投融资", "证券事务", "CFO",
    ],
    "法务/合规/风控": [
        "法务专员", "法律顾问", "律师", "知识产权", "合规经理",
        "业务风控", "内控", "反洗钱", "ESG/可持续发展",
    ],
    "行政/后勤": [
        "行政专员", "文员", "前台", "助理/秘书", "后勤", "资产管理",
        "办公室主任", "行政总监",
    ],
    "供应链/物流/采购": [
        "采购专员", "采购经理", "供应商开发", "供应链管理", "计划(PMC)",
        "仓储管理", "物流运营", "报关/关务", "货代", "配送/快递", "买手",
    ],
    "生产制造/工艺": [
        "工艺工程师", "生产主管", "车间主任", "设备工程师", "质量工程师(QA/QC)",
        "质检员", "体系/认证工程师", "EHS", "工业工程(IE)", "焊工/车工等技工",
    ],
    "咨询/研究": [
        "管理咨询顾问", "战略规划", "行业研究员", "投资分析师", "情报分析",
        "政策研究", "IT咨询顾问", "科研人员",
    ],
    "内容/媒体": [
        "编辑", "记者", "文案", "作者/写手", "翻译", "视频剪辑", "摄影/摄像",
        "后期制作", "主播", "导演/编导", "配音",
    ],
    "教育/培训": [
        "教师", "讲师", "教研", "课程顾问", "助教", "留学顾问", "教务",
    ],
    "医疗/生物医药": [
        "医生", "护士", "药师", "医学检验", "临床研究(CRA/CRC)", "药物研发",
        "生物信息", "医疗器械注册", "健康管理师", "康复治疗师",
    ],
    "高级管理": [
        "CEO/总经理", "COO", "CTO", "CMO", "VP/副总裁", "合伙人",
        "事业部负责人", "分公司负责人", "董事会秘书",
    ],
    # ---------- 兜底 ----------
    "其他": [],
}

# 扁平列表——UI 下拉框、筛选器、数据库校验都用这个
CATEGORIES = list(JOB_TAXONOMY.keys())

DEFAULT_CATEGORY = "其他"

# ---------------------------------------------------------------------------
# 消歧规则：喂给 LLM 时把这段一起放进 system prompt，能显著降低相邻类目的抖动
# ---------------------------------------------------------------------------
DISAMBIGUATION_RULES = """1. 算法 vs AI/大模型：做 LLM/多模态/AIGC/Agent → AI/大模型；做推荐、搜索、CV、语音、
   风控、自动驾驶等传统方向 → 算法。同时出现时以主要工作内容为准，仍无法判断归「算法」。
2. 算法 vs 数据：产出模型 → 算法；产出数据管道、报表、指标体系 → 数据。
3. 数据分析师 vs 运营：以取数建模为主 → 数据；以业务动作和结果负责为主 → 运营。
4. 运维/SRE vs 后端开发：负责稳定性、部署、基础设施 → 运维/SRE；负责业务逻辑 → 后端开发。
5. 技术支持：面向外部客户 → 客服/客户成功；面向公司内部 IT → 运维/SRE。
6. 风控：写模型的 → 算法；定策略、做审核的 → 法务/合规/风控。
7. 产品运营：岗位职责偏需求与方案 → 产品；偏活动、用户、内容 → 运营。
8. 高级管理仅用于「岗位本身就是公司/事业部一把手」，技术总监、销售总监等按职能归类。
9. 只有确实无法归入以上任何类目时才使用「其他」。"""

# ---------------------------------------------------------------------------
# 旧类目 -> 新类目的迁移映射（用于回填历史数据，见 db._migrate_legacy_categories）
# 值为 None 表示这是「一对多拆分」，无法自动判断该归到哪个新类目，
# 迁移时保持原值不动，交给用户自己改或重新识别一次，避免瞎猜污染数据。
# ---------------------------------------------------------------------------
LEGACY_MAPPING = {
    "开发": None,          # 需重跑：后端开发 / 前端开发 / 移动客户端 / 硬件嵌入式
    "测试": "测试/质量保障",
    "产品": "产品",
    "运营": "运营",
    "数据": "数据",
    "算法": None,          # 需重跑：算法 / AI/大模型
    "AI": "AI/大模型",
    "算法/AI": None,       # 需重跑：算法 / AI/大模型
    "设计": "设计",
    "市场/销售": None,     # 需重跑：市场/品牌/公关 / 销售/BD
    "人力": "人力资源",
    "人力/财务/行政": None,  # 需重跑：人力资源 / 财务 / 行政/后勤
    "财务": "财务",
    "行政": "行政/后勤",
    "其他": "其他",
}


def flatten_keywords() -> dict:
    """返回 关键词 -> 一级类目 的倒排表，可用于分类前的规则预筛。"""
    return {kw: cat for cat, kws in JOB_TAXONOMY.items() for kw in kws}


def build_category_prompt_block() -> str:
    """把分类体系 + 消歧规则拼成可直接嵌进 LLM system prompt 的文本块。

    只给类目名和典型岗位关键词，不给编号，避免模型输出编号而不是类目名。
    """
    lines = []
    for cat, keywords in JOB_TAXONOMY.items():
        if keywords:
            lines.append(f"- {cat}：{ '、'.join(keywords) }")
        else:
            lines.append(f"- {cat}：确实无法归入以上任何类目时才使用")
    taxonomy_text = "\n".join(lines)
    return (
        "可选的岗位分类（必须原样输出下列类目名之一，不要输出编号、不要自造类目）：\n"
        f"{taxonomy_text}\n\n"
        "分类消歧规则（相邻类目容易混淆时按这里判断）：\n"
        f"{DISAMBIGUATION_RULES}"
    )


# 倒排表在模块加载时算一次即可，normalize_category 每条记录都会调用
_KEYWORD_INDEX = flatten_keywords()
# 用于子串兜底匹配：长关键词优先，避免 "Go" 之类的短词误伤；
# 长度 < 3 的关键词（Go/H5/UE 等）只参与精确匹配，不参与子串匹配
_SUBSTRING_KEYWORDS = sorted(
    (kw for kw in _KEYWORD_INDEX if len(kw) >= 3), key=len, reverse=True
)


def normalize_category(raw_category: str) -> str:
    """将 LLM/表单输入的岗位分类归一化为 CATEGORIES 中的合法值。

    依次尝试：精确类目名 -> 旧类目名映射 -> 关键词精确匹配 -> 关键词子串匹配，
    全都不中才回退到「其他」。多做几层兜底是因为大模型偶尔会输出
    "后端工程师"（关键词而非类目名）或 "开发"（旧类目名）这类近似答案，
    直接判成「其他」会白白丢掉本来识别对了的信息。
    """
    if not raw_category:
        return DEFAULT_CATEGORY
    text = raw_category.strip()
    if not text:
        return DEFAULT_CATEGORY

    # 1) 已经是合法类目名
    if text in CATEGORIES:
        return text

    # 2) 旧类目名（有明确对应关系的才映射，一对多拆分的落到「其他」）
    if text in LEGACY_MAPPING:
        mapped = LEGACY_MAPPING[text]
        if mapped in CATEGORIES:
            return mapped

    # 3) 关键词精确匹配（大小写不敏感，覆盖 java / JAVA / Java）
    lowered = text.lower()
    for kw, cat in _KEYWORD_INDEX.items():
        if kw.lower() == lowered:
            return cat

    # 4) 关键词子串匹配，长词优先（"Java后端开发工程师" -> 后端开发）
    for kw in _SUBSTRING_KEYWORDS:
        if kw.lower() in lowered:
            return _KEYWORD_INDEX[kw]

    return DEFAULT_CATEGORY
