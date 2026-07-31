"""
ui_pipeline.py
----------------
投递看板板块：以统计卡片 + 时间线图 + 阶段日期表汇总所有投递记录的进度，
用于快速发现"好久没动静、可以催一催"的记录。

- 统计卡片：总投递数 / 进行中 / 已收offer / 可以催一下
- 进度时间线：每条投递记录从投递到当前（或终态）的时间跨度，按当前状态着色
- 阶段日期表：status_history 按阶段展开成列，好久没动静的记录整行标一层柔和的暖色

判定逻辑：取每条记录相邻两次状态变更之间的间隔天数，以及"当前状态已持续到
现在"的天数（尚未到终态时），二者中的最大值超过阈值即视为"该催一下了"。历史
遗留数据（建表前已存在的投递）只补得到一条初始记录，因此只能判断"当前状态已
持续多久"。

文案基调：求职本身压力就不小，这里尽量用温和、鼓励的语气，不用"停滞""警告"
这类容易让人emo的词，标黄也做成半透明的暖色色块而不是刺眼的实心黄。
"""

from datetime import date, datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

import db
from config import STATUS_FLOW, TERMINAL_STATUSES, STATUS_COLOR, STALE_WARNING_COLOR, STALE_DAYS_THRESHOLD
from timezone_utils import parse_iso, utc_iso_to_chicago
from ui_filters import render_filters


def _compute_pipeline(
    threshold_days: float,
    start_date: str = None,
    end_date: str = None,
    categories: list[str] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """把 applications + status_history 拼装成投递看板所需的数据。

    返回 (df, segments_df)：
    - df：一行一条投递记录，供统计卡片、阶段日期表使用。
    - segments_df：一行一个"阶段区间"，供进度时间线按阶段分段着色使用
      （同一条投递按 status_history 拆成多段：每段从进入某阶段的时间，
      到进入下一阶段的时间为止；最后一段延伸到当前时间）。
    """
    apps = db.list_applications(start_date=start_date, end_date=end_date, categories=categories)
    if apps.empty:
        return apps, pd.DataFrame()

    history = db.get_all_status_history()
    now = datetime.now(timezone.utc)

    rows = []
    segment_rows = []
    for app in apps.itertuples():
        label = f"{app.company} · {app.position}"
        app_hist = history[history["application_id"] == app.id].sort_values("changed_at")
        stage_dates = {s: None for s in STATUS_FLOW}
        change_times = []
        change_statuses = []
        for h in app_hist.itertuples():
            ts = parse_iso(h.changed_at)
            change_times.append(ts)
            change_statuses.append(h.to_status)
            if h.to_status in stage_dates:
                stage_dates[h.to_status] = ts

        gaps_days = [
            (change_times[i + 1] - change_times[i]).days
            for i in range(len(change_times) - 1)
        ]
        if change_times and app.status not in TERMINAL_STATUSES:
            gaps_days.append((now - change_times[-1]).days)
        max_gap = max(gaps_days) if gaps_days else 0

        start_ts = change_times[0] if change_times else parse_iso(app.status_updated_at)

        row = {
            "id": app.id,
            "company": app.company,
            "position": app.position,
            "岗位分类": app.category,
            "当前状态": app.status,
            "最大间隔天数": int(max_gap),
            "跟进提示": "💭 催一下" if max_gap > threshold_days else "",
            "_start": start_ts,
        }
        for s in STATUS_FLOW:
            ts = stage_dates[s]
            row[s] = utc_iso_to_chicago(ts.isoformat()).strftime("%Y-%m-%d") if ts else ""
        rows.append(row)

        for i in range(len(change_times)):
            seg_start = change_times[i]
            seg_end = change_times[i + 1] if i + 1 < len(change_times) else now
            local_start = utc_iso_to_chicago(seg_start.isoformat())
            segment_rows.append(
                {
                    "投递": label,
                    "company": app.company,
                    "position": app.position,
                    "阶段": change_statuses[i],
                    "start": seg_start,
                    "end": seg_end,
                    "start_label": f"{local_start.month}/{local_start.day}",
                }
            )

    return pd.DataFrame(rows), pd.DataFrame(segment_rows)


def _render_stat_cards(df: pd.DataFrame) -> None:
    total = len(df)
    in_progress = int((~df["当前状态"].isin(TERMINAL_STATUSES + ["已收offer"])).sum())
    offers = int((df["当前状态"] == "已收offer").sum())
    stale = int((df["跟进提示"] != "").sum())

    col1, col2, col3, col4 = st.columns(4)
    with col1, st.container(border=True):
        st.metric("总投递数", total)
    with col2, st.container(border=True):
        st.metric("进行中", in_progress)
    with col3, st.container(border=True):
        st.metric("已收offer", offers)
        if offers:
            st.caption("🎉 太棒了")
    with col4, st.container(border=True):
        st.metric("可以催一下", stale)
        if stale:
            st.caption("💭 有几条好久没消息啦，下面表格里看看～")


def _render_timeline(df: pd.DataFrame, segments_df: pd.DataFrame) -> None:
    st.markdown("#### 🗂️ 投递进度时间线")
    st.caption("每条投递是一根横条，按阶段分段着色；每段开头的小字是进入该阶段的日期")

    order = df.sort_values("_start")["company"].str.cat(df.sort_values("_start")["position"], sep=" · ").tolist()

    bars = (
        alt.Chart(segments_df)
        .mark_bar(cornerRadius=4, height=14)
        .encode(
            x=alt.X("start:T", title=None),
            x2="end:T",
            y=alt.Y("投递:N", sort=order, title=None),
            color=alt.Color(
                "阶段:N",
                scale=alt.Scale(domain=list(STATUS_COLOR.keys()), range=list(STATUS_COLOR.values())),
                legend=alt.Legend(title="阶段", orient="bottom", columns=5),
            ),
            tooltip=[
                alt.Tooltip("company:N", title="公司"),
                alt.Tooltip("position:N", title="岗位"),
                alt.Tooltip("阶段:N", title="阶段"),
                alt.Tooltip("start:T", title="开始", format="%Y-%m-%d"),
                alt.Tooltip("end:T", title="结束/至今", format="%Y-%m-%d"),
            ],
        )
    )

    labels = (
        alt.Chart(segments_df)
        .mark_text(align="left", dx=3, dy=-10, fontSize=9, color="#c3c2b7")
        .encode(
            x=alt.X("start:T"),
            y=alt.Y("投递:N", sort=order),
            text=alt.Text("start_label:N"),
        )
    )

    chart = (bars + labels).properties(height=max(220, 36 * len(df)))
    st.altair_chart(chart, width="stretch")


def _highlight_stale(row: pd.Series) -> list:
    # 半透明暖色色块而非实心刺眼黄，保留提醒作用但视觉上更柔和；
    # 文字颜色不强制改黑色，跟深色主题的白字叠加出来是柔和的暖棕色调
    r, g, b = int(STALE_WARNING_COLOR[1:3], 16), int(STALE_WARNING_COLOR[3:5], 16), int(STALE_WARNING_COLOR[5:7], 16)
    style = f"background-color: rgba({r}, {g}, {b}, 0.22);" if row["跟进提示"] else ""
    return [style] * len(row)


def _render_table(df: pd.DataFrame) -> None:
    st.markdown("#### 📋 阶段日期表")

    display_cols = ["id", "company", "position", "岗位分类", "当前状态"] + STATUS_FLOW + ["最大间隔天数", "跟进提示"]
    view = df[display_cols].rename(columns={"id": "ID", "company": "公司", "position": "岗位"})

    styled = view.style.apply(_highlight_stale, axis=1).hide(axis="index")
    st.dataframe(styled, width="stretch")
    st.caption("🌼 暖色行：这个阶段有点久没动静了，去跟进一下吧～不代表没戏，可能只是HR比较忙")

    csv_bytes = view.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📤 导出阶段日期表为 CSV",
        data=csv_bytes,
        file_name=f"求职投递看板_{date.today().isoformat()}.csv",
        mime="text/csv",
        key="btn_export_pipeline_csv",
    )


def render_pipeline_page() -> None:
    st.subheader("📊 投递看板")
    st.caption("汇总所有投递记录的进度，好久没动静的会轻轻标一层暖色提醒你去catch up～")

    start_date, end_date, categories = render_filters("pipeline")
    threshold_days = st.slider(
        "多久没进展就提醒我一下（天）",
        min_value=1,
        max_value=30,
        value=STALE_DAYS_THRESHOLD,
        key="pipeline_stale_threshold",
    )

    df, segments_df = _compute_pipeline(
        threshold_days, start_date=start_date, end_date=end_date, categories=categories
    )
    if df.empty:
        st.info("这里还空着呢～去「全部记录」新增或导入数据，或者调整一下筛选条件吧")
        return

    _render_stat_cards(df)
    st.divider()
    _render_timeline(df, segments_df)
    st.divider()
    _render_table(df)
