"""
timezone_utils.py
------------------
时区转换工具：统一使用 zoneinfo。

数据库里一律存 UTC 时间（ISO8601 字符串），展示层再转成**用户选定的展示时区**
（侧边栏「时区设置」，见 ui_timezone.py；存在 app_settings 表里，重启后仍生效）。
没设置过时用 config.DEFAULT_DISPLAY_TIMEZONE 兜底。

不硬编码 UTC±N 偏移量：zoneinfo 会按具体日期自动处理夏令时切换（比如
America/Chicago 夏天是 CDT、冬天是 CST，Europe/London 夏天是 BST）。
用户人在国内、面试约在美东，两边的夏令时规则和切换日期都不一样，
自己算偏移量迟早算错，这也是这里只认 IANA 时区名的原因。
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import db
from config import (
    DEFAULT_DISPLAY_TIMEZONE,
    DEFAULT_SOURCE_TIMEZONE,
    SETTING_DISPLAY_TIMEZONE,
    SETTING_DISPLAY_TIMEZONE_LABEL,
    build_timezone_label,
    find_timezone_location,
)


def is_valid_timezone(tz_name: str) -> bool:
    """是不是一个 zoneinfo 认识的 IANA 时区名。"""
    if not tz_name:
        return False
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def get_display_timezone() -> str:
    """当前生效的展示时区（IANA 名）。存的值无效时回退到默认时区。"""
    tz_name = db.get_setting(SETTING_DISPLAY_TIMEZONE)
    return tz_name if is_valid_timezone(tz_name) else DEFAULT_DISPLAY_TIMEZONE


def get_display_timezone_label() -> str:
    """展示时区给用户看的说明，如「中国 · 北京时间（全国统一）」。"""
    tz_name = get_display_timezone()
    label = db.get_setting(SETTING_DISPLAY_TIMEZONE_LABEL)
    if label and db.get_setting(SETTING_DISPLAY_TIMEZONE) == tz_name:
        return label
    location = find_timezone_location(tz_name)
    return build_timezone_label(*location) if location else tz_name


def set_display_timezone(tz_name: str, label: str = "") -> None:
    """保存用户选的展示时区。时区名非法时直接抛错，不写脏值进库。"""
    if not is_valid_timezone(tz_name):
        raise ValueError(f"不认识这个时区名：{tz_name}")
    db.set_setting(SETTING_DISPLAY_TIMEZONE, tz_name)
    location = find_timezone_location(tz_name)
    db.set_setting(
        SETTING_DISPLAY_TIMEZONE_LABEL,
        label or (build_timezone_label(*location) if location else tz_name),
    )


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


def utc_iso_to_local(utc_iso_str: str) -> datetime:
    """把存储的 UTC ISO 字符串转换为当前展示时区的本地时间（自动处理夏令时）。"""
    dt_utc = parse_iso(utc_iso_str)
    return dt_utc.astimezone(ZoneInfo(get_display_timezone()))


def format_local(utc_iso_str: str, fmt: str = "%Y-%m-%d %H:%M %Z") -> str:
    """把 UTC ISO 字符串直接格式化为展示时区的可读字符串。"""
    try:
        return utc_iso_to_local(utc_iso_str).strftime(fmt)
    except Exception:
        return f"{utc_iso_str} (解析失败)"


def now_in_display_tz() -> datetime:
    """展示时区下的"现在"，用于在设置面板里让用户确认自己选对了没。"""
    return datetime.now(timezone.utc).astimezone(ZoneInfo(get_display_timezone()))


def utc_offset_text(tz_name: str = None) -> str:
    """当前时刻该时区相对 UTC 的偏移，如 "UTC+8"、"UTC-5"（含夏令时影响）。"""
    tz_name = tz_name or get_display_timezone()
    offset = datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name)).utcoffset()
    if offset is None:
        return "UTC"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
