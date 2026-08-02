"""
db.py
-----
数据持久层：SQLite 建表与全部读写操作。

约定（按任务要求）：
- 写入操作一律使用 sqlite3 原生接口。
- 查询展示统一用 pandas.read_sql 返回 DataFrame，方便上层 UI 直接渲染表格。
- applications 表通过 UNIQUE(company, position, applied_date) 实现导入去重。
- interviews 表通过外键关联 applications，并记录 timezone_confirmed 标志。
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import (
    CATEGORIES,
    DB_PATH,
    DEFAULT_CATEGORY,
    LEGACY_MAPPING,
    STATUS_FLOW,
    TERMINAL_STATUSES,
    get_valid_next_statuses,
    is_forward_status,
)
from dedup import normalize_company, normalize_position


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """初始化数据库表结构（存在则跳过）。应用启动时调用一次即可。"""
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                position TEXT NOT NULL,
                job_link TEXT,
                applied_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '已投递',
                status_updated_at TEXT NOT NULL,
                source TEXT NOT NULL CHECK(source IN ('screenshot','email','manual')),
                notes TEXT,
                category TEXT NOT NULL DEFAULT '其他',
                assessment_deadline_utc TEXT,
                assessment_deadline_confirmed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(company, position, applied_date)
            );

            CREATE TABLE IF NOT EXISTS interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                round TEXT NOT NULL,
                start_time_utc TEXT NOT NULL,
                timezone_confirmed INTEGER NOT NULL DEFAULT 0,
                location_or_link TEXT,
                notes TEXT,
                FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                FOREIGN KEY (application_id) REFERENCES applications(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        reset_settings_cache()  # 换库/重新初始化时别把上一个库的设置带过来
        _ensure_application_columns(conn)
        _migrate_legacy_categories(conn)
        _backfill_status_history(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_application_columns(conn: sqlite3.Connection) -> None:
    """给建表前已存在的 applications 表补上后加的字段。

    CREATE TABLE IF NOT EXISTS 对已存在的表不会补字段，只能 ALTER TABLE 迁移。
    可重复执行：每次只补当前缺的列。
    """
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(applications);").fetchall()]
    pending = {
        "category": f"TEXT NOT NULL DEFAULT '{DEFAULT_CATEGORY}'",
        # 笔试/测评的最晚提交时间（UTC ISO），供首页「近期笔试 Deadline」使用
        "assessment_deadline_utc": "TEXT",
        # 邮件里是否明确写了时区；没写的按北京时间解释并标注待确认
        "assessment_deadline_confirmed": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, ddl in pending.items():
        if column not in cols:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {column} {ddl};")


def _migrate_legacy_categories(conn: sqlite3.Connection) -> int:
    """把旧岗位分类体系的历史数据回填成新体系的类目名。可重复执行。

    只迁移 LEGACY_MAPPING 里有明确一对一对应关系的旧值；像「开发」「算法/AI」
    这类一对多拆分的旧值（映射为 None）保持原样不动——自动猜一个新类目大概率
    是错的，宁可留着让用户自己改或重新识别一次。这些没迁移的值仍会出现在
    筛选器里（见 ui_filters.render_filters），不会因为不在 CATEGORIES 里就
    被静默过滤掉。

    返回实际被改写的记录数，便于测试与排查。
    """
    changed = 0
    for old_value, new_value in LEGACY_MAPPING.items():
        if not new_value or new_value == old_value or new_value not in CATEGORIES:
            continue
        cur = conn.execute(
            "UPDATE applications SET category=? WHERE category=?;", (new_value, old_value)
        )
        changed += cur.rowcount
    return changed


def get_distinct_categories() -> list[str]:
    """数据库里实际出现过的岗位分类值（含尚未迁移的旧值）。

    供筛选器把这些旧值也列进选项，避免"选了全部分类反而漏掉几条老记录"。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT category FROM applications WHERE category IS NOT NULL AND category != '';"
        ).fetchall()
        return [row["category"] for row in rows]
    finally:
        conn.close()


def _backfill_status_history(conn: sqlite3.Connection) -> None:
    """为建表前已存在的 applications 记录补一条初始状态历史。

    没有这一步，投递看板无法为老数据计算阶段日期与停滞天数。只对尚无
    历史记录的投递补一条 (from_status=NULL, to_status=当前状态)，可重复执行。
    """
    conn.execute(
        """
        INSERT INTO status_history (application_id, from_status, to_status, changed_at)
        SELECT a.id, NULL, a.status, a.status_updated_at
        FROM applications a
        WHERE NOT EXISTS (
            SELECT 1 FROM status_history h WHERE h.application_id = a.id
        );
        """
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# app_settings 表操作（应用级偏好设置，目前存展示时区）
# ---------------------------------------------------------------------------

# 设置项在一次进程里读得非常频繁（每渲染一个面试时间就要问一次时区），
# 而写入只发生在用户点下拉框的那一刻，所以在内存里缓存一份，写入时同步更新。
# 单机单用户应用，不存在别的进程偷偷改库的情况。
_SETTINGS_CACHE: dict[str, str] = {}
_SETTINGS_LOADED = False


def reset_settings_cache() -> None:
    """丢掉缓存，下次读设置时重新查库（切换数据库文件后必须调用）。"""
    global _SETTINGS_LOADED
    _SETTINGS_CACHE.clear()
    _SETTINGS_LOADED = False


def _load_settings() -> dict[str, str]:
    global _SETTINGS_LOADED
    if _SETTINGS_LOADED:
        return _SETTINGS_CACHE
    conn = get_connection()
    try:
        rows = conn.execute("SELECT key, value FROM app_settings;").fetchall()
        _SETTINGS_CACHE.clear()
        _SETTINGS_CACHE.update({row["key"]: row["value"] for row in rows})
        _SETTINGS_LOADED = True
    except sqlite3.OperationalError:
        # 表还没建（比如 init_db 之前就有人来读），当作"还没设置过"，用默认值
        pass
    finally:
        conn.close()
    return _SETTINGS_CACHE


def get_setting(key: str, default: str = None) -> str | None:
    return _load_settings().get(key, default)


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at;
            """,
            (key, value, _now_iso()),
        )
        conn.commit()
        _load_settings()[key] = value
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# applications 表操作
# ---------------------------------------------------------------------------

def find_normalized_duplicate(company: str, position: str, exclude_id: int = None) -> dict | None:
    """按归一化后的公司名+岗位名查找已有记录，找到返回该记录，否则 None。

    这是「同一次投递不要注册两遍」的数据层兜底：UNIQUE(company, position,
    applied_date) 只能拦住三个字段一模一样的重复，拦不住「德勤 / 德勤中国」
    这种写法不同、或者同一条投递隔几天又被邮件带进来一次（日期不同）的情况。
    归一化规则见 dedup.normalize_company / normalize_position。

    同时命中多条时返回 id 最小的那条（最早登记的），保证结果稳定可预期。
    """
    target = (normalize_company(company), normalize_position(position))
    if not target[0] and not target[1]:
        return None
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM applications ORDER BY id;").fetchall()
    finally:
        conn.close()
    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        if (normalize_company(row["company"]), normalize_position(row["position"])) == target:
            return dict(row)
    return None


def upsert_application(
    company: str,
    position: str,
    applied_date: str,
    status: str = "已投递",
    job_link: str = None,
    source: str = "manual",
    notes: str = None,
    category: str = DEFAULT_CATEGORY,
    allow_duplicate: bool = False,
) -> tuple[int, bool]:
    """插入新投递记录，命中去重则跳过插入。

    去重分两层：
    1. 归一化去重：公司名+岗位名归一化后一致就算同一次投递（不看投递日期），
       能拦住「德勤 / 德勤中国」「同一条投递隔天又被邮件带进来」这类重复。
    2. UNIQUE(company, position, applied_date) 约束：数据库层最后一道保险。

    allow_duplicate=True 时跳过第 1 层（用户已明确说「这不是同一条，就是要新建」），
    但第 2 层的数据库约束仍然生效。

    返回 (application_id, created)，created=False 表示命中去重、未新建。
    """
    if not allow_duplicate:
        existing = find_normalized_duplicate(company, position)
        if existing:
            return existing["id"], False

    now_iso = _now_iso()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO applications
                (company, position, job_link, applied_date, status, status_updated_at, source, notes, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company, position, applied_date) DO NOTHING;
            """,
            (company, position, job_link, applied_date, status, now_iso, source, notes, category),
        )
        created = cur.rowcount > 0
        row = conn.execute(
            "SELECT id FROM applications WHERE company=? AND position=? AND applied_date=?;",
            (company, position, applied_date),
        ).fetchone()
        if created and row:
            conn.execute(
                """
                INSERT INTO status_history (application_id, from_status, to_status, changed_at)
                VALUES (?, NULL, ?, ?);
                """,
                (row["id"], status, now_iso),
            )
        conn.commit()
        return (row["id"], created) if row else (None, False)
    finally:
        conn.close()


def update_status(application_id: int, new_status: str) -> tuple[bool, str]:
    """更新投递状态，写入前校验是否为合法的下一状态。

    返回 (是否成功, 提示信息)。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM applications WHERE id=?;", (application_id,)
        ).fetchone()
        if row is None:
            return False, "未找到该投递记录"
        current = row["status"]
        valid_next = get_valid_next_statuses(current)
        if new_status not in valid_next:
            return False, f"还不能这么跳：{current} → {new_status}，可以流转到的下一步是 {valid_next or '（没有了，这条记录已经到终点啦）'}"
        now_iso = _now_iso()
        conn.execute(
            "UPDATE applications SET status=?, status_updated_at=? WHERE id=?;",
            (new_status, now_iso, application_id),
        )
        conn.execute(
            """
            INSERT INTO status_history (application_id, from_status, to_status, changed_at)
            VALUES (?, ?, ?, ?);
            """,
            (application_id, current, new_status, now_iso),
        )
        conn.commit()
        return True, "状态更新好啦，继续加油～"
    finally:
        conn.close()


def advance_status(application_id: int, new_status: str) -> tuple[bool, str]:
    """把某条投递的状态直接推进到 new_status（允许一次跨多个阶段，但不允许倒退）。

    专供「外部信息带来的进度更新」使用（邮件同步确认合并到已有记录时）：
    邮件里说已经约二面了，而库里还停在「已投递」，这时用户不该被迫手动点
    四五次流转；但一封迟到的旧邮件也绝不能把记录打回更早的阶段。

    返回 (是否更新, 提示信息)。状态没变化/倒退时返回 (False, 原因)。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM applications WHERE id=?;", (application_id,)
        ).fetchone()
        if row is None:
            return False, "未找到该投递记录"
        current = row["status"]
        if new_status == current:
            return False, f"状态还是「{current}」，没变化"
        if not is_forward_status(current, new_status):
            return False, f"「{new_status}」比当前的「{current}」更早（或已到终止态），保持原状态不动"
        now_iso = _now_iso()
        conn.execute(
            "UPDATE applications SET status=?, status_updated_at=? WHERE id=?;",
            (new_status, now_iso, application_id),
        )
        conn.execute(
            """
            INSERT INTO status_history (application_id, from_status, to_status, changed_at)
            VALUES (?, ?, ?, ?);
            """,
            (application_id, current, new_status, now_iso),
        )
        conn.commit()
        return True, f"状态已从「{current}」更新为「{new_status}」"
    finally:
        conn.close()


def append_notes(application_id: int, text: str) -> None:
    """把一段新信息追加到备注末尾（已经包含同样内容时不重复追加）。

    合并投递记录时用：邮件里的新信息要留痕，但不能把原来截图提炼的岗位信息覆盖掉。
    """
    text = (text or "").strip()
    if not text:
        return
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT notes FROM applications WHERE id=?;", (application_id,)
        ).fetchone()
        if row is None:
            return
        old = (row["notes"] or "").strip()
        if text in old:
            return
        merged = f"{old}\n{text}".strip() if old else text
        conn.execute("UPDATE applications SET notes=? WHERE id=?;", (merged, application_id))
        conn.commit()
    finally:
        conn.close()


def update_application_fields(application_id: int, **fields) -> None:
    """更新 applications 表中的任意可编辑字段（company/position/job_link/notes 等）。"""
    if not fields:
        return
    allowed = {"company", "position", "job_link", "applied_date", "notes", "category"}
    set_clause = []
    values = []
    for k, v in fields.items():
        if k in allowed:
            set_clause.append(f"{k}=?")
            values.append(v)
    if not set_clause:
        return
    values.append(application_id)
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE applications SET {', '.join(set_clause)} WHERE id=?;", values
        )
        conn.commit()
    finally:
        conn.close()


def delete_application(application_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM applications WHERE id=?;", (application_id,))
        conn.commit()
    finally:
        conn.close()


def list_applications(
    start_date: str = None,
    end_date: str = None,
    categories: list[str] = None,
) -> pd.DataFrame:
    """查询投递记录，可选按投递日期范围 / 岗位分类筛选。

    start_date/end_date 为闭区间（含首尾两天），传同一天即筛选单日；
    categories 为空/None 表示不按分类筛选。
    """
    conn = get_connection()
    try:
        query = "SELECT * FROM applications"
        conditions = []
        params = []
        if start_date:
            conditions.append("date(applied_date) >= date(?)")
            params.append(start_date)
        if end_date:
            conditions.append("date(applied_date) <= date(?)")
            params.append(end_date)
        if categories:
            placeholders = ",".join("?" for _ in categories)
            conditions.append(f"category IN ({placeholders})")
            params.extend(categories)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY applied_date DESC, id DESC;"
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()


def find_duplicate_groups() -> list[list[dict]]:
    """扫描全表，把归一化后公司名+岗位名相同的记录分成一组，返回所有存在重复的组。

    每组按 id 升序，组内第一条是最早登记的（合并时默认保留它）。
    供「全部记录」页面的重复记录清理工具使用。
    """
    conn = get_connection()
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM applications ORDER BY id;")]
    finally:
        conn.close()

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (normalize_company(row["company"]), normalize_position(row["position"]))
        if not key[0] and not key[1]:
            continue  # 公司岗位都是空/占位值，不参与分组，避免把所有脏数据归成一堆
        groups.setdefault(key, []).append(row)
    return [group for group in groups.values() if len(group) > 1]


def merge_applications(keep_id: int, merge_id: int) -> tuple[bool, str]:
    """把 merge_id 这条投递合并进 keep_id，然后删除 merge_id。

    合并策略：
    - 面试安排、状态流转历史全部改挂到 keep_id 名下，一条都不丢；
    - 状态取两者中更靠前的那个（进度更深的赢），并补一条流转历史；
    - 投递日期取更早的那个（第一次投出去的时间才是真正的投递日）；
    - 备注拼接、投递链接/分类空缺时用被合并记录的值补上。
    """
    if keep_id == merge_id:
        return False, "保留记录和被合并记录不能是同一条"
    conn = get_connection()
    try:
        keep = conn.execute("SELECT * FROM applications WHERE id=?;", (keep_id,)).fetchone()
        merge = conn.execute("SELECT * FROM applications WHERE id=?;", (merge_id,)).fetchone()
        if keep is None or merge is None:
            return False, "要合并的记录已经不存在了，刷新一下再试"

        conn.execute(
            "UPDATE interviews SET application_id=? WHERE application_id=?;", (keep_id, merge_id)
        )
        conn.execute(
            "UPDATE status_history SET application_id=? WHERE application_id=?;",
            (keep_id, merge_id),
        )

        updates = {}
        if is_forward_status(keep["status"], merge["status"]):
            updates["status"] = merge["status"]
            updates["status_updated_at"] = _now_iso()
        if merge["applied_date"] and (
            not keep["applied_date"] or merge["applied_date"] < keep["applied_date"]
        ):
            updates["applied_date"] = merge["applied_date"]
        if not (keep["job_link"] or "").strip() and (merge["job_link"] or "").strip():
            updates["job_link"] = merge["job_link"]
        # 笔试截止时间：保留的那条没有就补上；两条都有则取更早的那个
        # （宁可提前提醒，也不能因为合并把更紧的 deadline 弄丢）
        keep_deadline = (keep["assessment_deadline_utc"] or "").strip()
        merge_deadline = (merge["assessment_deadline_utc"] or "").strip()
        if merge_deadline and (not keep_deadline or merge_deadline < keep_deadline):
            updates["assessment_deadline_utc"] = merge_deadline
            updates["assessment_deadline_confirmed"] = merge["assessment_deadline_confirmed"]
        if (keep["category"] in (None, "", DEFAULT_CATEGORY)) and merge["category"] not in (
            None,
            "",
            DEFAULT_CATEGORY,
        ):
            updates["category"] = merge["category"]

        keep_notes = (keep["notes"] or "").strip()
        merge_notes = (merge["notes"] or "").strip()
        if merge_notes and merge_notes not in keep_notes:
            updates["notes"] = f"{keep_notes}\n{merge_notes}".strip()

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE applications SET {set_clause} WHERE id=?;",
                [*updates.values(), keep_id],
            )
        if "status" in updates:
            conn.execute(
                """
                INSERT INTO status_history (application_id, from_status, to_status, changed_at)
                VALUES (?, ?, ?, ?);
                """,
                (keep_id, keep["status"], updates["status"], updates["status_updated_at"]),
            )

        conn.execute("DELETE FROM applications WHERE id=?;", (merge_id,))
        conn.commit()
        return True, f"已把 #{merge_id} 合并进 #{keep_id}"
    finally:
        conn.close()


def get_application(application_id: int):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM applications WHERE id=?;", (application_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# status_history 表操作
# ---------------------------------------------------------------------------

def get_all_status_history() -> pd.DataFrame:
    """全部投递记录的状态流转历史，按投递ID、变更时间排序。

    供「投递看板」计算各阶段到达日期、以及相邻两次流转之间的间隔天数
    （用于停滞预警），一次性查出全部记录比逐条查询更高效。
    """
    conn = get_connection()
    try:
        return pd.read_sql(
            """
            SELECT application_id, from_status, to_status, changed_at
            FROM status_history
            ORDER BY application_id, changed_at;
            """,
            conn,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# interviews 表操作
# ---------------------------------------------------------------------------

def add_interview(
    application_id: int,
    round_name: str,
    start_time_utc: str,
    timezone_confirmed: bool,
    location_or_link: str = None,
    notes: str = None,
) -> int:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO interviews
                (application_id, round, start_time_utc, timezone_confirmed, location_or_link, notes)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (application_id, round_name, start_time_utc, int(timezone_confirmed), location_or_link, notes),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_interviews(start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """联表查询面试记录 + 所属公司/岗位，可选按日期范围过滤（用于日历历史查询）。"""
    conn = get_connection()
    try:
        query = """
            SELECT i.id, i.application_id, i.round, i.start_time_utc,
                   i.timezone_confirmed, i.location_or_link, i.notes,
                   a.company, a.position, a.status
            FROM interviews i
            JOIN applications a ON a.id = i.application_id
        """
        conditions = []
        params = []
        if start_date:
            conditions.append("date(i.start_time_utc) >= date(?)")
            params.append(start_date)
        if end_date:
            conditions.append("date(i.start_time_utc) <= date(?)")
            params.append(end_date)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY i.start_time_utc ASC;"
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()


def get_upcoming_confirmed_interviews(hours: int = 48) -> pd.DataFrame:
    """未来 N 小时内、timezone_confirmed=1 的面试，按时间升序。"""
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=hours)
    conn = get_connection()
    try:
        query = """
            SELECT i.id, i.application_id, i.round, i.start_time_utc,
                   i.timezone_confirmed, i.location_or_link, i.notes,
                   a.company, a.position, a.status
            FROM interviews i
            JOIN applications a ON a.id = i.application_id
            WHERE i.timezone_confirmed = 1
              AND i.start_time_utc >= ?
              AND i.start_time_utc <= ?
            ORDER BY i.start_time_utc ASC;
        """
        return pd.read_sql(query, conn, params=[now.isoformat(), until.isoformat()])
    finally:
        conn.close()


def get_pending_confirmation_interviews() -> pd.DataFrame:
    """timezone_confirmed=0 的记录，单独分组展示，不参与正常排序。"""
    conn = get_connection()
    try:
        query = """
            SELECT i.id, i.application_id, i.round, i.start_time_utc,
                   i.timezone_confirmed, i.location_or_link, i.notes,
                   a.company, a.position, a.status
            FROM interviews i
            JOIN applications a ON a.id = i.application_id
            WHERE i.timezone_confirmed = 0
            ORDER BY i.id DESC;
        """
        return pd.read_sql(query, conn)
    finally:
        conn.close()


def confirm_interview_timezone(interview_id: int, start_time_utc: str = None) -> None:
    """用户人工确认某条待确认面试的时区/时间，将其标记为已确认。"""
    conn = get_connection()
    try:
        if start_time_utc:
            conn.execute(
                "UPDATE interviews SET timezone_confirmed=1, start_time_utc=? WHERE id=?;",
                (start_time_utc, interview_id),
            )
        else:
            conn.execute(
                "UPDATE interviews SET timezone_confirmed=1 WHERE id=?;", (interview_id,)
            )
        conn.commit()
    finally:
        conn.close()


def delete_interview(interview_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM interviews WHERE id=?;", (interview_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------

def get_status_counts() -> pd.DataFrame:
    conn = get_connection()
    try:
        df = pd.read_sql(
            "SELECT status, COUNT(*) AS count FROM applications GROUP BY status;", conn
        )
        return df
    finally:
        conn.close()


def get_daily_application_counts(start_date: str = None) -> pd.DataFrame:
    """按投递日期分组统计每天的投递数量，供首页投递趋势折线图使用。

    start_date: 只统计该日期（含）之后的记录；None 表示不限制，返回全部历史。
    返回列：applied_date（YYYY-MM-DD）、count。日期没有投递的不会补 0 行，
    由调用方按需重建连续日期轴。
    """
    conn = get_connection()
    try:
        query = "SELECT date(applied_date) AS applied_date, COUNT(*) AS count FROM applications"
        params = []
        if start_date:
            query += " WHERE date(applied_date) >= date(?)"
            params.append(start_date)
        query += " GROUP BY date(applied_date) ORDER BY date(applied_date);"
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 笔试/测评截止时间
# ---------------------------------------------------------------------------
# 说明：投递 -> 面试 转化率的统计已从首页移除，转化情况改由「投递看板」的
# 漏斗分析图逐层展示（口径更细，也不会被终止态覆盖掉进度），因此这里不再保留
# get_conversion_stats。首页空出来的位置改放「近期笔试 Deadline」。

def set_assessment_deadline(
    application_id: int, deadline_utc: str, confirmed: bool = True
) -> None:
    """写入某条投递的笔试/测评最晚提交时间（UTC ISO 字符串）。

    confirmed=False 表示邮件里没写清时区，是按默认时区（北京时间）推算出来的，
    展示时会标注"时区待确认"，提醒用户核对——截止时间算错一小时就可能错过提交。
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE applications SET assessment_deadline_utc=?, assessment_deadline_confirmed=? WHERE id=?;",
            (deadline_utc, int(confirmed), application_id),
        )
        conn.commit()
    finally:
        conn.close()


def clear_assessment_deadline(application_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE applications SET assessment_deadline_utc=NULL, assessment_deadline_confirmed=0 WHERE id=?;",
            (application_id,),
        )
        conn.commit()
    finally:
        conn.close()


def get_upcoming_assessment_deadlines(include_expired: bool = False) -> pd.DataFrame:
    """还没到期的笔试/测评截止时间，按截止时间升序（最紧急的排最前）。

    include_expired=True 时连已经过期的一起返回（首页用它统计"错过了几个"）。
    终止态（已拒绝/已终止）的投递不再需要做题，直接排除。
    """
    conn = get_connection()
    try:
        query = """
            SELECT id, company, position, status, category,
                   assessment_deadline_utc, assessment_deadline_confirmed
            FROM applications
            WHERE assessment_deadline_utc IS NOT NULL
              AND assessment_deadline_utc != ''
              AND status NOT IN ('已拒绝', '已终止')
        """
        params = []
        if not include_expired:
            # 存的都是 "+00:00" 结尾的 UTC ISO 串，格式一致时字符串比较等价于时间比较
            query += " AND assessment_deadline_utc >= ?"
            params.append(datetime.now(timezone.utc).isoformat())
        query += " ORDER BY assessment_deadline_utc ASC;"
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()
