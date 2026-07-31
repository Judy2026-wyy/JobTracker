"""
ui_email_sync.py
-----------------
163 邮箱同步板块：连接 IMAP -> 拉取近期邮件 -> LLM 解析 -> 按邮件类型分组预览
候选记录 -> 用户勾选确认后批量写入数据库（source="email"）。

设计上采用"预览-确认"两步，避免误判邮件导致脏数据直接落库。

候选记录按邮件类型（笔试 / 面试 / offer通知 / 其他）分 tab 展示。对不想入库的
邮件提供两种处理方式，都只影响本次预览，不会去动 163 邮箱里的原始邮件：
- 跳过：本次不导入，但仍留在原来的类型分组里。
- 忽略：本次不导入，并把它归到「其他」分组（适合反复被误判成求职相关的邮件）。
两种操作都能撤销（点「↩️ 恢复」），下次重新同步时所有标记都会重置。
"""

from datetime import date

import streamlit as st

import db
from config import EMAIL_TYPE_OTHER, EMAIL_TYPES
from email_import import fetch_and_parse
from timezone_utils import format_chicago

ACTION_SKIP = "skip"
ACTION_IGNORE = "ignore"


def _effective_type(candidate: dict, action: str | None) -> str:
    """候选记录在界面上实际归属的分组：被「忽略」的一律划到「其他」。"""
    if action == ACTION_IGNORE:
        return EMAIL_TYPE_OTHER
    return candidate.get("email_type") or EMAIL_TYPE_OTHER


def _build_label(candidate: dict) -> str:
    parts = [
        candidate.get("company") or "（未知公司）",
        candidate.get("position") or "（未知岗位）",
    ]
    if candidate.get("category"):
        parts.append(f"分类: {candidate['category']}")
    if candidate.get("status"):
        parts.append(f"状态: {candidate['status']}")
    if candidate.get("has_interview_time") and candidate.get("start_time_utc"):
        local_time = format_chicago(candidate["start_time_utc"])
        confirmed_tag = "" if candidate.get("timezone_confirmed") else "（时区待确认）"
        parts.append(f"面试时间: {local_time} {confirmed_tag}")
    return " · ".join(parts)


def _render_candidate(idx: int, candidate: dict, actions: dict) -> None:
    action = actions.get(idx)
    label = _build_label(candidate)

    with st.container(border=True):
        if action == ACTION_SKIP:
            st.markdown(f"~~{label}~~")
            st.caption("⏭️ 本次跳过，不会导入")
        elif action == ACTION_IGNORE:
            st.markdown(f"~~{label}~~")
            st.caption("🚫 已忽略，归到「其他」，本次不会导入")
        else:
            st.checkbox(label, value=True, key=f"email_cand_{idx}")

        with st.expander("查看邮件来源", expanded=False):
            st.caption(f"邮件主题: {candidate.get('source_subject', '')}")
            st.caption(f"邮件日期: {candidate.get('source_date', '')}")
            if candidate.get("notes"):
                st.caption(f"备注: {candidate['notes']}")

        if action is None:
            col_skip, col_ignore = st.columns(2)
            with col_skip:
                if st.button("⏭️ 跳过", key=f"email_skip_{idx}", width="stretch"):
                    actions[idx] = ACTION_SKIP
                    st.rerun()
            with col_ignore:
                if st.button("🚫 忽略（归到其他）", key=f"email_ignore_{idx}", width="stretch"):
                    actions[idx] = ACTION_IGNORE
                    st.rerun()
        else:
            if st.button("↩️ 恢复", key=f"email_restore_{idx}", width="stretch"):
                actions.pop(idx, None)
                st.rerun()


def render_email_sync():
    st.subheader("📧 163 邮箱同步")
    st.caption("自动读取近期邮件，识别投递进度更新与面试邀约，确认后导入数据库")

    st.session_state.setdefault("email_cand_actions", {})

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
                st.session_state["email_cand_actions"] = {}  # 新一次同步，清空上次的标记
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

    actions = st.session_state["email_cand_actions"]

    grouped = {email_type: [] for email_type in EMAIL_TYPES}
    for idx, candidate in enumerate(candidates):
        grouped[_effective_type(candidate, actions.get(idx))].append((idx, candidate))

    st.markdown("**按邮件类型分组，勾选需要导入的记录：**")
    st.caption("不想入库的邮件可以「跳过」或「忽略」，两者都不会动163邮箱里的原始邮件")

    tabs = st.tabs([f"{email_type}（{len(grouped[email_type])}）" for email_type in EMAIL_TYPES])
    for tab, email_type in zip(tabs, EMAIL_TYPES):
        with tab:
            if not grouped[email_type]:
                st.caption("这个分类下暂时没有邮件～")
                continue
            for idx, candidate in grouped[email_type]:
                _render_candidate(idx, candidate, actions)

    if st.button("✅ 导入勾选的记录", key="btn_import_email_candidates"):
        imported, duplicated, passed = 0, 0, 0
        for idx, c in enumerate(candidates):
            if actions.get(idx) in (ACTION_SKIP, ACTION_IGNORE):
                passed += 1
                continue
            if not st.session_state.get(f"email_cand_{idx}", True):
                passed += 1
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
                duplicated += 1

            if app_id and c.get("has_interview_time") and c.get("start_time_utc"):
                db.add_interview(
                    application_id=app_id,
                    round_name=status if status else "面试",
                    start_time_utc=c["start_time_utc"],
                    timezone_confirmed=bool(c.get("timezone_confirmed")),
                    location_or_link=c.get("location_or_link", ""),
                    notes=c.get("notes", ""),
                )

        summary = f"导入完成啦：新增 {imported} 条，重复的 {duplicated} 条就没有再收一遍～"
        if passed:
            summary += f"另外有 {passed} 条按你的选择没有导入。"
        st.success(summary)
        st.session_state["email_candidates"] = []
        st.session_state["email_cand_actions"] = {}
