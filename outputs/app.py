"""
app.py
------
求职追踪应用主入口。整合各功能板块：
- 首页（面试提醒条 + 简单统计）
- 上传截图识别
- 163 邮箱同步
- 面试月历
- 全部投递记录（增/改/查/删 + 状态流转 + 添加面试）

运行方式： streamlit run app.py
"""

import streamlit as st

from db import init_db
from ui_home import render_home
from ui_screenshot import render_screenshot_upload
from ui_email_sync import render_email_sync
from ui_calendar import render_calendar
from ui_applications import render_applications_page
from ui_pipeline import render_pipeline_page

st.set_page_config(
    page_title="求职追踪看板",
    page_icon="📋",
    layout="wide",
)

# 卡片圆角/间距微调（配色本身由 .streamlit/config.toml 的暗色主题统一控制）
st.markdown(
    """
    <style>
    div[data-testid="stMetric"] {
        padding: 0.25rem 0;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 16px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# 应用启动时确保数据库表结构存在
try:
    init_db()
except Exception as e:
    st.error(f"数据库初始化失败：{e}")
    st.stop()

st.title("📋 求职追踪看板")

PAGES = {
    "🏠 首页": render_home,
    "📊 投递看板": render_pipeline_page,
    "🖼️ 上传截图": render_screenshot_upload,
    "📧 邮箱同步": render_email_sync,
    "🗓️ 面试月历": render_calendar,
    "📋 全部记录": render_applications_page,
}

with st.sidebar:
    st.header("导航")
    choice = st.radio("选择板块", list(PAGES.keys()), label_visibility="collapsed")
    st.divider()
    st.caption("环境变量提示：")
    st.caption("LLM_API_KEY / IMAP_HOST / IMAP_USER / IMAP_PASS")
    st.caption("目标时区：America/Chicago（美国达拉斯）")

PAGES[choice]()
