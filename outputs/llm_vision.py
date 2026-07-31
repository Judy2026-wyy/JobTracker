"""
llm_vision.py
--------------
截图识别模块：调用 OpenAI GPT-4o 的视觉能力，从用户上传的投递/面试进度截图中
提取 公司名称 / 岗位名称 / 投递进度 / 备注 等结构化信息。

密钥统一通过 os.getenv("LLM_API_KEY") 读取（load_dotenv 已在 config.py 中执行）。
"""

import base64
import json
import os

from config import ALL_STATUSES, build_category_prompt_block, normalize_category, normalize_status

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

MODEL_NAME = os.getenv("VISION_MODEL", "gpt-4o")

SYSTEM_PROMPT = f"""你是一个求职投递进度识别助手。用户会上传一张或多张招聘平台/邮件/
短信的截图，截图中通常包含公司名称、岗位名称、以及投递状态（例如：已投递、简历筛选中、
笔试、初试/一面、复试/二面、终面/三面、HR面、录用通知/已收offer、已拒绝、已终止 等各种
说法）。

这些截图需要综合在一起分析：
- 如果多张截图其实是在描述同一条投递记录（例如同一封邮件截了好几张图、同一个页面往下
  滚动截了几屏），应该把它们的信息合并成 applications 数组里的同一条记录。
- 如果截图中出现了多条【公司+岗位不同】的独立记录（无论是来自同一张截图还是不同截图），
  应该分别作为 applications 数组中的不同元素输出，不要遗漏，也不要把不同公司或不同岗位
  的记录错误合并。

请只输出一个 JSON 对象，格式如下：
{{
  "applications": [
    {{
      "company": "公司名称（字符串，识别不到则填 \\"\\"）",
      "position": "岗位名称（字符串，识别不到则填 \\"\\"）",
      "category": "岗位所属分类，取值规则见下方【岗位分类】一节",
      "status": "必须从以下枚举中选择一个最匹配的值：{ALL_STATUSES}",
      "applied_date": "若截图中出现投递日期，按 YYYY-MM-DD 输出；否则填 \\"\\"",
      "notes": "岗位核心信息摘要，取值规则见下方【备注(notes)提炼规则】一节"
    }}
  ]
}}

【备注(notes)提炼规则】
notes 用来把截图里这条投递记录的**岗位核心信息完整地罗列出来**，方便以后回看时不用
再翻截图。请按「字段名：值」的形式逐条列出，每条之间用换行分隔，例如：

工作地点：北京
招聘类型：2026届校园招聘
投递渠道：公司官网
学历要求：本科及以上
薪资：20-30K·15薪
截止日期：2026-08-31
志愿顺序：第一志愿

需要提炼的字段包括但不限于：工作地点、所属部门/团队、招聘类型（校招/社招/实习/兼职等）、
学历与专业要求、工作经验要求、薪资待遇、职级、技能要求、工作内容要点、投递渠道/平台、
职位编号、截止日期、志愿顺序、面试形式与地点、以及截图里出现的任何其他与该岗位相关的
关键说明。

**严禁扭曲事实**，具体要求：
1. 只写截图里**真实出现**的内容，一个字都不要编造、不要脑补。
2. 不要用常识或先验知识补全截图里没写的信息（例如看到公司名就自行补上它的总部地址、
   规模、业务范围，一律不许）。
3. 数字、金额、日期、单位、专有名词一律保持截图原样，不要换算、不要四舍五入、
   不要"顺手"改写成更规范的说法。
4. 截图里看不清或不确定的内容，直接不写这一条，不要猜；也不要写"可能是""大概"这类
   模糊表述来蒙混。
5. 不要做任何主观评价、推测或建议（例如"这个岗位竞争应该很激烈"）。
6. 截图里确实没有任何可提炼的岗位信息时，notes 填 ""，不要为了凑内容硬写。

【岗位分类】
分类只看岗位职能本身，不要受公司所属行业影响（例如银行招的后端工程师属于「后端开发」，
不属于「财务」）。请优先根据岗位名称判断，岗位名称含义不明时再结合截图里的其他描述。

{build_category_prompt_block()}

若截图中只有一条记录，applications 数组也只包含这一个元素。只输出 JSON，不要输出任何
解释性文字。"""


def _get_client():
    if OpenAI is None:
        raise RuntimeError("未安装 openai 库，请先 pip install openai")
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("环境变量 LLM_API_KEY 未设置，无法调用截图识别功能")
    base_url = os.getenv("LLM_BASE_URL")  # 兼容自定义 OpenAI 兼容网关（可选）
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _normalize_entry(entry: dict) -> dict:
    return {
        "company": (entry.get("company") or "").strip(),
        "position": (entry.get("position") or "").strip(),
        "category": normalize_category(entry.get("category", "")),
        "status": normalize_status(entry.get("status", "")),
        "applied_date": (entry.get("applied_date") or "").strip(),
        "notes": (entry.get("notes") or "").strip(),
    }


def recognize_screenshots(images: list[tuple[bytes, str]]) -> list[dict]:
    """调用 GPT-4o vision 综合识别一张或多张截图，返回结构化字典列表。

    images: [(image_bytes, mime_type), ...]，所有图片会放进同一次请求里，
    交给模型综合分析（例如同一封邮件截了几张图需要合并，或几张图里混了多条
    不同的投递记录需要拆分）。始终返回列表，即使只识别到一条记录。

    识别失败时抛出异常，由调用方（UI 层）捕获并展示友好错误提示。
    """
    if not images:
        raise ValueError("images 不能为空")

    client = _get_client()

    content = [{"type": "text", "text": "请综合识别下面这些截图中的投递信息，并按要求输出 JSON。"}]
    for image_bytes, mime_type in images:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_image}"
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
    )

    data = json.loads(response.choices[0].message.content)

    entries = data.get("applications")
    if not isinstance(entries, list) or not entries:
        entries = [data]

    return [_normalize_entry(entry) for entry in entries]
