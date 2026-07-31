"""
category_classifier.py
-----------------------
用大模型给「已经在数据库里的投递记录」重新判定岗位分类。

用途：岗位分类体系升级后（见 config.JOB_TAXONOMY），历史记录还留着旧类目，
或者当初识别时归错/归成了「其他」，这里提供一个批量重新归类的能力。

与 llm_vision / email_import 的区别：那两个模块是在「新数据入库前」顺带判定分类，
这里是对「已入库数据」做纯文本的重新判定，只看公司名 + 岗位名 + 备注，不碰图片。

一次请求批量处理多条记录（默认 40 条一批），比逐条调用省 token 也快得多。
"""

import json
import os

from config import CATEGORIES, DEFAULT_CATEGORY, build_category_prompt_block, normalize_category

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

MODEL_NAME = os.getenv("TEXT_MODEL", "gpt-4o")

# 一次请求塞多少条记录。太大容易触发输出长度上限、模型也更容易漏条目
DEFAULT_BATCH_SIZE = 40

SYSTEM_PROMPT = f"""你是求职岗位分类助手。用户会给你一批投递记录，每条包含 id、
公司名称、岗位名称和备注。请为每一条判定它属于哪个岗位分类。

分类只看岗位职能本身，不要受公司所属行业影响（例如银行招的后端工程师属于
「后端开发」，不属于「财务」；咨询公司招的数据分析师属于「数据」，不属于「咨询/研究」）。
请优先根据岗位名称判断，岗位名称含义不明时再结合公司名称和备注。

{build_category_prompt_block()}

请只输出一个 JSON 对象，格式如下：
{{
  "results": [
    {{"id": 1, "category": "后端开发"}},
    {{"id": 2, "category": "数据"}}
  ]
}}

results 必须为输入里的**每一条** id 都给出结果，不能遗漏、不能新增不存在的 id。
只输出 JSON，不要输出任何解释性文字。"""


def _get_client():
    if OpenAI is None:
        raise RuntimeError("未安装 openai 库，请先 pip install openai")
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("环境变量 LLM_API_KEY 未设置，无法调用重新归类功能")
    base_url = os.getenv("LLM_BASE_URL")  # 兼容自定义 OpenAI 兼容网关（可选）
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _classify_batch(client, items: list[dict]) -> dict[int, str]:
    """处理一批记录，返回 {id: 归一化后的分类}。"""
    payload = [
        {
            "id": item["id"],
            "company": item.get("company") or "",
            "position": item.get("position") or "",
            "notes": (item.get("notes") or "")[:200],  # 备注可能很长，截断够判断即可
        }
        for item in items
    ]

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)

    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"模型返回的格式不对，缺少 results 数组：{data}")

    valid_ids = {item["id"] for item in items}
    out = {}
    for row in results:
        if not isinstance(row, dict):
            continue
        try:
            row_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if row_id not in valid_ids:
            continue  # 模型编造了不存在的 id，丢弃
        # 走一遍 normalize_category：模型可能输出关键词而不是类目名，
        # 兜底逻辑能把 "后端工程师" 之类的答案纠回 "后端开发"
        out[row_id] = normalize_category(row.get("category", ""))
    return out


def classify_positions(items: list[dict], batch_size: int = DEFAULT_BATCH_SIZE) -> dict[int, str]:
    """批量重新判定岗位分类，返回 {application_id: 分类}。

    items: [{"id": int, "company": str, "position": str, "notes": str}, ...]

    模型漏掉的记录不会出现在返回值里（调用方据此提示"这几条没判出来"），
    而不是硬塞一个「其他」进去覆盖掉原本可能是对的分类。
    """
    if not items:
        return {}

    client = _get_client()
    out = {}
    for start in range(0, len(items), batch_size):
        out.update(_classify_batch(client, items[start:start + batch_size]))
    return out


__all__ = ["classify_positions", "CATEGORIES", "DEFAULT_CATEGORY"]
