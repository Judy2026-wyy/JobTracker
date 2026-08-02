"""
ui_email_sync.py
-----------------
163 邮箱同步板块：连接 IMAP -> 拉取近期邮件 -> LLM 解析 -> 查重 -> 按邮件类型
分组预览候选记录 -> 用户勾选确认后批量写入数据库（source="email"）。

设计上采用"预览-确认"两步，避免误判邮件导致脏数据直接落库。

【查重与合并】
解析出候选记录后，会先拿它和数据库里已有的投递比一遍（见 dedup.suggest_match）：
公司名归一化后一致（如「德勤」与「德勤中国」）且岗位对得上的，规则层直接判定；
像但不敢确定的，交给大模型判断。只要判出疑似同一次投递，就在卡片上**询问用户**：
- 选「同一条投递」：不新建记录，直接把已有记录的状态往前推（见 db.advance_status），
  邮件里的备注追加到原记录上，面试时间也挂到原记录名下。
- 选「不是同一条」：正常新建一条记录。

候选记录按邮件类型（笔试 / 面试 / offer通知 / 其他）分 tab 展示。对不想入库的
邮件提供两种处理方式，都只影响本次预览，不会去动 163 邮箱里的原始邮件：
- 跳过：本次不导入，但仍留在原来的类型分组里。
- 忽略：本次不导入，并把它归到「其他」分组（适合反复被误判成求职相关的邮件）。
两种操作都能撤销（点「↩️ 恢复」），下次重新同步时所有标记都会重置。
"""

from datetime import date

import streamlit as st

import db
import dedup
from config import EMAIL_TYPE_OTHER, EMAIL_TYPES
from email_import import fetch_and_parse
from timezone_utils import format_local

ACTION_SKIP = "skip"
ACTION_IGNORE = "ignore"

CHOICE_MERGE = "same"
CHOICE_NEW = "new"

# 疑似重复的把握达到这个值，默认就先帮用户选中「同一条投递」（用户仍可改）
MERGE_DEFAULT_CONFIDENCE = 0.6


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
        local_time = format_local(candidate["start_time_utc"])
        confirmed_tag = "" if candidate.get("timezone_confirmed") else "（时区待确认）"
        parts.append(f"面试时间: {local_time} {confirmed_tag}")
    if candidate.get("assessment_deadline_utc"):
        deadline = format_local(candidate["assessment_deadline_utc"])
        confirmed_tag = "" if candidate.get("assessment_deadline_confirmed") else "（时区待确认）"
        parts.append(f"⏳ 测评最晚提交: {deadline} {confirmed_tag}")
    return " · ".join(parts)


def _attach_duplicate_suggestions(candidates: list[dict], use_llm: bool = True) -> int:
    """给每条候选记录挂上「疑似同一次投递」的已有记录（没有则为 None）。

    返回找到疑似重复的条数。查重失败（比如大模型不可用）不影响导入流程，
    只是这条记录当作全新记录处理。
    """
    apps_df = db.list_applications()
    records = apps_df.to_dict("records") if not apps_df.empty else []
    found = 0
    for candidate in candidates:
        try:
            suggestion = dedup.suggest_match(
                company=candidate.get("company", ""),
                position=candidate.get("position", ""),
                records=records,
                notes=candidate.get("notes", ""),
                status=candidate.get("status", ""),
                use_llm=use_llm,
            )
        except Exception:
            suggestion = None
        candidate["dup"] = suggestion
        if suggestion:
            found += 1
    return found


def _render_duplicate_question(idx: int, candidate: dict) -> None:
    """询问用户：这封邮件说的是不是数据库里已有的那条投递？"""
    suggestion = candidate.get("dup")
    if not suggestion:
        return

    record = suggestion["record"]
    new_status = candidate.get("status") or "已投递"
    st.warning(
        f"🔎 数据库里已经有一条很像的投递：**#{record['id']} {record.get('company', '')} · "
        f"{record.get('position', '')}**（当前状态：{record.get('status', '')}，"
        f"投递日期：{record.get('applied_date', '')}）",
        icon="⚠️",
    )
    st.caption(
        f"判断依据：{suggestion['reason']}｜把握 {suggestion['confidence']:.0%}"
        f"｜{'大模型判定' if suggestion['decided_by'] == 'llm' else '规则判定'}"
    )
    default_index = 0 if suggestion["confidence"] >= MERGE_DEFAULT_CONFIDENCE else 1
    st.radio(
        "这封邮件说的是同一次投递吗？",
        options=[CHOICE_MERGE, CHOICE_NEW],
        format_func=lambda choice: (
            f"✅ 是同一条投递：不新建，把 #{record['id']} 的状态更新为「{new_status}」"
            if choice == CHOICE_MERGE
            else "🆕 不是同一条：另外新建一条记录"
        ),
        index=default_index,
        key=f"email_dup_choice_{idx}",
    )


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
            _render_duplicate_question(idx, candidate)

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


def _merge_into_existing(app_id: int, candidate: dict, status: str) -> str:
    """把一条邮件候选合并进已有投递记录：推进状态 + 追加备注。返回给用户看的一句话。"""
    changed, msg = db.advance_status(app_id, status)
    note = (candidate.get("notes") or "").strip()
    subject = (candidate.get("source_subject") or "").strip()
    if note or subject:
        stamp = (candidate.get("source_date") or "")[:10] or date.today().isoformat()
        db.append_notes(app_id, f"【邮件同步 {stamp}】{subject}{'：' + note if note else ''}")
    return f"#{app_id} {msg}"


def _find_in_batch_match(company: str, position: str, processed: list[dict]) -> int | None:
    """在「本次导入刚处理过的记录」里找同一次投递。

    同步时这些记录还不在库里，没机会拿去问用户（比如同一次同步里既有笔试通知
    又有面试通知，说的是同一条投递）。所以这里只用规则层兜一道，且只认高置信度
    的匹配，不调大模型——不该为了去重在导入环节偷偷再花一次 API 调用。
    """
    candidates = dedup.find_candidates(company, position, processed)
    if not candidates:
        return None
    top = candidates[0]
    if top["certain"] or top["combined"] >= dedup.FALLBACK_COMBINED_MIN:
        return int(top["record"]["id"])
    return None


def _import_candidates(candidates: list[dict], actions: dict) -> None:
    """把用户勾选的候选写库：选了「同一条投递」的合并进已有记录，其余新建。"""
    imported, merged, duplicated, passed, deadlines = 0, 0, 0, 0, 0
    details = []
    # 本次导入已经处理过的记录（新建的 + 合并进去的），用来拦住"同一条投递
    # 在一次同步里被登记两遍"
    processed: list[dict] = []

    for idx, c in enumerate(candidates):
        if actions.get(idx) in (ACTION_SKIP, ACTION_IGNORE):
            passed += 1
            continue
        if not st.session_state.get(f"email_cand_{idx}", True):
            passed += 1
            continue

        company = c.get("company") or "未知公司"
        position = c.get("position") or "未知岗位"
        status = c.get("status") or "已投递"

        suggestion = c.get("dup")
        choice = st.session_state.get(f"email_dup_choice_{idx}") if suggestion else None

        if suggestion and choice == CHOICE_MERGE:
            target_id = suggestion["application_id"]
        else:
            target_id = _find_in_batch_match(company, position, processed)

        if target_id is not None:
            details.append(_merge_into_existing(target_id, c, status))
            merged += 1
            app_id = target_id
        else:
            app_id, created = db.upsert_application(
                company=company,
                position=position,
                applied_date=date.today().isoformat(),
                status=status,
                source="email",
                notes=c.get("notes", ""),
                category=c.get("category", "其他"),
                # 用户明确回答「不是同一条」时，绕开归一化去重强行新建；
                # 没有疑似重复的记录仍然走数据层去重兜底
                allow_duplicate=bool(suggestion and choice == CHOICE_NEW),
            )
            if created:
                imported += 1
            else:
                duplicated += 1
                details.append(f"#{app_id}「{company} · {position}」库里已经有了，没重复新建")

        if app_id and not any(record["id"] == app_id for record in processed):
            saved = db.get_application(app_id)
            if saved:
                processed.append(saved)

        if app_id and c.get("has_interview_time") and c.get("start_time_utc"):
            db.add_interview(
                application_id=app_id,
                round_name=status if status else "面试",
                start_time_utc=c["start_time_utc"],
                timezone_confirmed=bool(c.get("timezone_confirmed")),
                location_or_link=c.get("location_or_link", ""),
                notes=c.get("notes", ""),
            )

        # 笔试/测评截止时间写到投递记录上，首页「近期笔试 Deadline」直接读它。
        # 合并进已有记录时也要写：一条投递先收到"已投递"确认、后收到测评通知，
        # 截止时间就是靠这一步补上去的
        if app_id and c.get("assessment_deadline_utc"):
            existing = db.get_application(app_id) or {}
            old_deadline = (existing.get("assessment_deadline_utc") or "").strip()
            new_deadline = c["assessment_deadline_utc"]
            # 已有更早的截止时间就别覆盖了（同一条投递可能有多封测评邮件，
            # 以最紧的那个为准，宁可早提醒也不能把 deadline 往后推）
            if not old_deadline or new_deadline < old_deadline:
                db.set_assessment_deadline(
                    app_id, new_deadline, confirmed=bool(c.get("assessment_deadline_confirmed"))
                )
                deadlines += 1
                details.append(
                    f"#{app_id}「{company}」记下了测评截止时间："
                    f"{format_local(new_deadline)}"
                    + ("" if c.get("assessment_deadline_confirmed") else "（时区待确认）")
                )

    summary = f"导入完成啦：新增 {imported} 条"
    if merged:
        summary += f"，合并到已有记录 {merged} 条"
    if deadlines:
        summary += f"，记下 {deadlines} 个测评截止时间"
    if duplicated:
        summary += f"，重复的 {duplicated} 条没有再收一遍"
    if passed:
        summary += f"，另有 {passed} 条按你的选择没有导入"
    st.success(summary)
    for line in details:
        st.caption(line)

    st.session_state["email_candidates"] = []
    st.session_state["email_cand_actions"] = {}


def render_email_sync():
    st.subheader("📧 163 邮箱同步")
    st.caption("自动读取近期邮件，识别投递进度更新与面试邀约，确认后导入数据库")

    st.session_state.setdefault("email_cand_actions", {})

    col1, col2 = st.columns(2)
    with col1:
        days = st.slider("同步最近几天的邮件", min_value=1, max_value=60, value=14)
    with col2:
        limit = st.slider("最多扫描邮件数", min_value=10, max_value=200, value=50)

    use_llm_dedup = st.checkbox(
        "用大模型判断「是不是同一次投递」（推荐）",
        value=True,
        key="email_use_llm_dedup",
        help="公司名写法不同（如「德勤」和「德勤中国」）时，交给大模型判断是不是同一次投递；"
        "取消勾选则只用归一化规则比对，不额外消耗 API 调用",
    )

    if st.button("🔄 开始同步", key="btn_sync_email"):
        with st.spinner("正在连接163邮箱并解析邮件，请稍候..."):
            try:
                candidates = fetch_and_parse(days=days, limit=limit)
            except Exception as e:
                st.error(f"同步没成功，看看是不是这个问题：{e}")
                candidates = None
                st.session_state["email_candidates"] = []

        if candidates is not None:
            dup_count = 0
            if candidates:
                with st.spinner("正在和已有投递记录比对，找出可能重复的记录..."):
                    dup_count = _attach_duplicate_suggestions(candidates, use_llm=use_llm_dedup)
            st.session_state["email_candidates"] = candidates
            st.session_state["email_cand_actions"] = {}  # 新一次同步，清空上次的标记
            if candidates:
                st.success(f"找到 {len(candidates)} 条看起来和求职有关的邮件，来看看吧")
                if dup_count:
                    st.info(f"其中 {dup_count} 条和库里已有的投递很像，下面会逐条问你是不是同一次投递")
            else:
                st.info("这次没找到相关邮件，说不定过阵子再来看看～")

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
        _import_candidates(candidates, actions)
