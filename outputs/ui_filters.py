"""
ui_filters.py
----------------
投递记录的日期范围 + 岗位分类筛选控件，被「全部记录」与「投递看板」两个页面
共用，避免同一套筛选逻辑维护两份。
"""

from datetime import date, timedelta

import streamlit as st

from config import CATEGORIES


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
        selected = st.multiselect(
            "按岗位分类筛选",
            options=CATEGORIES,
            default=CATEGORIES,
            key=f"{key_prefix}_categories",
        )
        categories = None if not selected or set(selected) == set(CATEGORIES) else selected

    return start_date, end_date, categories
