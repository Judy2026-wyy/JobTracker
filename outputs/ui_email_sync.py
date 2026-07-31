"""
ui_email_sync.py
-----------------
163 邮箱同步板块：连接 IMAP -> 拉取近期邮件 -> LLM 解析 -> 预览候选记录 ->
用户勾选确认后批量写入数据库（source="email"）。

设计上采用"预览-确认"两步，避免误判邮件导致脏数据直接落库。
"""

from datetime import date

import streamlit as st

import db
from email_import import fetch_and_parse
from timezone_utils import format_chicago


def render_email_sync():
    st.subheader("📧 163 邮箱同步")
    st.caption("自动读取近期邮件，识别投递进度更新与面试邀约，确认后导入数据库")

    col1, col2 = st.columns(2)
    with col1:
        days = st.slider("同步最近几天的邮件", min_value=1, max_value=60, value=14)
    with col2:
        limit = st.slider("最多扫描邮件数", min_value=10, max_value=200, value=50)

    if st.button("🔄 开始同步", key="btn_sync_email"):
        with st.spinner("正在连接163邮箱并解析邮件，请稍候..."):
            try:
                candidates = fetch_and_parse(days=days, limit=limit)
                st.session_state["email_candidates"] = candidates
                if candidates:
                    st.success(f"找到 {len(candidates)} 条看起来和求职有关的邮件，来看看吧")
                else:
                    st.info("这次没找到相关邮件，说不定过阵子再来看看～")
            except Exception as e:
                st.error(f"同步没成功，看看是不是这个问题：{e}")
                st.session_state["email_candidates"] = []

    candidates = st.session_state.get("email_candidates", [])
    if not candidates:
        return

    st.markdown("**请勾选需要导入的记录：**")
    selected_flags = []
    for idx, c in enumerate(candidates):
        label_parts = [c.get("company") or "（未知公司）", c.get("position") or "（未知岗位）"]
        if c.get("category"):
            label_parts.append(f"分类: {c['category']}")
        if c.get("status"):
            label_parts.append(f"状态: {c['status']}")
        if c.get("has_interview_time") and c.get("start_time_utc"):
            local_time = format_chicago(c["start_time_utc"])
            confirmed_tag = "" if c.get("timezone_confirmed") else "（时区待确认）"
            label_parts.append(f"面试时间: {local_time} {confirmed_tag}")
        label = " · ".join(label_parts)

        checked = st.checkbox(label, value=True, key=f"email_cand_{idx}")
        with st.expander("查看邮件来源", expanded=False):
            st.caption(f"邮件主题: {c.get('source_subject', '')}")
            st.caption(f"邮件日期: {c.get('source_date', '')}")
            if c.get("notes"):
                st.caption(f"备注: {c['notes']}")
        selected_flags.append(checked)

    if st.button("✅ 导入勾选的记录", key="btn_import_email_candidates"):
        imported, skipped = 0, 0
        for c, checked in zip(candidates, selected_flags):
            if not checked:
                continue
            company = c.get("company") or "未知公司"
            position = c.get("position") or "未知岗位"
            applied_date = date.today().isoformat()
            status = c.get("status") or "已投递"

            app_id, created = db.upsert_application(
                company=company,
                position=position,
                applied_date=applied_date,
                status=status,
                source="email",
                notes=c.get("notes", ""),
                category=c.get("category", "其他"),
            )
            if created:
                imported += 1
            else:
                skipped += 1

            if app_id and c.get("has_interview_time") and c.get("start_time_utc"):
                db.add_interview(
                    application_id=app_id,
                    round_name=status if status else "面试",
                    start_time_utc=c["start_time_utc"],
                    timezone_confirmed=bool(c.get("timezone_confirmed")),
                    location_or_link=c.get("location_or_link", ""),
                    notes=c.get("notes", ""),
                )

        st.success(f"导入完成啦：新增 {imported} 条，重复的 {skipped} 条就没有再收一遍～")
        st.session_state["email_candidates"] = []
