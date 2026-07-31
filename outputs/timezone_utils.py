"""
timezone_utils.py
------------------
时区转换工具：统一使用 zoneinfo，目标时区 America/Chicago（达拉斯所在时区）。

不硬编码 UTC-5/UTC-6 偏移量，zoneinfo 会根据具体日期自动处理夏令时(CDT/CST)切换。
数据库中一律存储 UTC 时间（ISO8601 字符串），展示层再转换为目标时区。
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import TARGET_TIMEZONE, DEFAULT_SOURCE_TIMEZONE

CHICAGO_TZ = ZoneInfo(TARGET_TIMEZONE)


def parse_iso(dt_str: str) -> datetime:
    """解析 ISO8601 字符串为带时区的 datetime；若无时区信息，默认视为 UTC。"""
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_utc_iso(dt: datetime, source_tz_name: str = None) -> str:
    """将一个（可能不带时区的）datetime 按 source_tz_name 解释后转换为 UTC ISO 字符串。"""
    if dt.tzinfo is None:
        tz_name = source_tz_name or DEFAULT_SOURCE_TIMEZONE
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(timezone.utc).isoformat()


def utc_iso_to_chicago(utc_iso_str: str) -> datetime:
    """把存储的 UTC ISO 字符串转换为 America/Chicago 本地时间（自动处理夏令时）。"""
    dt_utc = parse_iso(utc_iso_str)
    return dt_utc.astimezone(CHICAGO_TZ)


def format_chicago(utc_iso_str: str, fmt: str = "%Y-%m-%d %H:%M %Z") -> str:
    """把 UTC ISO 字符串直接格式化为达拉斯本地时间的可读字符串。"""
    try:
        dt_chicago = utc_iso_to_chicago(utc_iso_str)
        return dt_chicago.strftime(fmt)
    except Exception:
        return f"{utc_iso_str} (解析失败)"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
