"""
ui_timezone.py
---------------
侧边栏「时区设置」板块：选定所有面试时间的展示时区。

为什么要有这个：数据库里存的一律是 UTC，展示时才换算。以前展示时区被写死成
America/Chicago，人在国内或换了城市时，首页提醒条、面试月历、邮箱同步预览里
的时间就全是错的——约面试最怕的就是时间看错。

交互按需求设计：
- 选「中国」：中国全境行政上统一用北京时间，所以不再追问城市，直接用
  Asia/Shanghai。
- 选其他国家：再问一次城市/地区（跨时区的国家按时区档位给城市，比如美国分
  东部/中部/山地/太平洋），据此自动换算出 IANA 时区。
- 列表里没有的地方：选「其他」直接填 IANA 时区名，填错会当场提示、不会存进库。

选完立刻生效并存进 app_settings 表（重启应用后仍然是这个时区）。这个板块在
app.py 里渲染于「关闭应用」按钮上方，且早于页面主体，所以改完当次就能看到
新时区下的时间，不需要再刷新一次。
"""

import streamlit as st

from config import (
    CHINA_CITY_LABEL,
    CHINA_COUNTRY,
    CHINA_TIMEZONE,
    COUNTRY_TIMEZONES,
    CUSTOM_COUNTRY,
    build_timezone_label,
    find_timezone_location,
)
from timezone_utils import (
    get_display_timezone,
    get_display_timezone_label,
    is_valid_timezone,
    now_in_display_tz,
    set_display_timezone,
    utc_offset_text,
)


def _apply(tz_name: str, label: str) -> None:
    """时区真的变了才写库，避免每次 rerun 都白写一遍。"""
    if tz_name != get_display_timezone() or label != get_display_timezone_label():
        set_display_timezone(tz_name, label)


def render_timezone_settings() -> None:
    st.markdown("**🕒 时区设置**")
    st.caption("面试时间按这个时区展示")

    current_tz = get_display_timezone()
    location = find_timezone_location(current_tz)
    countries = list(COUNTRY_TIMEZONES.keys())
    default_country = location[0] if location else CUSTOM_COUNTRY

    country = st.selectbox(
        "国家 / 地区",
        countries,
        index=countries.index(default_country),
        key="tz_country_select",
    )

    if country == CHINA_COUNTRY:
        st.caption("🇨🇳 中国全境统一使用北京时间，不用再选城市")
        _apply(CHINA_TIMEZONE, build_timezone_label(CHINA_COUNTRY, CHINA_CITY_LABEL))

    elif country == CUSTOM_COUNTRY:
        custom = st.text_input(
            "IANA 时区名",
            value=current_tz,
            placeholder="例如 Europe/Madrid",
            key="tz_custom_input",
            help="填写 IANA 时区名（形如 大洲/城市），可在维基百科搜「IANA 时区数据库」查到",
        )
        custom = (custom or "").strip()
        if is_valid_timezone(custom):
            _apply(custom, custom)
        elif custom:
            st.error(f"没有「{custom}」这个时区，先按原来的 {current_tz} 展示")

    else:
        cities = list(COUNTRY_TIMEZONES[country].keys())
        default_city = location[1] if location and location[0] == country else cities[0]
        city = st.selectbox(
            "城市 / 地区",
            cities,
            index=cities.index(default_city),
            # key 带上国家：换国家时选项整组换掉，共用一个 key 会把上一个国家的
            # 城市值带进新选项里对不上
            key=f"tz_city_select_{country}",
        )
        _apply(COUNTRY_TIMEZONES[country][city], build_timezone_label(country, city))

    now_local = now_in_display_tz()
    st.caption(
        f"当前：**{get_display_timezone_label()}**\n\n"
        f"{get_display_timezone()}（{utc_offset_text()}）· "
        f"现在 {now_local.strftime('%m-%d %H:%M')}"
    )
