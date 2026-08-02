"""
dedup.py
---------
投递记录去重模块：判断「新解析出来的一条投递」和「数据库里已有的记录」是不是
同一次投递（典型场景：截图里写「德勤」，邮件署名却是「德勤中国」）。

两级判定，先便宜后贵：
1. 规则层（本模块的 normalize_* / *_similarity）：把公司名、岗位名归一化后算
   相似度。完全一致的（如 德勤 / 德勤中国）直接判定为同一条，不花 API 调用。
2. 大模型层（llm_judge_same_application）：只有规则层觉得「像但不敢确定」时才
   调用，让模型结合中英文名、母公司/分部、岗位说法差异做最终判断。

模块只负责「给出建议」，不直接改库；是否合并一律由 UI 层询问用户后决定
（见 ui_email_sync.render_email_sync）。
"""

import json
import os
import re
from difflib import SequenceMatcher

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

MODEL_NAME = os.getenv("TEXT_MODEL", "gpt-4o")

# LLM 解析不出公司/岗位时会填这些占位值，去重时要当成「空」，
# 否则两条「未知公司」会被判成同一家公司
PLACEHOLDER_VALUES = {"未知公司", "未知岗位", "未知", "无", "n/a", "na", "none", "unknown"}

# 公司组织形式后缀/通用词：对「是不是同一家公司」没有区分度，归一化时去掉。
# 中文词都是多字词，直接当子串删掉不会误伤；英文词必须按整词删（下面用 \b 边界），
# 否则 "cisco" 会被 "co" 削成 "cis"、"incentive" 会被 "inc" 削成 "entive"。
_COMPANY_ORG_WORDS_CN = [
    "股份有限公司", "有限责任公司", "有限公司", "责任公司", "股份公司",
    "特殊普通合伙", "普通合伙", "合伙企业",
    "集团公司", "集团", "控股", "公司",
]
_COMPANY_ORG_WORDS_EN = [
    "corporation", "incorporated", "technologies", "technology",
    "limited", "holdings", "company", "group", "corp", "inc", "ltd", "llc", "plc", "co",
]
_COMPANY_ORG_EN_RE = re.compile(
    r"\b(?:" + "|".join(_COMPANY_ORG_WORDS_EN) + r")\b", flags=re.IGNORECASE
)

# 只在结尾出现才去掉的地域词：「德勤中国」要归一成「德勤」，
# 但「中国银行」「中国移动」的「中国」在词首，是名字的一部分，绝不能删
_COMPANY_TAIL_REGIONS = ["中国大陆", "中国区", "中国", "大陆", "内地", "亚太", "china", "cn"]

# 岗位名里的招聘季/批次噪声：不影响「是不是同一个岗位」
_POSITION_NOISE_PATTERNS = [
    r"20\d{2}\s*届", r"\d{2}\s*/\s*\d{2}", r"20\d{2}", r"\d{2}届",
    r"校园招聘", r"校招", r"社招", r"秋招", r"春招", r"补录",
    r"毕业生计划", r"管培生计划", r"急聘", r"热招", r"招聘",
]

# ---------------------------------------------------------------------------
# 判定阈值
# ---------------------------------------------------------------------------
# 公司相似度到这个值就算候选（会交给大模型进一步判断）
COMPANY_CANDIDATE_MIN = 0.60
# 公司名字面完全对不上、但岗位名高度一致时也算候选：
# 「PwC」和「普华永道」、「Deloitte」和「德勤」这种中英文写法，字符串相似度是 0，
# 光靠规则永远认不出来，必须放进候选让大模型看一眼（它认得出这是同一家）。
# 只按岗位进来的候选永远不会被规则层直接判成同一条，一定要大模型点头。
POSITION_ONLY_CANDIDATE_MIN = 0.75
# 公司 + 岗位都到这个程度，规则层就能拍板，不必再问大模型
CERTAIN_COMPANY_MIN = 0.95
CERTAIN_POSITION_MIN = 0.90
# 大模型不可用（没配 Key / 网络失败）时的兜底：综合分到这个值才敢提示用户
FALLBACK_COMBINED_MIN = 0.80
# 一次最多把几条候选交给大模型判断
MAX_CANDIDATES = 5


def _is_placeholder(text: str) -> bool:
    return (text or "").strip().lower() in PLACEHOLDER_VALUES


def _to_halfwidth(text: str) -> str:
    """全角字符转半角（（）ＡＢ 之类），避免同一个名字因为标点全半角不同判成两家。"""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            code = 32
        elif 0xFF01 <= code <= 0xFF5E:
            code -= 0xFEE0
        out.append(chr(code))
    return "".join(out)


def normalize_company(name: str) -> str:
    """公司名归一化：去括号内容、去组织形式后缀、去结尾地域词、去标点空格并转小写。

    例：
        "德勤中国"                         -> "德勤"
        "安永（中国）企业咨询有限公司"      -> "安永企业咨询"
        "Deloitte Touche Tohmatsu Ltd."   -> "deloittetouchetohmatsu"
        "中国银行"                         -> "中国银行"（词首的「中国」保留）
    """
    if not name or _is_placeholder(name):
        return ""
    text = _to_halfwidth(str(name)).lower()
    # 括号里通常是分部、法律形式、地区，去掉不影响主体识别
    text = re.sub(r"[\(\[{【（][^\)\]}】）]*[\)\]}】）]", "", text)
    # 英文组织形式词必须趁标点/空格还在的时候按整词删
    text = _COMPANY_ORG_EN_RE.sub(" ", text)
    for word in _COMPANY_ORG_WORDS_CN:
        text = text.replace(word, "")
    # 只保留中日韩文字与字母数字，其余（空格、-、·、&、,、.）全部丢弃
    text = re.sub(r"[^0-9a-z一-鿿]", "", text)
    # 结尾地域词循环剥离（"德勤中国区" -> "德勤"），但不能把名字剥空
    changed = True
    while changed:
        changed = False
        for region in _COMPANY_TAIL_REGIONS:
            if text.endswith(region) and len(text) - len(region) >= 2:
                text = text[: -len(region)]
                changed = True
    return text


def normalize_position(name: str) -> str:
    """岗位名归一化：去招聘季/批次噪声、去标点空格并转小写。"""
    if not name or _is_placeholder(name):
        return ""
    text = _to_halfwidth(str(name)).lower()
    for pattern in _POSITION_NOISE_PATTERNS:
        text = re.sub(pattern, "", text)
    text = re.sub(r"[^0-9a-z一-鿿]", "", text)
    return text


def normalized_key(company: str, position: str) -> tuple[str, str]:
    """(归一化公司, 归一化岗位)，供数据层做完全一致的分组去重。"""
    return normalize_company(company), normalize_position(position)


def _text_similarity(a: str, b: str) -> float:
    """归一化之后两个字符串的相似度，0~1。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # 一方完全包含另一方（"德勤" vs "德勤咨询"）：算高分但不满分，留给大模型/用户定夺。
    # 长度过短的（1 个字）不走这条，"京" 谁都能包含，太容易误判
    if (a in b or b in a) and min(len(a), len(b)) >= 2:
        return 0.88
    return SequenceMatcher(None, a, b).ratio()


def company_similarity(a: str, b: str) -> float:
    return _text_similarity(normalize_company(a), normalize_company(b))


def position_similarity(a: str, b: str) -> float:
    return _text_similarity(normalize_position(a), normalize_position(b))


def find_candidates(company: str, position: str, records) -> list[dict]:
    """在已有记录里找出「可能是同一次投递」的候选，按可能性从高到低排。

    records: 可迭代的 dict（至少含 id / company / position），一般来自
             db.list_applications().to_dict("records")。

    入选条件是「公司名够像」或「岗位名够像」二者之一（后者是为了兜住中英文
    公司名，见 POSITION_ONLY_CANDIDATE_MIN 的说明）。入选只代表"值得再看一眼"，
    最终是不是同一次投递由 suggest_match 里的规则/大模型/用户依次决定。

    返回 [{"record": 原记录, "company_similarity": float,
           "position_similarity": float|None, "combined": float, "certain": bool}, ...]
    position_similarity 为 None 表示新记录没识别出岗位名，无法比对岗位。
    """
    new_position_known = bool(normalize_position(position))
    out = []
    for record in records:
        c_sim = company_similarity(company, record.get("company", ""))
        if new_position_known and normalize_position(record.get("position", "")):
            p_sim = position_similarity(position, record.get("position", ""))
            combined = 0.6 * c_sim + 0.4 * p_sim
            certain = c_sim >= CERTAIN_COMPANY_MIN and p_sim >= CERTAIN_POSITION_MIN
        else:
            # 有一方没岗位名就只能看公司，永远不算「确定」，交给大模型/用户判断
            p_sim = None
            combined = c_sim * 0.8
            certain = False
        company_hit = c_sim >= COMPANY_CANDIDATE_MIN
        position_hit = p_sim is not None and p_sim >= POSITION_ONLY_CANDIDATE_MIN
        if not company_hit and not position_hit:
            continue
        out.append(
            {
                "record": record,
                "company_similarity": round(c_sim, 3),
                "position_similarity": round(p_sim, 3) if p_sim is not None else None,
                "combined": round(combined, 3),
                "certain": certain,
            }
        )
    out.sort(key=lambda item: (item["certain"], item["combined"]), reverse=True)
    return out[:MAX_CANDIDATES]


def find_similar_pairs(records, min_combined: float = 0.75) -> list[dict]:
    """两两比对已有记录，找出「像但归一化后不完全一致」的记录对。

    归一化后完全一致的重复对不在这里返回（那种由 db.find_duplicate_groups 成组
    处理，更直观），这里专门捞「阿里 / 阿里巴巴」「腾讯科技 / 腾讯」这类漏网之鱼，
    交给用户人工判断要不要合并。记录条数是几十~几百量级，O(n²) 完全够用。
    """
    recs = list(records)
    out = []
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            a, b = recs[i], recs[j]
            if normalized_key(a.get("company", ""), a.get("position", "")) == normalized_key(
                b.get("company", ""), b.get("position", "")
            ):
                continue
            c_sim = company_similarity(a.get("company", ""), b.get("company", ""))
            if c_sim < COMPANY_CANDIDATE_MIN:
                continue
            p_sim = position_similarity(a.get("position", ""), b.get("position", ""))
            combined = 0.6 * c_sim + 0.4 * p_sim
            if combined < min_combined:
                continue
            out.append(
                {
                    "a": a,
                    "b": b,
                    "company_similarity": round(c_sim, 3),
                    "position_similarity": round(p_sim, 3),
                    "combined": round(combined, 3),
                }
            )
    out.sort(key=lambda item: item["combined"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# 大模型判定
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = """你是求职投递记录去重助手。用户会给你一条【新解析出来的投递信息】
和若干条【数据库里已有的投递记录】，请判断新信息是否和其中某一条记录指的是同一次投递。

判定标准（两个条件都满足才算同一次投递）：
1. 公司是同一家：简称/全称、中英文名、母公司与其中国分部/地区实体、带不带
   「（中国）」「集团」「有限公司」等后缀，都算同一家。
   例：「德勤」=「德勤中国」=「Deloitte」；「字节」=「字节跳动」。
   但不同法律主体且业务完全不同的关联公司不算，例如「安永华明会计师事务所」
   与「安永（中国）企业咨询有限公司」是两家不同主体，除非岗位也完全对得上。
2. 岗位是同一个岗位：同一岗位的中英文写法、繁简写法、带不带部门/城市后缀，
   都算同一个。不同职能、不同部门、不同职级（如「数据分析师」vs「数据分析实习生」）
   不算同一个岗位。若新信息里没有岗位名（岗位为空或写着「未知岗位」），
   只有在已有记录只剩唯一一条同公司记录、且新信息里的其他线索（备注、状态）
   都对得上时才判为同一次投递，否则一律判为不同。

只输出一个 JSON 对象：
{"match_id": 匹配到的已有记录 id（整数）；没有任何一条匹配则填 null,
 "confidence": 0 到 1 之间的小数，表示你对这个判断的把握,
 "reason": "一句话中文说明理由，不超过 40 字"}

只输出 JSON，不要输出任何解释性文字。"""


def _get_client():
    if OpenAI is None:
        raise RuntimeError("未安装 openai 库，请先 pip install openai")
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("环境变量 LLM_API_KEY 未设置，无法调用投递去重判定")
    base_url = os.getenv("LLM_BASE_URL")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def llm_judge_same_application(new_record: dict, candidates: list[dict], client=None) -> dict:
    """让大模型判断新记录和哪条已有记录是同一次投递。

    new_record: {"company", "position", "notes"(可选), "status"(可选)}
    candidates: find_candidates() 的返回值
    client:     可注入的 OpenAI 客户端（测试时用假客户端，避免真实网络调用）

    返回 {"match_id": int|None, "confidence": float, "reason": str}；
    模型给出的 id 不在候选里会被丢弃（防止编造 id）。
    调用失败时向上抛异常，由调用方决定降级策略。
    """
    if not candidates:
        return {"match_id": None, "confidence": 1.0, "reason": "数据库里没有相似记录"}

    client = client or _get_client()
    payload = {
        "new_record": {
            "company": new_record.get("company", ""),
            "position": new_record.get("position", ""),
            "status": new_record.get("status", ""),
            "notes": (new_record.get("notes") or "")[:300],
        },
        "existing_records": [
            {
                "id": int(item["record"]["id"]),
                "company": item["record"].get("company", ""),
                "position": item["record"].get("position", ""),
                "status": item["record"].get("status", ""),
                "applied_date": item["record"].get("applied_date", ""),
            }
            for item in candidates
        ],
    }

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content)

    valid_ids = {int(item["record"]["id"]) for item in candidates}
    match_id = data.get("match_id")
    try:
        match_id = int(match_id) if match_id is not None else None
    except (TypeError, ValueError):
        match_id = None
    if match_id not in valid_ids:
        match_id = None

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)

    return {
        "match_id": match_id,
        "confidence": confidence,
        "reason": (data.get("reason") or "").strip()[:100],
    }


def suggest_match(
    company: str,
    position: str,
    records,
    notes: str = "",
    status: str = "",
    use_llm: bool = True,
    client=None,
) -> dict | None:
    """给一条新投递信息找出「疑似同一次投递」的已有记录，找不到返回 None。

    返回值（供 UI 直接渲染）：
    {
      "application_id": 已有记录 id,
      "record": 已有记录原始 dict,
      "confidence": 0~1,
      "reason": 判断理由,
      "decided_by": "rule" | "llm" | "rule-fallback",
      "company_similarity": float, "position_similarity": float|None,
    }
    """
    candidates = find_candidates(company, position, records)
    if not candidates:
        return None

    top = candidates[0]
    if top["certain"]:
        return {
            "application_id": int(top["record"]["id"]),
            "record": top["record"],
            "confidence": 0.99,
            "reason": "公司名与岗位名归一化后完全一致（规则判定）",
            "decided_by": "rule",
            "company_similarity": top["company_similarity"],
            "position_similarity": top["position_similarity"],
        }

    if not use_llm:
        return _fallback_suggestion(top)

    try:
        judgement = llm_judge_same_application(
            {"company": company, "position": position, "notes": notes, "status": status},
            candidates,
            client=client,
        )
    except Exception as e:  # 没配 Key、网络不通、模型返回不是 JSON……都降级到规则层
        fallback = _fallback_suggestion(top)
        if fallback:
            fallback["reason"] = f"{fallback['reason']}（大模型判定不可用：{e}）"
        return fallback

    if judgement["match_id"] is None:
        return None

    matched = next(
        item for item in candidates if int(item["record"]["id"]) == judgement["match_id"]
    )
    return {
        "application_id": judgement["match_id"],
        "record": matched["record"],
        "confidence": judgement["confidence"],
        "reason": judgement["reason"] or "大模型判定为同一次投递",
        "decided_by": "llm",
        "company_similarity": matched["company_similarity"],
        "position_similarity": matched["position_similarity"],
    }


def _fallback_suggestion(top: dict) -> dict | None:
    if top["combined"] < FALLBACK_COMBINED_MIN:
        return None
    return {
        "application_id": int(top["record"]["id"]),
        "record": top["record"],
        "confidence": top["combined"],
        "reason": f"公司名相似度 {top['company_similarity']}"
        + (
            f"、岗位名相似度 {top['position_similarity']}"
            if top["position_similarity"] is not None
            else "、新记录没识别出岗位名"
        ),
        "decided_by": "rule-fallback",
        "company_similarity": top["company_similarity"],
        "position_similarity": top["position_similarity"],
    }


__all__ = [
    "normalize_company",
    "normalize_position",
    "normalized_key",
    "company_similarity",
    "position_similarity",
    "find_candidates",
    "find_similar_pairs",
    "llm_judge_same_application",
    "suggest_match",
]
