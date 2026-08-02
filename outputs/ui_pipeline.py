"""
ui_pipeline.py
----------------
投递看板板块：以统计卡片 + 漏斗分析图 + 阶段日期表汇总所有投递记录的进度，
用于看清每一阶段的转化率，以及发现"好久没动静、可以催一催"的记录。

- 统计卡片：总投递数 / 进行中 / 已收offer / 可以催一下
- 漏斗分析图：最上层是总投递数，最底层是拿到 offer 的数量，逐层展示转化率
- 阶段日期表：status_history 按阶段展开成列，好久没动静的记录整行标一层柔和的暖色

漏斗口径（见 config.FUNNEL_STAGES 的注释）：按"至少走到了这一层"统计，
每条投递取它**历史上到过的最深阶段**，而不是当前状态——状态一旦转成
已拒绝/已终止就把之前的进度覆盖掉了，只看当前状态会把走到过三面才被拒的
投递算成"没进过面试"，漏斗会失真。

跟进提示的判定逻辑：取每条记录相邻两次状态变更之间的间隔天数，以及"当前状态
已持续到现在"的天数（尚未到终态时），二者中的最大值超过阈值即视为"该催一下了"。
历史遗留数据（建表前已存在的投递）只补得到一条初始记录，因此只能判断"当前状态
已持续多久"。

文案基调：求职本身压力就不小，这里尽量用温和、鼓励的语气，不用"停滞""警告"
这类容易让人emo的词，标黄也做成半透明的暖色色块而不是刺眼的实心黄。
"""

from datetime import date, datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

import db
from config import (
    FUNNEL_STAGES,
    STALE_DAYS_THRESHOLD,
    STALE_WARNING_COLOR,
    STATUS_COLOR,
    STATUS_FLOW,
    TERMINAL_STATUSES,
    status_depth,
)
from timezone_utils import parse_iso, utc_iso_to_local
from ui_filters import render_filters


def _compute_pipeline(
    threshold_days: float,
    start_date: str = None,
    end_date: str = None,
    categories: list[str] = None,
) -> pd.DataFrame:
    """把 applications + status_history 拼装成投递看板所需的数据（一行一条投递）。

    除了展示用的列，还额外算一列 `_depth`：这条投递**历史上到过的最深阶段**
    在 STATUS_FLOW 里的下标（终止态不算深度），供漏斗分析图按"至少走到这一层"
    统计。取历史最大值而不是当前状态，是因为状态转成已拒绝/已终止之后，
    当前状态里就看不出它曾经走到过哪一步了。
    """
    apps = db.list_applications(start_date=start_date, end_date=end_date, categories=categories)
    if apps.empty:
        return apps

    history = db.get_all_status_history()
    now = datetime.now(timezone.utc)

    rows = []
    for app in apps.itertuples():
        app_hist = history[history["application_id"] == app.id].sort_values("changed_at")
        stage_dates = {s: None for s in STATUS_FLOW}
        change_times = []
        # 当前状态也参与深度计算：万一某条记录的历史缺了一截（比如更早版本
        # 写入的数据），至少不会把它算得比现在还浅
        depths = [status_depth(app.status)]
        for h in app_hist.itertuples():
            ts = parse_iso(h.changed_at)
            change_times.append(ts)
            depths.append(status_depth(h.to_status))
            if h.from_status:
                depths.append(status_depth(h.from_status))
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
            "_depth": max(depths),
        }
        for s in STATUS_FLOW:
            ts = stage_dates[s]
            row[s] = utc_iso_to_local(ts.isoformat()).strftime("%Y-%m-%d") if ts else ""
        rows.append(row)

    return pd.DataFrame(rows)


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


def build_funnel_data(df: pd.DataFrame) -> pd.DataFrame:
    """按 FUNNEL_STAGES 逐层统计"至少走到了这一层"的投递数与转化率。

    - 层内人数：`_depth >= 该层所需的最低状态深度` 的投递条数。
      「已投递」层的门槛是 STATUS_FLOW 里的「已投递」，所以「简历筛选中」
      自然并进第一层；走到「HR面」的投递深度大于「三面」，会被算进三面层，
      但 HR面 不单独占一层。
    - 阶段转化率：本层 / 上一层（第一层恒为 100%）。
    - 整体转化率：本层 / 第一层（总投递数）。

    抽成模块级函数（不是私有的 `_` 开头）是为了能被测试直接调用验证口径。
    """
    total = len(df) if "_depth" in df.columns else 0
    rows = []
    prev_count = None
    for label, min_status in FUNNEL_STAGES:
        need = status_depth(min_status)
        count = int((df["_depth"] >= need).sum()) if total else 0
        rows.append(
            {
                "阶段": label,
                "人数": count,
                "阶段转化率": 1.0 if prev_count is None else (count / prev_count if prev_count else 0.0),
                "整体转化率": count / total if total else 0.0,
                "较上一阶段流失": 0 if prev_count is None else prev_count - count,
            }
        )
        prev_count = count
    return pd.DataFrame(rows)


def _render_funnel(df: pd.DataFrame) -> None:
    st.markdown("#### 🔻 投递漏斗分析")
    st.caption(
        "从上到下：总投递 → 拿到 offer。口径是「至少走到过这一层」，"
        "被拒/终止不单独占一层，它体现为下一层人数的减少"
    )

    funnel = build_funnel_data(df)
    total = int(funnel.iloc[0]["人数"])

    # 居中对称的条形图就是漏斗：每层以 0 为中心向两侧展开，宽度=人数
    plot = funnel.copy()
    plot["start"] = -plot["人数"] / 2
    plot["end"] = plot["人数"] / 2
    # 文字层要落在色块正中央。这里必须用一个值为 0 的数据列走 x 编码，
    # 不能写 alt.value(0)——那是像素坐标 0（画布最左边），文字会跑到
    # y 轴标签上叠成一团
    plot["中心"] = 0.0
    plot["标注"] = plot.apply(
        lambda r: f"{int(r['人数'])} 条 · 占总投递 {r['整体转化率']:.0%}", axis=1
    )
    stage_order = plot["阶段"].tolist()
    half_span = max(total / 2, 0.5) * 1.25  # 两侧留白，文字不会顶到边

    base = alt.Chart(plot).encode(
        y=alt.Y("阶段:N", sort=stage_order, title=None, axis=alt.Axis(labelFontSize=13)),
    )
    bars = base.mark_bar(cornerRadius=4, height=34).encode(
        x=alt.X(
            "start:Q",
            title=None,
            axis=None,
            scale=alt.Scale(domain=[-half_span, half_span]),
        ),
        x2="end:Q",
        color=alt.Color(
            "阶段:N",
            scale=alt.Scale(
                domain=stage_order,
                range=[STATUS_COLOR.get(s, "#64748b") for s in stage_order],
            ),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("阶段:N", title="阶段"),
            alt.Tooltip("人数:Q", title="投递数"),
            alt.Tooltip("阶段转化率:Q", title="较上一阶段", format=".0%"),
            alt.Tooltip("整体转化率:Q", title="占总投递", format=".0%"),
        ],
    )
    # 人数标在色块正中间；色块窄到放不下时文字会压在背景上，深色主题下依然清楚
    labels = base.mark_text(fontSize=12, color="#ffffff", fontWeight="bold").encode(
        x=alt.X("中心:Q", title=None, axis=None), text=alt.Text("标注:N")
    )
    chart = (bars + labels).properties(height=52 * len(plot))
    # 居中对称的坐标轴对读数没有意义（负半轴只是为了对称），干脆整条隐藏
    st.altair_chart(chart.configure_view(stroke=None), width="stretch")

    view = funnel.copy()
    view["人数"] = view["人数"].astype(int)
    view["阶段转化率"] = view["阶段转化率"].map(lambda v: f"{v:.0%}")
    view["整体转化率"] = view["整体转化率"].map(lambda v: f"{v:.0%}")
    view = view.rename(
        columns={
            "人数": "投递数",
            "阶段转化率": "较上一阶段转化率",
            "整体转化率": "占总投递",
            "较上一阶段流失": "本层流失",
        }
    )
    st.dataframe(view, width="stretch", hide_index=True)

    offers = int(funnel.iloc[-1]["人数"])
    if offers:
        st.caption(f"🎉 {total} 条投递里已经有 {offers} 条拿到 offer 啦")
    else:
        # 找出流失最多的那一层，给一句具体的提示（比"加油"有用）
        drops = funnel.iloc[1:]
        if not drops.empty and drops["较上一阶段流失"].max() > 0:
            worst = drops.loc[drops["较上一阶段流失"].idxmax()]
            st.caption(
                f"💭 目前卡得最多的是「{worst['阶段']}」这一层，"
                f"有 {int(worst['较上一阶段流失'])} 条没能走到这里"
            )


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

    df = _compute_pipeline(
        threshold_days, start_date=start_date, end_date=end_date, categories=categories
    )
    if df.empty:
        st.info("这里还空着呢～去「全部记录」新增或导入数据，或者调整一下筛选条件吧")
        return

    _render_stat_cards(df)
    st.divider()
    _render_funnel(df)
    st.divider()
    _render_table(df)
