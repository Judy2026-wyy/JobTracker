"""
ui_filters.py
----------------
投递记录的日期范围 + 岗位分类筛选控件，被「全部记录」与「投递看板」两个页面
共用，避免同一套筛选逻辑维护两份。
"""

from datetime import date, timedelta

import streamlit as st

import db
from config import CATEGORIES


def _category_options() -> list[str]:
    """筛选器可选的岗位分类 = 新分类体系 + 数据库里尚未迁移的旧类目值。

    「开发」「算法/AI」这类一对多拆分的旧值不会被自动迁移（见
    db._migrate_legacy_categories），如果这里只列 CATEGORIES，用户手动取消勾选
    某几个分类时，这些老记录会因为不在选项里而被静默过滤掉。把它们补在末尾，
    既能选到也能一眼看出哪些记录还没归好类。
    """
    try:
        existing = db.get_distinct_categories()
    except Exception:
        existing = []
    legacy = [c for c in existing if c not in CATEGORIES]
    return CATEGORIES + sorted(legacy)


def render_filters(key_prefix: str):
    """渲染筛选控件，返回 (start_date_iso, end_date_iso, categories)。

    返回值可直接传给 db.list_applications(start_date=..., end_date=..., categories=...)：
    - 未勾选日期筛选时，start_date/end_date 为 None（不筛选）；
      勾选后选同一天即筛选单日，选不同两天即筛选区间。
    - 岗位分类全选（或一个都没选）时 categories 为 None（不筛选）；
      仅取消部分分类时才生效。
    """
    col1, col2 = st.columns([2, 3])

    start_date = end_date = None
    with col1:
        use_date_filter = st.checkbox("按投递日期筛选", key=f"{key_prefix}_use_date")
        if use_date_filter:
            date_range = st.date_input(
                "日期范围（选同一天即筛选单日）",
                value=(date.today() - timedelta(days=30), date.today()),
                key=f"{key_prefix}_date_range",
            )
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_date, end_date = date_range[0].isoformat(), date_range[1].isoformat()
            elif isinstance(date_range, date):
                start_date = end_date = date_range.isoformat()

    with col2:
        options = _category_options()
        selected = st.multiselect(
            "按岗位分类筛选",
            options=options,
            default=options,
            key=f"{key_prefix}_categories",
        )
        categories = None if not selected or set(selected) == set(options) else selected

    return start_date, end_date, categories
