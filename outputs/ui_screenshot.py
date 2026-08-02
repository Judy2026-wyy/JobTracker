"""
ui_screenshot.py
-----------------
截图上传板块：用户在同一个方框里「上传文件」或「粘贴剪贴板」加入一张或多张
投递/面试进度截图 -> 调用 llm_vision 把所有图片一起交给大模型综合识别 ->
展示可编辑的识别结果 -> 用户确认后写入数据库（source="screenshot"）。

交互设计说明：
- 上传和粘贴不再分 tab。两种方式并排放在同一个方框里，任意方式加进来的图片
  都进同一个「待识别图片墙」，用户不需要先选择用哪种方式。
- 图片一进来就被复制进 st.session_state（见 _ingest_uploads / _ingest_paste），
  之后立刻把上传/粘贴控件的 key 换一个版本号。这样控件本身被重置成空，
  既不会在下一次 rerun 时把同一张图重复塞进来，也让「删掉的图片又自己冒出来」
  这个老问题彻底消失——图片墙的唯一数据源就是 session_state，控件只负责收图。
- 缩略图统一压成正方形（见 image_utils.make_square_thumbnail），排出来是整齐的
  方阵；点击图片即可切换选中（选中态是绿色边框），不再需要勾选框。
"""

from datetime import date

import streamlit as st
from streamlit_paste_button import paste_image_button

import db
from config import ALL_STATUSES, CATEGORIES
from image_utils import image_signature, make_square_thumbnail, to_png_bytes
from llm_vision import recognize_screenshots

# 图片墙每行几张
GRID_COLUMNS = 4

# 选中态配色：与主题的成功色一致
SELECT_COLOR = "#2f9f3d"
SELECT_GLOW = "rgba(47, 159, 61, 0.35)"


def _init_state() -> None:
    st.session_state.setdefault("shot_items", [])  # [{id,bytes,mime,name,source,thumb}]
    st.session_state.setdefault("shot_selected", set())
    st.session_state.setdefault("shot_uploader_version", 0)
    st.session_state.setdefault("shot_paste_version", 0)


def _clear_all_images() -> None:
    """清空图片墙，并把上传/粘贴控件都重置掉（否则控件里残留的图会被再次收进来）。"""
    st.session_state["shot_items"] = []
    st.session_state["shot_selected"] = set()
    st.session_state["shot_uploader_version"] += 1
    st.session_state["shot_paste_version"] += 1
    st.session_state.pop("screenshot_result", None)


def _add_image(image_bytes: bytes, mime: str, name: str, source: str) -> bool:
    """把一张图加进图片墙。返回 False 表示这张图已经在墙上了（按内容去重）。"""
    img_id = image_signature(image_bytes)
    if any(item["id"] == img_id for item in st.session_state["shot_items"]):
        return False
    st.session_state["shot_items"].append(
        {
            "id": img_id,
            "bytes": image_bytes,
            "mime": mime,
            "name": name,
            "source": source,
            "thumb": make_square_thumbnail(image_bytes),
        }
    )
    return True


def _ingest_uploads(uploaded_files) -> None:
    """把 file_uploader 里的文件收进图片墙，然后重置控件并 rerun。"""
    if not uploaded_files:
        return
    added, duplicated, failed = 0, 0, []
    for f in uploaded_files:
        try:
            if _add_image(f.getvalue(), f.type or "image/png", f.name, "upload"):
                added += 1
            else:
                duplicated += 1
        except Exception:
            failed.append(f.name)

    st.session_state["shot_uploader_version"] += 1
    if added:
        st.toast(f"已添加 {added} 张图片")
    if duplicated:
        st.toast(f"有 {duplicated} 张图片下面已经有了，没重复添加")
    if failed:
        st.session_state["shot_ingest_error"] = f"这些文件打不开，跳过了：{'、'.join(failed)}"
    st.rerun()


def _ingest_paste(paste_result) -> None:
    """把剪贴板粘贴进来的图收进图片墙，然后重置粘贴控件并 rerun。"""
    try:
        image_bytes = to_png_bytes(paste_result.image_data)
        added = _add_image(image_bytes, "image/png", "剪贴板截图", "paste")
    except Exception as e:
        st.session_state["shot_ingest_error"] = f"这张粘贴的图没处理成功：{e}"
        added = False
    st.session_state["shot_paste_version"] += 1
    st.toast("已添加 1 张粘贴的图片" if added else "这张图下面已经有了，没重复添加")
    st.rerun()


def _render_input_box() -> None:
    """上传 + 粘贴合并在一个方框里，用户不用先选择方式。"""
    with st.container(border=True):
        st.markdown("#### 📥 添加截图：**上传文件** 或 **粘贴剪贴板**，两种方式都可以")
        st.caption("两种方式加进来的图片会一起排在下面的图片墙里，可以混着用，也可以只用其中一种")

        col_upload, col_paste = st.columns([3, 2], gap="medium")
        with col_upload:
            st.markdown("**方式一：从电脑里选图片**")
            uploaded_files = st.file_uploader(
                "选择截图文件（可多选）",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
                key=f"shot_uploader_{st.session_state['shot_uploader_version']}",
                label_visibility="collapsed",
            )
            st.caption("支持 png / jpg / jpeg / webp，可一次选多张")

        with col_paste:
            st.markdown("**方式二：粘贴剪贴板里的截图**")
            paste_result = paste_image_button(
                label="📋 点这里粘贴截图",
                background_color=SELECT_COLOR,
                hover_background_color="#27862f",
                key=f"shot_paste_{st.session_state['shot_paste_version']}",
            )
            st.caption("微信/系统截图复制后点上面的按钮，可以连续粘贴多张")

    # 收图放在方框渲染之后：两个控件都拿到值了再统一处理，避免只收到一半
    if uploaded_files:
        _ingest_uploads(uploaded_files)
    if paste_result is not None and paste_result.image_data is not None:
        _ingest_paste(paste_result)


def _inject_grid_css(selected_ids: set) -> None:
    """图片墙样式：整张卡片就是一个透明按钮，选中的卡片描绿边。

    做成「按钮铺满卡片 + opacity:0」而不是用勾选框，是为了满足"点图片就能选中"；
    万一将来 Streamlit 换了 DOM 结构让这段 CSS 失效，按钮也只是恢复成一个普通
    的可见按钮（标签写着「选择 / 取消选择」），功能不会坏掉。

    两个实测踩出来的坑，改样式前先看这里（都用真浏览器验证过）：
    1. 覆盖按钮必须 position:absolute + inset:0 直接贴着卡片铺满，不能靠
       height:100% 一层层往下传——按钮和卡片之间还隔着 .stButton 包装层，
       那一层撑不满时按钮只占顶部 40px 一条，点图片中间会落空（点了没反应）。
    2. 选中态的规则要额外带一份 :hover 版本。基础样式里的 :hover 白边优先级
       比单个属性选择器高，鼠标停在卡片上时会把绿边盖掉（表现就是"点了图片
       却没变绿"）——而点完图片鼠标本来就停在那张卡片上。
    """
    selected_rules = "\n".join(
        f'div[class*="st-key-shotcard_{img_id}"],\n'
        f'div[class*="st-key-shotcard_{img_id}"]:hover {{'
        f" border-color: {SELECT_COLOR};"
        f" box-shadow: 0 0 0 3px {SELECT_GLOW};"
        f" background: rgba(47, 159, 61, 0.10); }}"
        for img_id in selected_ids
    )
    st.markdown(
        f"""
        <style>
        div[class*="st-key-shotcard_"] {{
            position: relative;
            border: 2px solid rgba(255, 255, 255, 0.14);
            border-radius: 14px;
            padding: 8px;
            overflow: hidden;
            transition: border-color .15s ease, box-shadow .15s ease, background .15s ease;
        }}
        div[class*="st-key-shotcard_"]:hover {{
            border-color: rgba(255, 255, 255, 0.45);
        }}
        div[class*="st-key-shotcard_"] img {{
            border-radius: 10px;
        }}
        /* 图片右上角的「全屏查看」工具条会抢走点击，卡片里不需要 */
        div[class*="st-key-shotcard_"] div[data-testid="stElementToolbar"] {{
            display: none;
        }}
        /* 选中按钮铺满整张卡片：点图片 = 点按钮 */
        div[class*="st-key-shotcard_"] div[class*="st-key-shotpick_"] {{
            position: absolute;
            inset: 0;
            margin: 0;
            z-index: 3;
        }}
        div[class*="st-key-shotcard_"] div[class*="st-key-shotpick_"] button {{
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            opacity: 0;
            border: none;
            background: transparent;
            cursor: pointer;
        }}
        {selected_rules}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_grid(items: list[dict]) -> None:
    selected = st.session_state["shot_selected"]
    _inject_grid_css(selected)

    # 按行切块，每行固定 GRID_COLUMNS 列：最后一行不满时留空列，
    # 这样每张缩略图的宽度在所有行里都完全一致，排出来是整齐的方阵
    for row_start in range(0, len(items), GRID_COLUMNS):
        row_items = items[row_start : row_start + GRID_COLUMNS]
        cols = st.columns(GRID_COLUMNS, gap="small")
        for offset, item in enumerate(row_items):
            idx = row_start + offset
            with cols[offset], st.container(key=f"shotcard_{item['id']}"):
                st.image(item["thumb"], width="stretch")
                is_selected = item["id"] in selected
                icon = "✅" if is_selected else "🖼️"
                source_tag = "粘贴" if item["source"] == "paste" else "上传"
                name = item["name"] if len(item["name"]) <= 16 else item["name"][:15] + "…"
                st.caption(f"{icon} {idx + 1}. {name}（{source_tag}）")
                if st.button(
                    "取消选择" if is_selected else "选择",
                    key=f"shotpick_{item['id']}",
                    width="stretch",
                ):
                    if is_selected:
                        selected.discard(item["id"])
                    else:
                        selected.add(item["id"])
                    st.rerun()


def _render_toolbar(items: list[dict]) -> None:
    selected = st.session_state["shot_selected"]
    col_del, col_clear, col_all = st.columns([2, 2, 1])

    with col_del:
        if st.button(
            f"🗑️ 删除选中的图片（{len(selected)}）",
            key="btn_delete_selected_images",
            disabled=not selected,
            width="stretch",
        ):
            st.session_state["shot_items"] = [
                item for item in items if item["id"] not in selected
            ]
            st.session_state["shot_selected"] = set()
            st.session_state.pop("screenshot_result", None)
            st.rerun()

    with col_clear:
        if st.button(
            f"🧹 批量删除（清空全部 {len(items)} 张）",
            key="btn_clear_all_images",
            width="stretch",
        ):
            _clear_all_images()
            st.rerun()

    with col_all:
        all_selected = len(selected) == len(items)
        if st.button(
            "全不选" if all_selected else "全选",
            key="btn_toggle_select_all_images",
            width="stretch",
        ):
            st.session_state["shot_selected"] = (
                set() if all_selected else {item["id"] for item in items}
            )
            st.rerun()


def _render_result_form(results: list[dict]) -> None:
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
                    existing = db.get_application(app_id) or {}
                    st.info(
                        f"「{entry['company']} - {entry['position']}」和已有的 "
                        f"#{app_id}「{existing.get('company', '')} - {existing.get('position', '')}」"
                        "是同一条投递，就不重复添加了"
                    )
            if any_processed:
                _clear_all_images()
                st.rerun()


def render_screenshot_upload():
    st.subheader("🖼️ 上传投递/面试截图")
    st.caption(
        "支持招聘平台、邮件、短信等截图，可一次上传/粘贴多张图片，"
        "大模型会综合分析所有图片再提取公司、岗位与投递进度"
    )

    _init_state()
    _render_input_box()

    error = st.session_state.pop("shot_ingest_error", None)
    if error:
        st.warning(error)

    items = st.session_state["shot_items"]
    # 图片被删掉之后，选中集合里可能还留着已经不存在的 id，这里对齐一次
    valid_ids = {item["id"] for item in items}
    st.session_state["shot_selected"] &= valid_ids

    if not items:
        st.info("还没有待识别的图片，用上面任意一种方式加几张吧～")
        return

    st.markdown(f"**🗂️ 待识别图片（{len(items)} 张）**")
    st.caption("点击图片即可选中/取消，选中的图片会显示绿色边框")
    _render_grid(items)
    _render_toolbar(items)

    st.divider()
    if st.button("🔍 开始识别", key="btn_recognize_screenshot", type="primary"):
        with st.spinner(f"正在调用大模型综合识别 {len(items)} 张截图..."):
            try:
                results = recognize_screenshots([(item["bytes"], item["mime"]) for item in items])
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
        _render_result_form(results)
