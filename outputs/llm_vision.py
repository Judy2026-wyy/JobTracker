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

from config import ALL_STATUSES, CATEGORIES, normalize_category, normalize_status

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

MODEL_NAME = os.getenv("VISION_MODEL", "gpt-4o")

SYSTEM_PROMPT = f"""你是一个求职投递进度识别助手。用户会上传招聘平台/邮件/短信的截图，
截图中通常包含公司名称、岗位名称、以及投递状态（例如：已投递、简历筛选中、笔试、
初试/一面、复试/二面、终面/三面、HR面、录用通知/已收offer、已拒绝、已终止 等各种说法）。

请只输出一个 JSON 对象，字段如下：
- company: 公司名称（字符串，识别不到则填 ""）
- position: 岗位名称（字符串，识别不到则填 ""）
- category: 岗位所属分类，必须从以下枚举中选择一个最匹配的值：{CATEGORIES}；
  无法判断则填 "其他"
- status: 必须从以下枚举中选择一个最匹配的值：{ALL_STATUSES}
- applied_date: 若截图中出现投递日期，按 YYYY-MM-DD 输出；否则填 ""
- notes: 其他有用的补充信息（简短中文），识别不到则填 ""

只输出 JSON，不要输出任何解释性文字。"""


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


def recognize_screenshot(image_bytes: bytes, mime_type: str = "image/png") -> dict:
    """调用 GPT-4o vision 识别截图，返回结构化字典。

    识别失败时抛出异常，由调用方（UI 层）捕获并展示友好错误提示。
    """
    client = _get_client()
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type};base64,{b64_image}"

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别这张截图中的投递信息，并按要求输出 JSON。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    content = response.choices[0].message.content
    data = json.loads(content)

    return {
        "company": (data.get("company") or "").strip(),
        "position": (data.get("position") or "").strip(),
        "category": normalize_category(data.get("category", "")),
        "status": normalize_status(data.get("status", "")),
        "applied_date": (data.get("applied_date") or "").strip(),
        "notes": (data.get("notes") or "").strip(),
    }
