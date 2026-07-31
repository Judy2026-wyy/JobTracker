"""
ui_calendar.py
----------------
月历板块：使用 streamlit-calendar（基于 FullCalendar）展示月视图，
每场面试渲染为一张任务卡片（公司 + 轮次），并支持切换月份查看历史面试。

时间展示：日历 calendarOptions 设置 timeZone="America/Chicago"，
事件本身以 UTC ISO 字符串传入，由 FullCalendar 自动换算为达拉斯本地时间展示，
与其余板块的时区处理口径保持一致。
"""

from datetime import date, timedelta

import streamlit as st

import db
from config import TARGET_TIMEZONE, STATUS_COLOR
from timezone_utils import format_chicago

try:
    from streamlit_calendar import calendar as st_calendar
except ImportError:  # pragma: no cover
    st_calendar = None


def _build_events(df):
    events = []
    for _, row in df.iterrows():
        color = STATUS_COLOR.get(row["status"], "#64748b")
        title = f"{row['company']} · {row['round']}"
        if not row["timezone_confirmed"]:
            title += "（待确认）"
        events.append(
            {
                "title": title,
                "start": row["start_time_utc"],
                "end": row["start_time_utc"],
                "color": color,
                "extendedProps": {
                    "company": row["company"],
                    "position": row["position"],
                    "round": row["round"],
                    "location_or_link": row["location_or_link"] or "",
                    "notes": row["notes"] or "",
                    "timezone_confirmed": bool(row["timezone_confirmed"]),
                },
            }
        )
    return events


def render_calendar():
    st.subheader("🗓️ 面试月历")

    if st_calendar is None:
        st.error("未安装 streamlit-calendar，请先 pip install streamlit-calendar")
        return

    with st.expander("🔎 历史查询（按日期范围筛选）", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "起始日期", value=date.today() - timedelta(days=90), key="calendar_start"
            )
        with col2:
            end_date = st.date_input(
                "结束日期", value=date.today() + timedelta(days=90), key="calendar_end"
            )
        company_filter = st.text_input("按公司名称筛选（可选）", key="calendar_company_filter")

    try:
        df = db.list_interviews(start_date=start_date.isoformat(), end_date=end_date.isoformat())
    except Exception as e:
        st.error(f"加载面试记录失败：{e}")
        return

    if company_filter:
        df = df[df["company"].str.contains(company_filter, case=False, na=False)]

    if df.empty:
        st.info("所选范围内暂无面试安排")
        return

    events = _build_events(df)

    calendar_options = {
        "initialView": "dayGridMonth",
        "timeZone": TARGET_TIMEZONE,
        "headerToolbar": {
            "left": "prev,next today",
            "center": "title",
            "right": "dayGridMonth,listMonth",
        },
        "height": 650,
    }

    clicked = st_calendar(events=events, options=calendar_options, key="job_interview_calendar")

    if clicked and clicked.get("eventClick"):
        props = clicked["eventClick"]["event"].get("extendedProps", {})
        start = clicked["eventClick"]["event"].get("start")
        st.markdown("#### 📌 面试详情")
        st.write(f"**公司**：{props.get('company', '')}")
        st.write(f"**岗位**：{props.get('position', '')}")
        st.write(f"**轮次**：{props.get('round', '')}")
        if start:
            st.write(f"**时间（美国达拉斯 America/Chicago）**：{format_chicago(start)}")
        st.write(f"**地点/链接**：{props.get('location_or_link', '（未填写）')}")
        if not props.get("timezone_confirmed"):
            st.warning("该面试时间的时区尚未确认，请核实后在「全部投递记录」中更新")
        if props.get("notes"):
            st.write(f"**备注**：{props.get('notes')}")
