"""
ui_home.py
----------
首页板块：
1. 提醒条 —— 未来 48 小时内的面试，按时间升序展示（公司 + 轮次 + America/Chicago
   本地时间 + 地点/链接）；timezone_confirmed=False 的记录单独分组标注"时间待确认"，
   不参与正常排序展示；无待办时显示空状态提示。
2. 简单统计 —— 各状态数量、投递→面试转化率。
3. 投递趋势折线图 —— X 轴为投递日期、Y 轴为当日投递数，支持切换统计周期
   （最近7/30/90天或全部），没有投递的日期补 0 使折线连续。
"""

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import db
from timezone_utils import format_chicago

# 与 config.STATUS_COLOR 的分类色板同一套蓝色（slot 1），单一数据系列直接用它，
# 不需要额外图例
TREND_LINE_COLOR = "#2a78d6"

TREND_PERIOD_OPTIONS = {
    "最近 7 天": 7,
    "最近 30 天": 30,
    "最近 90 天": 90,
    "全部": None,
}


def render_reminder_bar():
    st.subheader("📅 未来 48 小时面试提醒")

    try:
        upcoming = db.get_upcoming_confirmed_interviews(hours=48)
    except Exception as e:
        st.error(f"加载提醒失败：{e}")
        upcoming = None

    if upcoming is not None:
        if upcoming.empty:
            st.info("接下来 48 小时暂时没有安排好的面试，可以喘口气～ ✅")
        else:
            for _, row in upcoming.iterrows():
                local_time = format_chicago(row["start_time_utc"])
                location = row["location_or_link"] or "（未填写地点/链接）"
                st.markdown(
                    f"**{row['company']}** · {row['round']} · 🕒 {local_time}（美国达拉斯时间）· 📍 {location}"
                )

    try:
        pending = db.get_pending_confirmation_interviews()
    except Exception as e:
        pending = None

    if pending is not None and not pending.empty:
        with st.expander(f"⏳ 时间待确认（{len(pending)} 条），点击展开", expanded=False):
            for _, row in pending.iterrows():
                st.markdown(
                    f"- **{row['company']}** · {row['round']} · 原始记录: {row['start_time_utc']} "
                    f"· 📍 {row['location_or_link'] or '未填写'}（时区未确认，暂不参与排序）"
                )


def render_stats():
    st.subheader("📊 简单统计")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**各状态数量**")
        try:
            counts = db.get_status_counts()
            if counts.empty:
                st.caption("还没有数据，投出第一份简历后这里就会热闹起来～")
            else:
                st.dataframe(counts, width="stretch", hide_index=True)
        except Exception as e:
            st.error(f"加载统计失败：{e}")

    with col2:
        st.markdown("**投递 → 面试 转化率**")
        try:
            stats = db.get_conversion_stats()
            rate_pct = f"{stats['conversion_rate'] * 100:.1f}%"
            st.metric(
                label="转化率（已进入面试环节 / 总投递数）",
                value=rate_pct,
                delta=f"{stats['applications_with_interview']} / {stats['total_applications']}",
            )
            if stats["total_applications"] > 0:
                st.caption("每一份投递都是靠近offer的一步，慢慢来 💪")
        except Exception as e:
            st.error(f"加载统计失败：{e}")


def render_trend_chart():
    st.subheader("📈 投递趋势")

    period_label = st.radio(
        "统计周期",
        list(TREND_PERIOD_OPTIONS.keys()),
        index=0,
        horizontal=True,
        key="home_trend_period",
    )
    period_days = TREND_PERIOD_OPTIONS[period_label]

    try:
        today = date.today()
        start_date = (today - timedelta(days=period_days - 1)).isoformat() if period_days else None
        daily_counts = db.get_daily_application_counts(start_date=start_date)
    except Exception as e:
        st.error(f"加载投递趋势失败：{e}")
        return

    if daily_counts.empty:
        st.caption("还没有投递数据，投出第一份简历后这里就会画出曲线啦～")
        return

    # 没有投递的日期补 0，让折线连续、不会看起来像数据缺失
    daily_counts["applied_date"] = pd.to_datetime(daily_counts["applied_date"])
    range_start = pd.to_datetime(start_date) if start_date else daily_counts["applied_date"].min()
    range_end = pd.to_datetime(today)
    full_range = pd.date_range(range_start, range_end, freq="D")
    chart_df = (
        daily_counts.set_index("applied_date")
        .reindex(full_range, fill_value=0)
        .rename_axis("applied_date")
        .reset_index()
    )
    chart_df["count"] = chart_df["count"].astype(int)

    base = alt.Chart(chart_df).encode(
        x=alt.X("applied_date:T", title=None),
        y=alt.Y("count:Q", title="投递数", axis=alt.Axis(tickMinStep=1)),
    )
    hover = alt.selection_point(fields=["applied_date"], nearest=True, on="mouseover", empty=False)

    line = base.mark_line(strokeWidth=2, color=TREND_LINE_COLOR)
    points = base.mark_point(size=64, filled=True, color=TREND_LINE_COLOR).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0))
    )
    rule = (
        base.mark_rule(color="#c3c2b7")
        .encode(
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[
                alt.Tooltip("applied_date:T", title="日期"),
                alt.Tooltip("count:Q", title="投递数"),
            ],
        )
        .add_params(hover)
    )

    chart = (line + points + rule).properties(height=260)
    st.altair_chart(chart, width="stretch")


def render_home():
    render_reminder_bar()
    st.divider()
    render_stats()
    st.divider()
    render_trend_chart()
