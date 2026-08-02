"""
ui_home.py
----------
首页板块：
1. 提醒条 —— 未来 48 小时内的面试，按时间升序展示（公司 + 轮次 + 本地时间 +
   地点/链接）。本地时间按侧边栏「时区设置」选定的时区换算，时间后面会标出
   当前是哪个时区，避免看错；timezone_confirmed=False 的记录单独分组标注
   "时间待确认"，不参与正常排序展示；无待办时显示空状态提示。
2. 简单统计 —— 左边各状态数量，右边「近期笔试 Deadline」：把邮箱同步时解析出来的
   笔试/测评最晚提交时间按公司、岗位列出来，越紧急的排越前面，不足 24 小时的会
   单独提醒。（原来这里是「投递→面试转化率」，转化情况已改由「投递看板」的漏斗
   分析图逐层展示，比单一比率更能看出卡在哪一环，就不在首页重复占位了。）
3. 投递趋势折线图 —— X 轴为投递日期、Y 轴为当日投递数，支持切换统计周期
   （最近7/30/90天或全部），没有投递的日期补 0 使折线连续。
"""

from datetime import date, datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

import db
from timezone_utils import format_local, get_display_timezone_label, parse_iso

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
            tz_label = get_display_timezone_label()
            for _, row in upcoming.iterrows():
                local_time = format_local(row["start_time_utc"])
                location = row["location_or_link"] or "（未填写地点/链接）"
                st.markdown(
                    f"**{row['company']}** · {row['round']} · 🕒 {local_time}（{tz_label}）· 📍 {location}"
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


def _remaining_text(deadline_utc: str) -> tuple[str, bool]:
    """距离截止还剩多久的人话描述，以及是否紧急（不足 24 小时）。

    用"还剩 N 天 / N 小时"而不是直接摆一个日期，是因为截止时间这种东西，
    看到"还剩 6 小时"才会立刻去做，看到"08-07 11:53"很容易以为还早。
    """
    try:
        left = parse_iso(deadline_utc) - datetime.now(timezone.utc)
    except Exception:
        return "", False
    total_hours = left.total_seconds() / 3600
    if total_hours < 0:
        return "已截止", False
    if total_hours < 1:
        return f"仅剩 {max(int(left.total_seconds() // 60), 1)} 分钟", True
    if total_hours < 24:
        return f"仅剩 {int(total_hours)} 小时", True
    return f"还剩 {int(total_hours // 24)} 天", False


def render_assessment_deadlines():
    """近期笔试 Deadline：公司 / 岗位 / 测评最晚提交时间。

    数据来自邮箱同步时解析出的测评截止时间（applications.assessment_deadline_utc），
    只展示还没到期的，按最紧急的排在最前。
    """
    st.markdown("**⏳ 近期笔试 Deadline**")
    try:
        upcoming = db.get_upcoming_assessment_deadlines()
    except Exception as e:
        st.error(f"加载笔试截止时间失败：{e}")
        return

    if upcoming.empty:
        st.caption("目前没有待完成的笔试/测评～同步邮箱后，识别到的测评截止时间会出现在这里")
        return

    tz_label = get_display_timezone_label()
    rows = []
    urgent_count = 0
    unconfirmed_count = 0
    for row in upcoming.itertuples():
        remaining, urgent = _remaining_text(row.assessment_deadline_utc)
        urgent_count += int(urgent)
        deadline_text = format_local(row.assessment_deadline_utc)
        if not row.assessment_deadline_confirmed:
            # 用一个记号而不是"（时区待确认）"五个字：这张表挤在首页右半边，
            # 多几个字就会把「剩余」那一列挤出可视区域，含义放到下面的说明里讲
            deadline_text += " ⚠️"
            unconfirmed_count += 1
        rows.append(
            {
                "公司": row.company,
                "岗位": row.position,
                "测评最晚提交时间": deadline_text,
                "剩余": ("🔴 " if urgent else "") + remaining,
            }
        )

    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        column_config={
            "公司": st.column_config.TextColumn(width="small"),
            "岗位": st.column_config.TextColumn(width="medium"),
            "测评最晚提交时间": st.column_config.TextColumn(width="medium"),
            "剩余": st.column_config.TextColumn(width="small"),
        },
    )
    st.caption(f"时间按「{tz_label}」展示，越紧急的排越前面")
    if unconfirmed_count:
        st.caption("⚠️ = 邮件里没写清时区，已按北京时间推算，建议核对一下原邮件")
    if urgent_count:
        st.warning(f"有 {urgent_count} 个测评不到 24 小时就截止了，先把这几个做掉吧！", icon="⏰")


def render_stats():
    st.subheader("📊 简单统计")

    # 右边的 Deadline 表有四列（公司/岗位/时间/剩余），对半分的话「剩余」那列
    # 会被挤出可视区域，所以给它两倍宽；左边只是个状态计数表，窄一点够用
    col1, col2 = st.columns([1, 2])

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
        render_assessment_deadlines()


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
