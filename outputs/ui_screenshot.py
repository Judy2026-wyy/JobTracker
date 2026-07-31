"""
ui_screenshot.py
-----------------
截图上传板块：用户上传投递/面试进度截图 -> 调用 llm_vision 识别 ->
展示可编辑的识别结果 -> 用户确认后写入数据库（source="screenshot"）。
"""

from datetime import date

import streamlit as st

import db
from config import ALL_STATUSES, CATEGORIES
from llm_vision import recognize_screenshot


def render_screenshot_upload():
    st.subheader("🖼️ 上传投递/面试截图")
    st.caption("支持招聘平台、邮件、短信等截图，自动识别公司、岗位与投递进度")

    uploaded_file = st.file_uploader("选择截图", type=["png", "jpg", "jpeg", "webp"])

    if uploaded_file is None:
        return

    image_bytes = uploaded_file.getvalue()
    st.image(image_bytes, caption="预览", width=400)

    if st.button("🔍 开始识别", key="btn_recognize_screenshot"):
        with st.spinner("正在调用大模型识别截图内容..."):
            try:
                result = recognize_screenshot(image_bytes, mime_type=uploaded_file.type or "image/png")
                st.session_state["screenshot_result"] = result
                st.success("识别完成啦，检查一下下面的信息，确认无误就保存吧")
            except Exception as e:
                st.error(f"这次没认出来，要不换张清晰点的图试试？（{e}）")
                st.session_state.pop("screenshot_result", None)

    result = st.session_state.get("screenshot_result")
    if result:
        with st.form("confirm_screenshot_form"):
            company = st.text_input("公司名称", value=result.get("company", ""))
            position = st.text_input("岗位名称", value=result.get("position", ""))
            category = st.selectbox(
                "岗位分类（自动识别，可修改）",
                CATEGORIES,
                index=CATEGORIES.index(result.get("category", "其他"))
                if result.get("category") in CATEGORIES
                else CATEGORIES.index("其他"),
            )
            status = st.selectbox(
                "投递进度",
                ALL_STATUSES,
                index=ALL_STATUSES.index(result.get("status", "已投递"))
                if result.get("status") in ALL_STATUSES
                else 0,
            )
            applied_date_str = result.get("applied_date") or ""
            try:
                default_date = date.fromisoformat(applied_date_str) if applied_date_str else date.today()
            except ValueError:
                default_date = date.today()
            applied_date = st.date_input("投递日期", value=default_date)
            notes = st.text_area("备注", value=result.get("notes", ""))

            submitted = st.form_submit_button("✅ 确认并保存到数据库")
            if submitted:
                if not company or not position:
                    st.warning("公司名称和岗位名称还空着，补一下再保存哦～")
                else:
                    app_id, created = db.upsert_application(
                        company=company,
                        position=position,
                        applied_date=applied_date.isoformat(),
                        status=status,
                        source="screenshot",
                        notes=notes,
                        category=category,
                    )
                    if created:
                        st.success(f"投递记录保存好啦（ID={app_id}），加油！")
                    else:
                        st.info(f"这条记录（公司+岗位+投递日期一样）已经有啦，就不重复添加了，ID={app_id}")
                    st.session_state.pop("screenshot_result", None)
