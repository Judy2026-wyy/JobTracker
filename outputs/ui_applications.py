"""
ui_applications.py
--------------------
全部投递记录板块：
- 表格展示所有 applications（来自截图/邮件/手动录入）
- 手动新增投递记录
- 状态流转（下拉框仅展示合法的下一状态，防止非法跳转）
- 手动添加面试安排（含时区确认勾选）
- 删除记录
- 用大模型按当前分类体系批量重新归类历史记录（预览-确认后再写库）
"""

from datetime import date, datetime, time

import streamlit as st

import db
from category_classifier import classify_positions
from config import STATUS_FLOW, SOURCES, CATEGORIES, get_valid_next_statuses
from timezone_utils import to_utc_iso as _to_utc_iso
from ui_filters import render_filters


def render_manual_add():
    st.markdown("### ➕ 手动新增投递记录")
    with st.form("manual_add_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            company = st.text_input("公司名称 *")
            position = st.text_input("岗位名称 *")
        with col2:
            applied_date = st.date_input("投递日期 *", value=date.today())
            job_link = st.text_input("投递链接（可选）")
        category = st.selectbox("岗位分类", CATEGORIES, index=CATEGORIES.index("其他"))
        notes = st.text_area("备注（可选）")

        submitted = st.form_submit_button("保存")
        if submitted:
            if not company or not position:
                st.warning("公司名称和岗位名称还没填哦，补上再保存吧～")
            else:
                app_id, created = db.upsert_application(
                    company=company,
                    position=position,
                    applied_date=applied_date.isoformat(),
                    status="已投递",
                    job_link=job_link,
                    source="manual",
                    notes=notes,
                    category=category,
                )
                if created:
                    st.success(f"记录保存好啦（ID={app_id}），加油！")
                else:
                    st.info(f"这条记录（公司+岗位+投递日期一样）已经有啦，就不重复添加了，ID={app_id}")


def render_status_transition():
    st.markdown("### 🔁 状态流转")
    apps_df = db.list_applications()
    if apps_df.empty:
        st.caption("还没有投递记录，慢慢来～")
        return

    options = {
        f"#{row.id} {row.company} - {row.position}（当前: {row.status}）": row.id
        for row in apps_df.itertuples()
    }
    choice = st.selectbox("选择要更新的记录", list(options.keys()), key="status_change_select")
    app_id = options[choice]
    current_status = apps_df[apps_df["id"] == app_id].iloc[0]["status"]

    valid_next = get_valid_next_statuses(current_status)
    if not valid_next:
        st.info(f"这条记录已经走到头啦（当前：{current_status}），不用再流转啦")
        return

    new_status = st.selectbox("流转到", valid_next, key="new_status_select")
    if st.button("更新状态", key="btn_update_status"):
        ok, msg = db.update_status(app_id, new_status)
        if ok:
            st.success(msg)
        else:
            st.error(msg)


def render_add_interview():
    st.markdown("### 🗓️ 添加面试安排")
    apps_df = db.list_applications()
    if apps_df.empty:
        st.caption("先去添加一条投递记录吧，再来安排面试～")
        return

    options = {
        f"#{row.id} {row.company} - {row.position}": row.id for row in apps_df.itertuples()
    }
    choice = st.selectbox("所属投递记录", list(options.keys()), key="interview_app_select")
    app_id = options[choice]

    with st.form("add_interview_form", clear_on_submit=True):
        round_name = st.selectbox("面试轮次", STATUS_FLOW[2:], key="interview_round_select")
        col1, col2 = st.columns(2)
        with col1:
            interview_date = st.date_input("日期", value=date.today())
        with col2:
            interview_time = st.time_input("时间", value=time(10, 0))
        tz_known = st.checkbox("我明确知道这个时间对应的时区（否则将标注为待确认）", value=True)
        source_tz = st.text_input(
            "时间所在时区（IANA 名称，如 Asia/Shanghai、America/Chicago）",
            value="Asia/Shanghai",
        )
        location = st.text_input("地点 / 会议链接")
        notes = st.text_area("备注")

        submitted = st.form_submit_button("保存面试安排")
        if submitted:
            naive_dt = datetime.combine(interview_date, interview_time)
            try:
                start_time_utc = _to_utc_iso(naive_dt, source_tz_name=source_tz)
                db.add_interview(
                    application_id=app_id,
                    round_name=round_name,
                    start_time_utc=start_time_utc,
                    timezone_confirmed=tz_known,
                    location_or_link=location,
                    notes=notes,
                )
                st.success("面试安排保存好啦，祝顺利！")
            except Exception as e:
                st.error(f"没保存成功，看看时区名称是不是填对了：{e}")


def render_applications_table():
    st.markdown("### 📋 全部投递记录")

    start_date, end_date, categories = render_filters("apps_table")
    df = db.list_applications(start_date=start_date, end_date=end_date, categories=categories)

    if df.empty:
        st.info("这里还空着呢～去上传截图 / 同步邮箱 / 手动新增一条，或者调整一下筛选条件吧")
        return

    display_cols = [
        "id",
        "company",
        "position",
        "category",
        "status",
        "applied_date",
        "source",
        "job_link",
        "notes",
        "status_updated_at",
    ]
    st.dataframe(
        df[display_cols],
        width="stretch",
        hide_index=True,
    )

    csv_bytes = df[display_cols].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📤 导出当前表格为 CSV",
        data=csv_bytes,
        file_name=f"求职投递记录_{date.today().isoformat()}.csv",
        mime="text/csv",
        key="btn_export_applications_csv",
    )

    with st.expander("🗑️ 删除记录"):
        options = {f"#{row.id} {row.company} - {row.position}": row.id for row in df.itertuples()}
        if options:
            choice = st.selectbox("选择要删除的记录", list(options.keys()), key="delete_app_select")
            if st.button("确认删除", key="btn_delete_app"):
                db.delete_application(options[choice])
                st.success("已删除")
                st.rerun()


def _needs_reclassify(category: str) -> bool:
    """判断这条记录是否"没归好类"：旧分类体系的遗留值，或兜底的「其他」。"""
    return category not in CATEGORIES or category == "其他"


def render_reclassify():
    """用大模型按当前分类体系重新判定历史记录的岗位分类（预览-确认后再写库）。"""
    with st.expander("🔄 用当前分类体系重新归类历史记录", expanded=False):
        st.caption(
            "岗位分类体系升级后，老记录可能还留着旧类目或被归成了「其他」。"
            "这里会把公司名+岗位名+备注交给大模型重新判定，**预览确认后**才写入数据库。"
        )

        all_apps = db.list_applications()
        if all_apps.empty:
            st.info("还没有投递记录，先添加几条再来吧～")
            return

        pending = all_apps[all_apps["category"].apply(_needs_reclassify)]
        scope = st.radio(
            "处理范围",
            [f"只处理没归好类的（{len(pending)} 条）", f"全部记录（{len(all_apps)} 条）"],
            key="reclassify_scope",
            horizontal=True,
        )
        target = all_apps if scope.startswith("全部") else pending

        if target.empty:
            st.success("这些记录都已经归好类啦，不需要重新处理 🎉")
            return

        st.caption(f"将调用大模型处理 {len(target)} 条记录（一次请求批量处理，不会逐条调用）")

        if st.button("🚀 开始重新归类", key="btn_start_reclassify"):
            items = [
                {
                    "id": int(row.id),
                    "company": row.company,
                    "position": row.position,
                    "notes": row.notes or "",
                }
                for row in target.itertuples()
            ]
            with st.spinner(f"正在调用大模型判定 {len(items)} 条记录的分类..."):
                try:
                    result = classify_positions(items)
                    st.session_state["reclassify_result"] = result
                    st.session_state["reclassify_items"] = items
                    missing = len(items) - len(result)
                    if missing:
                        st.warning(f"有 {missing} 条模型没给出结果，这几条会保持原样不动")
                except Exception as e:
                    st.error(f"重新归类失败了：{e}")
                    st.session_state.pop("reclassify_result", None)

        result = st.session_state.get("reclassify_result")
        items = st.session_state.get("reclassify_items")
        if not result or not items:
            return

        old_by_id = {int(row.id): row.category for row in all_apps.itertuples()}
        changes = [
            {
                "id": item["id"],
                "公司": item["company"],
                "岗位": item["position"],
                "原分类": old_by_id.get(item["id"], ""),
                "建议分类": result[item["id"]],
            }
            for item in items
            if item["id"] in result and result[item["id"]] != old_by_id.get(item["id"])
        ]

        if not changes:
            st.success("模型判定的结果和现有分类一致，没有需要修改的记录 👍")
            return

        st.markdown(f"**模型建议修改以下 {len(changes)} 条，取消勾选即可跳过：**")
        keep = []
        for change in changes:
            checked = st.checkbox(
                f"#{change['id']} {change['公司']} · {change['岗位']} ："
                f"{change['原分类']} → **{change['建议分类']}**",
                value=True,
                key=f"reclassify_keep_{change['id']}",
            )
            keep.append(checked)

        if st.button("✅ 应用勾选的修改", key="btn_apply_reclassify"):
            applied = 0
            for change, checked in zip(changes, keep):
                if not checked:
                    continue
                db.update_application_fields(change["id"], category=change["建议分类"])
                applied += 1
            st.success(f"更新好啦，共修改了 {applied} 条记录的分类～")
            st.session_state.pop("reclassify_result", None)
            st.session_state.pop("reclassify_items", None)
            st.rerun()


def render_applications_page():
    render_applications_table()
    render_reclassify()
    st.divider()
    tab1, tab2, tab3 = st.tabs(["手动新增", "状态流转", "添加面试安排"])
    with tab1:
        render_manual_add()
    with tab2:
        render_status_transition()
    with tab3:
        render_add_interview()
