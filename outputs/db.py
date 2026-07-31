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

from config import DB_PATH, STATUS_FLOW, TERMINAL_STATUSES, DEFAULT_CATEGORY, get_valid_next_statuses


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
            """
        )
        conn.commit()
        _ensure_category_column(conn)
        _backfill_status_history(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_category_column(conn: sqlite3.Connection) -> None:
    """给建表前已存在的 applications 表补上 category 列（CREATE TABLE IF NOT EXISTS
    对已存在的表不会补字段，只能用 ALTER TABLE 迁移）。可重复执行。"""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(applications);").fetchall()]
    if "category" not in cols:
        conn.execute(
            f"ALTER TABLE applications ADD COLUMN category TEXT NOT NULL DEFAULT '{DEFAULT_CATEGORY}';"
        )


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
# applications 表操作
# ---------------------------------------------------------------------------

def upsert_application(
    company: str,
    position: str,
    applied_date: str,
    status: str = "已投递",
    job_link: str = None,
    source: str = "manual",
    notes: str = None,
    category: str = DEFAULT_CATEGORY,
) -> tuple[int, bool]:
    """插入新投递记录；若 (company, position, applied_date) 已存在则跳过插入（去重）。

    返回 (application_id, created)，created=False 表示命中去重、未新建。
    """
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


def get_conversion_stats() -> dict:
    """投递 -> 面试 转化率：口径为「已产生至少一条面试记录的投递数 / 总投递数」。

    该口径不依赖 status 字段（因为状态一旦转为终止态会覆盖之前的进度信息），
    只要曾经进入过面试环节（interviews 表中存在记录）即计入分子，更稳健。
    """
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM applications;").fetchone()["c"]
        with_interview = conn.execute(
            "SELECT COUNT(DISTINCT application_id) AS c FROM interviews;"
        ).fetchone()["c"]
        rate = (with_interview / total) if total else 0.0
        return {
            "total_applications": total,
            "applications_with_interview": with_interview,
            "conversion_rate": rate,
        }
    finally:
        conn.close()
