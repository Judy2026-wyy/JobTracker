"""
ui_home.py
----------
首页板块：
1. 提醒条 —— 未来 48 小时内的面试，按时间升序展示（公司 + 轮次 + America/Chicago
   本地时间 + 地点/链接）；timezone_confirmed=False 的记录单独分组标注"时间待确认"，
   不参与正常排序展示；无待办时显示空状态提示。
2. 简单统计 —— 各状态数量、投递→面试转化率。
"""

import streamlit as st

import db
from timezone_utils import format_chicago


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


def render_home():
    render_reminder_bar()
    st.divider()
    render_stats()
