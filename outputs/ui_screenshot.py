"""
ui_screenshot.py
-----------------
截图上传板块：用户上传或粘贴一张或多张投递/面试进度截图 -> 调用 llm_vision
把所有图片一起交给大模型综合识别 -> 展示可编辑的识别结果 -> 用户确认后
写入数据库（source="screenshot"）。
"""

import hashlib
import io
from datetime import date

import streamlit as st
from streamlit_paste_button import paste_image_button

import db
from config import ALL_STATUSES, CATEGORIES
from llm_vision import recognize_screenshots


def _image_to_png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_screenshot_upload():
    st.subheader("🖼️ 上传投递/面试截图")
    st.caption(
        "支持招聘平台、邮件、短信等截图，可一次上传/粘贴多张图片，"
        "大模型会综合分析所有图片再提取公司、岗位与投递进度"
    )

    st.session_state.setdefault("screenshot_uploader_version", 0)
    st.session_state.setdefault("pasted_images", [])
    st.session_state.setdefault("excluded_upload_ids", set())

    tab_upload, tab_paste = st.tabs(["📁 选择文件", "📋 粘贴截图"])

    with tab_upload:
        uploaded_files = st.file_uploader(
            "选择截图（可多选）",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=f"screenshot_uploader_{st.session_state['screenshot_uploader_version']}",
        )

    with tab_paste:
        st.caption("截图（如微信截图、系统截图）复制到剪贴板后，点击下方按钮再按 Ctrl+V 粘贴，可重复粘贴多张")
        paste_result = paste_image_button(label="📋 点击后按 Ctrl+V 粘贴一张截图")
        if paste_result.image_data is not None:
            pasted_bytes = _image_to_png_bytes(paste_result.image_data)
            sig = hashlib.md5(pasted_bytes).hexdigest()
            if sig != st.session_state.get("_last_paste_sig"):
                st.session_state["pasted_images"].append(pasted_bytes)
                st.session_state["_last_paste_sig"] = sig

    # 汇总所有待识别图片：文件上传（排除已被批量删除的） + 剪贴板粘贴
    # kind 用来在批量删除时区分"文件上传"（按 file_id 记入排除名单）和
    # "剪贴板粘贴"（直接从 pasted_images 列表里摘除）两种来源
    all_images = []  # [(bytes, mime_type, 展示用标签, kind, ref_id)]
    for f in uploaded_files or []:
        if f.file_id in st.session_state["excluded_upload_ids"]:
            continue
        all_images.append((f.getvalue(), f.type or "image/png", f.name, "upload", f.file_id))
    for i, pb in enumerate(st.session_state["pasted_images"]):
        paste_id = hashlib.md5(pb).hexdigest()
        all_images.append((pb, "image/png", f"粘贴图片 {i + 1}", "paste", paste_id))

    has_any_staged = bool(uploaded_files) or st.session_state["pasted_images"]

    if not all_images:
        if has_any_staged:
            st.info("已选中的图片都被清除啦，请重新上传或粘贴")
        else:
            st.info("请先上传或粘贴至少一张截图")
        return

    st.caption(f"共 {len(all_images)} 张图片待识别，勾选后可批量删除：")
    cols = st.columns(min(len(all_images), 4))
    selected_to_delete = []
    for idx, (img_bytes, _mime, label, kind, ref_id) in enumerate(all_images):
        with cols[idx % len(cols)]:
            st.image(img_bytes, caption=label, width=150)
            checked = st.checkbox("选中删除", key=f"screenshot_del_check_{kind}_{idx}_{ref_id}")
            if checked:
                selected_to_delete.append((kind, ref_id))

    col_del, col_clear = st.columns(2)
    with col_del:
        if st.button(
            f"🗑️ 删除选中的图片（{len(selected_to_delete)}）",
            key="btn_delete_selected_images",
            disabled=not selected_to_delete,
        ):
            delete_upload_ids = {ref_id for kind, ref_id in selected_to_delete if kind == "upload"}
            delete_paste_ids = {ref_id for kind, ref_id in selected_to_delete if kind == "paste"}
            st.session_state["excluded_upload_ids"] |= delete_upload_ids
            st.session_state["pasted_images"] = [
                pb
                for pb in st.session_state["pasted_images"]
                if hashlib.md5(pb).hexdigest() not in delete_paste_ids
            ]
            st.session_state.pop("screenshot_result", None)
            st.rerun()
    with col_clear:
        if st.button("🗑️ 清除全部图片", key="btn_clear_all_images"):
            st.session_state["pasted_images"] = []
            st.session_state["excluded_upload_ids"] = set()
            st.session_state.pop("_last_paste_sig", None)
            st.session_state.pop("screenshot_result", None)
            st.session_state["screenshot_uploader_version"] += 1
            st.rerun()

    if st.button("🔍 开始识别", key="btn_recognize_screenshot"):
        with st.spinner(f"正在调用大模型综合识别 {len(all_images)} 张截图..."):
            try:
                results = recognize_screenshots([(b, m) for b, m, _, _, _ in all_images])
                st.session_state["screenshot_result"] = results
                if len(results) > 1:
                    st.success(f"识别完成啦，一共认出了 {len(results)} 条投递记录，逐条核对后保存吧")
                else:
                    st.success("识别完成啦，检查一下下面的信息，确认无误就保存吧")
            except Exception as e:
                st.error(f"这次没认出来，要不换几张清晰点的图试试？（{e}）")
                st.session_state.pop("screenshot_result", None)

    results = st.session_state.get("screenshot_result")
    if results:
        with st.form("confirm_screenshot_form"):
            entries = []
            for i, result in enumerate(results):
                if len(results) > 1:
                    st.markdown(f"**记录 {i + 1}**")
                include = (
                    st.checkbox("保存这条记录", value=True, key=f"screenshot_include_{i}")
                    if len(results) > 1
                    else True
                )
                company = st.text_input("公司名称", value=result.get("company", ""), key=f"screenshot_company_{i}")
                position = st.text_input("岗位名称", value=result.get("position", ""), key=f"screenshot_position_{i}")
                category = st.selectbox(
                    "岗位分类（自动识别，可修改）",
                    CATEGORIES,
                    index=CATEGORIES.index(result.get("category", "其他"))
                    if result.get("category") in CATEGORIES
                    else CATEGORIES.index("其他"),
                    key=f"screenshot_category_{i}",
                )
                status = st.selectbox(
                    "投递进度",
                    ALL_STATUSES,
                    index=ALL_STATUSES.index(result.get("status", "已投递"))
                    if result.get("status") in ALL_STATUSES
                    else 0,
                    key=f"screenshot_status_{i}",
                )
                applied_date_str = result.get("applied_date") or ""
                try:
                    default_date = date.fromisoformat(applied_date_str) if applied_date_str else date.today()
                except ValueError:
                    default_date = date.today()
                applied_date = st.date_input("投递日期", value=default_date, key=f"screenshot_date_{i}")
                notes = st.text_area(
                    "备注（截图里提炼出的岗位核心信息，可自行增删改）",
                    value=result.get("notes", ""),
                    height=200,
                    key=f"screenshot_notes_{i}",
                )

                entries.append(
                    {
                        "include": include,
                        "company": company,
                        "position": position,
                        "category": category,
                        "status": status,
                        "applied_date": applied_date,
                        "notes": notes,
                    }
                )
                if len(results) > 1:
                    st.divider()

            submitted = st.form_submit_button("✅ 确认并保存到数据库")
            if submitted:
                any_processed = False
                for entry in entries:
                    if not entry["include"]:
                        continue
                    if not entry["company"] or not entry["position"]:
                        st.warning("有一条记录的公司名称或岗位名称还空着，这条先跳过了～")
                        continue
                    app_id, created = db.upsert_application(
                        company=entry["company"],
                        position=entry["position"],
                        applied_date=entry["applied_date"].isoformat(),
                        status=entry["status"],
                        source="screenshot",
                        notes=entry["notes"],
                        category=entry["category"],
                    )
                    any_processed = True
                    if created:
                        st.success(f"「{entry['company']} - {entry['position']}」保存好啦（ID={app_id}），加油！")
                    else:
                        st.info(f"「{entry['company']} - {entry['position']}」已经有啦，就不重复添加了，ID={app_id}")
                if any_processed:
                    st.session_state.pop("screenshot_result", None)
                    st.session_state["pasted_images"] = []
                    st.session_state["excluded_upload_ids"] = set()
                    st.session_state.pop("_last_paste_sig", None)
                    st.session_state["screenshot_uploader_version"] += 1
                    st.rerun()
