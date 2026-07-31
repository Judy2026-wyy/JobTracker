"""
email_import.py
----------------
163 邮箱同步模块：
1. 通过 IMAP（imaplib）连接 163 邮箱，读取近期邮件。
2. 用 LLM 解析邮件正文，提取投递/面试进度相关结构化信息。
3. 若邮件中包含面试时间，转换为 UTC 存储，并标注是否明确了时区（timezone_confirmed）。

凭据全部通过环境变量读取：
- IMAP_HOST（默认 imap.163.com）
- IMAP_USER
- IMAP_PASS

注意：163 邮箱的 IMAP 客户端需要先发送 ID 命令（客户端标识），否则可能被判定为
"不安全的登录方式" 而拒绝。本模块已内置该兼容处理。
"""

import email
import imaplib
import json
import os
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime

from config import ALL_STATUSES, CATEGORIES, DEFAULT_SOURCE_TIMEZONE, normalize_category, normalize_status
from timezone_utils import to_utc_iso

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

MODEL_NAME = os.getenv("TEXT_MODEL", "gpt-4o")

PARSE_SYSTEM_PROMPT = f"""你是求职邮件信息抽取助手。用户会给你一封邮件的主题和正文，
这封邮件可能与投递进度更新或面试邀约有关，也可能完全无关（例如营销邮件、无关通知）。

请只输出一个 JSON 对象：
- is_job_related: true/false，判断该邮件是否与求职投递/面试相关
- company: 公司名称，识别不到填 ""
- position: 岗位名称，识别不到填 ""
- category: 岗位所属分类，必须从以下枚举中选择一个最匹配的值：{CATEGORIES}；
  无法判断则填 "其他"
- status: 若能判断当前进度，从以下枚举中选择：{ALL_STATUSES}；无法判断填 ""
- has_interview_time: true/false，邮件中是否包含具体的面试时间
- interview_datetime: 若 has_interview_time 为 true，按 "YYYY-MM-DD HH:MM" 输出邮件中提到的原始时间（不要换算）
- interview_timezone: 若邮件中明确提到时区（如"北京时间"、"美东时间"、"CST"等），
  给出对应的 IANA 时区名（如 Asia/Shanghai、America/New_York）；未提及则填 ""
- location_or_link: 面试地点或会议链接，识别不到填 ""
- notes: 简短备注

只输出 JSON，不要输出解释性文字。"""


def _get_client():
    if OpenAI is None:
        raise RuntimeError("未安装 openai 库，请先 pip install openai")
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("环境变量 LLM_API_KEY 未设置，无法调用邮件解析功能")
    base_url = os.getenv("LLM_BASE_URL")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _decode_mime_words(s: str) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    decoded = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            decoded += text.decode(enc or "utf-8", errors="ignore")
        else:
            decoded += text
    return decoded


def _get_email_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if content_type == "text/plain" and "attachment" not in disposition:
                charset = part.get_content_charset() or "utf-8"
                try:
                    return part.get_payload(decode=True).decode(charset, errors="ignore")
                except Exception:
                    continue
        # 没有 text/plain 则退而求其次找 text/html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                try:
                    return part.get_payload(decode=True).decode(charset, errors="ignore")
                except Exception:
                    continue
        return ""
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            return msg.get_payload(decode=True).decode(charset, errors="ignore")
        except Exception:
            return str(msg.get_payload())


def fetch_recent_emails(days: int = 14, mailbox: str = "INBOX", limit: int = 50) -> list[dict]:
    """连接 163 IMAP，拉取最近 N 天的邮件（主题+正文+发件人+日期）。

    返回列表，每项为 {"subject", "from", "date", "body"}。
    连接/认证失败会抛出异常，由调用方展示友好错误信息。
    """
    host = os.getenv("IMAP_HOST", "imap.163.com")
    user = os.getenv("IMAP_USER")
    password = os.getenv("IMAP_PASS")
    if not user or not password:
        raise RuntimeError("环境变量 IMAP_USER / IMAP_PASS 未设置，无法同步163邮箱")

    imap = imaplib.IMAP4_SSL(host)
    try:
        imap.login(user, password)

        # 163/126 要求客户端在登录成功后、发起 SELECT 等其他命令前先发送 ID
        # 命令自报身份，否则服务端会把后续命令当作"不安全的登录方式"直接拒绝。
        # 若 ID 发在 login 之前，或者压根没发送，SELECT 就会失败——但 imaplib
        # 的 select() 失败时只是把连接状态退回 AUTH 并返回非 OK，并不抛异常，
        # 于是下一步 search() 才会报出令人费解的
        # "command SEARCH illegal in state AUTH, only allowed in states SELECTED"。
        imap_id = (
            "name", "job-tracker-app",
            "version", "1.0",
            "vendor", "job-tracker-app",
            "contact", user,
        )
        try:
            imap._simple_command("ID", '("' + '" "'.join(imap_id) + '")')
        except Exception:
            pass  # 部分账号/服务商无需此步骤，忽略即可

        typ, sel_data = imap.select(mailbox)
        if typ != "OK":
            detail = sel_data[0].decode(errors="ignore") if sel_data and sel_data[0] else typ
            raise RuntimeError(
                f"选择邮箱目录 {mailbox!r} 失败（{detail}）。"
                "请确认163邮箱已在设置中开启IMAP/SMTP服务，且 IMAP_PASS 使用的是"
                "客户端授权码而非登录密码。"
            )

        since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        status, data = imap.search(None, f'(SINCE "{since_date}")')
        if status != "OK":
            raise RuntimeError(f"IMAP 搜索邮件失败: {status}")

        ids = data[0].split()
        ids = ids[-limit:] if len(ids) > limit else ids

        emails = []
        for eid in reversed(ids):
            status, msg_data = imap.fetch(eid, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            subject = _decode_mime_words(msg.get("Subject", ""))
            from_ = _decode_mime_words(msg.get("From", ""))
            try:
                date_ = parsedate_to_datetime(msg.get("Date"))
            except Exception:
                date_ = None
            body = _get_email_body(msg)
            emails.append(
                {
                    "subject": subject,
                    "from": from_,
                    "date": date_.isoformat() if date_ else "",
                    "body": body[:4000],  # 截断，避免超长正文
                }
            )
        return emails
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def parse_email_with_llm(mail: dict) -> dict | None:
    """用 LLM 解析单封邮件，返回结构化投递/面试信息；若判定与求职无关则返回 None。"""
    client = _get_client()
    user_content = f"主题: {mail.get('subject','')}\n发件人: {mail.get('from','')}\n正文:\n{mail.get('body','')}"

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": PARSE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(response.choices[0].message.content)

    if not data.get("is_job_related"):
        return None

    result = {
        "company": (data.get("company") or "").strip(),
        "position": (data.get("position") or "").strip(),
        "category": normalize_category(data.get("category", "")),
        "status": normalize_status(data.get("status", "")) if data.get("status") else "",
        "has_interview_time": bool(data.get("has_interview_time")),
        "interview_datetime_raw": (data.get("interview_datetime") or "").strip(),
        "interview_timezone": (data.get("interview_timezone") or "").strip(),
        "location_or_link": (data.get("location_or_link") or "").strip(),
        "notes": (data.get("notes") or "").strip(),
        "source_subject": mail.get("subject", ""),
        "source_date": mail.get("date", ""),
    }

    # 换算面试时间为 UTC；若邮件未明确时区，标记为待确认
    if result["has_interview_time"] and result["interview_datetime_raw"]:
        tz_name = result["interview_timezone"] or DEFAULT_SOURCE_TIMEZONE
        timezone_confirmed = bool(result["interview_timezone"])
        try:
            naive_dt = datetime.strptime(result["interview_datetime_raw"], "%Y-%m-%d %H:%M")
            result["start_time_utc"] = to_utc_iso(naive_dt, source_tz_name=tz_name)
            result["timezone_confirmed"] = timezone_confirmed
        except ValueError:
            result["start_time_utc"] = None
            result["timezone_confirmed"] = False
    else:
        result["start_time_utc"] = None
        result["timezone_confirmed"] = False

    return result


def fetch_and_parse(days: int = 14, mailbox: str = "INBOX", limit: int = 50) -> list[dict]:
    """拉取邮件并逐封解析，返回与求职相关的候选记录列表（供 UI 层预览确认后再入库）。"""
    mails = fetch_recent_emails(days=days, mailbox=mailbox, limit=limit)
    results = []
    for mail in mails:
        try:
            parsed = parse_email_with_llm(mail)
        except Exception as e:
            parsed = None
        if parsed:
            results.append(parsed)
    return results
