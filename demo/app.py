from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

import psycopg
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from psycopg.rows import dict_row


# Đọc biến môi trường từ file .env cục bộ.
def load_local_env(env_path: str) -> None:
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


# Lấy thời gian UTC dạng ISO.
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Đổi dữ liệu DB sang kiểu JSON an toàn.
def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (date, datetime, uuid.UUID, Decimal)):
        return str(value)
    return value


# Mở kết nối PostgreSQL theo biến môi trường.
def get_db_connection() -> psycopg.Connection:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        conn = psycopg.connect(database_url, row_factory=dict_row)
    else:
        conn = psycopg.connect(
            host=os.getenv("PGHOST", "localhost"),
            port=int(os.getenv("PGPORT", "5432")),
            dbname=os.getenv("PGDATABASE", "postgres"),
            user=os.getenv("PGUSER", "postgres"),
            password=os.getenv("PGPASSWORD", ""),
            row_factory=dict_row,
        )
    with conn.cursor() as cur:
        cur.execute("SET search_path TO public;")
    return conn


# Kiểm tra nhanh sự tồn tại của table trong schema public.
def table_exists(conn: psycopg.Connection, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS rel", (f"public.{table_name}",))
        row = cur.fetchone()
    return bool(row and row.get("rel"))


# Kiểm tra nhanh sự tồn tại của cột trong table.
def column_exists(conn: psycopg.Connection, table_name: str, column_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                  AND column_name = %s
            ) AS ok
            """,
            (table_name, column_name),
        )
        row = cur.fetchone()
    return bool(row and row.get("ok"))


# Ghi audit log bằng kết nối riêng để không bị mất khi transaction chính rollback.
def write_audit_log(
    run_id: str,
    action: str,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    normalized_status = (status or "").strip().upper()
    if normalized_status not in {"SUCCESS", "FAILED", "ROLLED_BACK"}:
        normalized_status = "FAILED"

    try:
        with get_db_connection() as audit_conn:
            with audit_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_logs (
                        run_id,
                        action,
                        status,
                        error_message
                    )
                    VALUES (
                        %s::uuid,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (run_id, action, normalized_status, error_message),
                )
            audit_conn.commit()
    except Exception:
        # Khong lam fail luong chinh neu audit log gap loi.
        pass


# Tải dữ liệu lookup cho dropdown.
def fetch_lookup_data(conn: psycopg.Connection) -> Dict[str, List[Dict[str, Any]]]:
    has_user_status = column_exists(conn, "users", "status")
    has_wallets = table_exists(conn, "wallets")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.user_id,
                   u.username,
                   COALESCE(up.full_name, u.username) AS full_name
            FROM students AS s
            JOIN users AS u
                 ON u.user_id = s.user_id
            LEFT JOIN user_profiles AS up
                 ON up.user_id = s.user_id
            WHERE u.is_deleted = FALSE
            ORDER BY full_name ASC
            """
        )
        students = cur.fetchall()

        cur.execute(
            """
            SELECT gc.course_id,
                   gc.title,
                   gc.visibility_status
            FROM general_courses AS gc
            WHERE gc.is_deleted = FALSE
            ORDER BY gc.title ASC
            """
        )
        courses = cur.fetchall()

        cur.execute(
            """
            SELECT gcc.category_id,
                   gcc.name
            FROM general_course_categories AS gcc
            ORDER BY gcc.name ASC
            """
        )
        course_categories = cur.fetchall()

        user_status_expr = "u.status" if has_user_status else "'active'"
        cur.execute(
            f"""
            SELECT u.user_id,
                   u.username,
                   COALESCE(up.full_name, u.username) AS full_name,
                   r.role_name,
                   {user_status_expr} AS status,
                   u.is_deleted
            FROM users AS u
            JOIN roles AS r
                 ON r.role_id = u.role_id
            LEFT JOIN user_profiles AS up
                   ON up.user_id = u.user_id
            ORDER BY full_name ASC
            """
        )
        users = cur.fetchall()

        cur.execute(
            """
            SELECT l.lesson_id,
                   l.title AS lesson_title,
                   gc.title AS course_title
            FROM general_course_lessons AS l
            JOIN general_course_modules AS m
                 ON m.module_id = l.module_id
            JOIN general_courses AS gc
                 ON gc.course_id = m.course_id
            WHERE gc.is_deleted = FALSE
            ORDER BY gc.title ASC, l.title ASC
            """
        )
        lessons = cur.fetchall()

        wallet_senders: List[Dict[str, Any]] = []
        admin_wallets: List[Dict[str, Any]] = []
        if has_wallets:
            cur.execute(
                f"""
                SELECT w.user_id,
                       u.username,
                       COALESCE(up.full_name, u.username) AS full_name,
                       r.role_name,
                       {user_status_expr} AS status,
                       w.balance
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                LEFT JOIN user_profiles AS up
                       ON up.user_id = u.user_id
                WHERE u.is_deleted = FALSE
                  AND r.role_name <> 'ADMIN'
                ORDER BY r.role_name ASC, full_name ASC
                """
            )
            wallet_senders = cur.fetchall()

            cur.execute(
                f"""
                SELECT w.user_id,
                       u.username,
                       COALESCE(up.full_name, u.username) AS full_name,
                       {user_status_expr} AS status,
                       w.balance
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                LEFT JOIN user_profiles AS up
                       ON up.user_id = u.user_id
                WHERE u.is_deleted = FALSE
                  AND r.role_name = 'ADMIN'
                ORDER BY full_name ASC
                """
            )
            admin_wallets = cur.fetchall()

    return {
        "students": students,
        "courses": courses,
        "course_categories": course_categories,
        "users": users,
        "lessons": lessons,
        "wallet_senders": wallet_senders,
        "admin_wallets": admin_wallets,
    }


# Tải snapshot các bảng nguồn để so sánh trước/sau.
def fetch_source_tables(conn: psycopg.Connection) -> Dict[str, List[Dict[str, Any]]]:
    has_user_status = column_exists(conn, "users", "status")
    has_wallets = table_exists(conn, "wallets")
    has_transaction_logs = table_exists(conn, "transaction_logs")
    has_transaction_action_logs = table_exists(conn, "transaction_action_logs")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ce.enrollment_id,
                   ce.student_id,
                   su.username AS student_username,
                   ce.course_id,
                   gc.title AS course_title,
                   ce.progress,
                   ce.enrolled_at
            FROM course_enrollments AS ce
            JOIN users AS su
                 ON su.user_id = ce.student_id
            JOIN general_courses AS gc
                 ON gc.course_id = ce.course_id
            ORDER BY ce.enrolled_at DESC
            LIMIT 40
            """
        )
        enrollments = cur.fetchall()

        cur.execute(
            """
            SELECT ss.student_id,
                   u.username,
                   ss.current_streak,
                   ss.highest_streak,
                   ss.last_activity_date
            FROM student_streaks AS ss
            JOIN users AS u
                 ON u.user_id = ss.student_id
            ORDER BY ss.current_streak DESC, u.username ASC
            LIMIT 40
            """
        )
        streaks = cur.fetchall()

        cur.execute(
            """
            SELECT n.notification_id,
                   n.user_id,
                   u.username,
                   n.title,
                   n.message,
                   n.is_read,
                   n.created_at
            FROM notification_users AS n
            JOIN users AS u
                 ON u.user_id = n.user_id
            ORDER BY n.created_at DESC
            LIMIT 40
            """
        )
        notifications = cur.fetchall()

        cur.execute(
            """
            SELECT c.comment_id,
                   c.lesson_id,
                   l.title AS lesson_title,
                   c.user_id,
                   u.username,
                   c.content,
                   c.created_at
            FROM comments AS c
            JOIN users AS u
                 ON u.user_id = c.user_id
            LEFT JOIN general_course_lessons AS l
                   ON l.lesson_id = c.lesson_id
            ORDER BY c.created_at DESC
            LIMIT 40
            """
        )
        comments = cur.fetchall()

        user_status_expr = "u.status" if has_user_status else "'active'"
        cur.execute(
            f"""
            SELECT u.user_id,
                   u.username,
                   r.role_name,
                   {user_status_expr} AS status,
                   u.is_deleted,
                   u.updated_at
            FROM users AS u
            JOIN roles AS r
                 ON r.role_id = u.role_id
            ORDER BY u.updated_at DESC, u.username ASC
            LIMIT 40
            """
        )
        users = cur.fetchall()

        cur.execute(
            """
            SELECT gc.course_id,
                   gc.title,
                   gc.visibility_status,
                   gc.is_deleted,
                   gc.updated_at
            FROM general_courses AS gc
            ORDER BY gc.updated_at DESC, gc.title ASC
            LIMIT 40
            """
        )
        courses = cur.fetchall()

        cur.execute(
            """
            SELECT s.user_id,
                   u.username,
                   s.grade_level,
                   s.school_name
            FROM students AS s
            JOIN users AS u
                 ON u.user_id = s.user_id
            ORDER BY u.created_at DESC, u.username ASC
            LIMIT 40
            """
        )
        students = cur.fetchall()

        cur.execute(
            """
            SELECT m.module_id,
                   m.course_id,
                   gc.title AS course_title,
                   m.title AS module_title,
                   m.order_index
            FROM general_course_modules AS m
            JOIN general_courses AS gc
                 ON gc.course_id = m.course_id
            ORDER BY gc.title ASC, m.order_index ASC
            LIMIT 40
            """
        )
        course_modules = cur.fetchall()

        wallets: List[Dict[str, Any]] = []
        if has_wallets:
            cur.execute(
                f"""
                SELECT w.user_id,
                       u.username,
                       COALESCE(up.full_name, u.username) AS full_name,
                       r.role_name,
                       {user_status_expr} AS status,
                       w.balance,
                       w.updated_at
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                LEFT JOIN user_profiles AS up
                       ON up.user_id = u.user_id
                ORDER BY r.role_name ASC, full_name ASC
                LIMIT 60
                """
            )
            wallets = cur.fetchall()

        transaction_logs: List[Dict[str, Any]] = []
        if has_transaction_logs:
            cur.execute(
                """
                SELECT tl.transaction_id,
                       tl.from_wallet_user_id,
                       fu.username AS from_username,
                       tl.to_wallet_user_id,
                       tu.username AS to_username,
                       tl.amount,
                       tl.status,
                       tl.message,
                       tl.created_at
                FROM transaction_logs AS tl
                LEFT JOIN users AS fu
                       ON fu.user_id = tl.from_wallet_user_id
                LEFT JOIN users AS tu
                       ON tu.user_id = tl.to_wallet_user_id
                ORDER BY tl.created_at DESC
                LIMIT 60
                """
            )
            transaction_logs = cur.fetchall()

        transaction_action_logs: List[Dict[str, Any]] = []
        if has_transaction_action_logs:
            cur.execute(
                """
                SELECT tal.action_log_id,
                       tal.transaction_id,
                       tal.action_type,
                       tal.message,
                       tal.created_at
                FROM transaction_action_logs AS tal
                ORDER BY tal.created_at DESC
                LIMIT 80
                """
            )
            transaction_action_logs = cur.fetchall()

    return {
        "course_enrollments": enrollments,
        "student_streaks": streaks,
        "notification_users": notifications,
        "comments": comments,
        "users": users,
        "general_courses": courses,
        "students": students,
        "general_course_modules": course_modules,
        "wallets": wallets,
        "transaction_logs": transaction_logs,
        "transaction_action_logs": transaction_action_logs,
    }


# Đọc dữ liệu từ 3 view báo cáo.
def fetch_reporting_views(conn: psycopg.Connection) -> Dict[str, List[Dict[str, Any]]]:
    queries = {
        "vw_student_progress_report": """
            SELECT *
            FROM vw_student_progress_report
            ORDER BY enrolled_at DESC, progress DESC, student_name ASC
            LIMIT 40
        """,
        "vw_course_analytics": """
            SELECT *
            FROM vw_course_analytics
            ORDER BY total_students DESC, avg_progress DESC, course_title ASC
            LIMIT 40
        """,
        "vw_top_learners_leaderboard": """
            SELECT *
            FROM vw_top_learners_leaderboard
            ORDER BY current_streak DESC, total_achievements DESC, full_name ASC
            LIMIT 40
        """,
    }

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT viewname
            FROM pg_views
            WHERE schemaname = 'public'
              AND viewname = ANY(%s::text[])
            """,
            (list(queries.keys()),),
        )
        existing = {row["viewname"] for row in cur.fetchall()}

        result: Dict[str, List[Dict[str, Any]]] = {}
        for view_name, sql in queries.items():
            if view_name not in existing:
                result[view_name] = []
                continue
            cur.execute(sql)
            result[view_name] = cur.fetchall()

    return result


# Kiểm tra chuỗi UUID đầu vào.
def validate_uuid(raw_value: Any, field_name: str) -> str:
    if raw_value is None:
        raise ValueError(f"Thiếu trường bắt buộc: {field_name}.")
    try:
        return str(uuid.UUID(str(raw_value)))
    except ValueError as exc:
        raise ValueError(f"{field_name} phải là UUID hợp lệ.") from exc


# Chạy query trả về đúng 1 giá trị.
def run_query_value(conn: psycopg.Connection, sql: str, params: tuple[Any, ...]) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return None if row is None else next(iter(row.values()))


# Lấy dấu vân tay dữ liệu để check có mutate hay không.
def fetch_mutation_fingerprint(conn: psycopg.Connection) -> Dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS total FROM course_enrollments")
        enrollments = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) AS total FROM comments")
        comments = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) AS total FROM notification_users")
        notifications = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) AS total FROM student_streaks")
        streaks = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) AS total FROM users WHERE is_deleted = TRUE")
        deleted_users = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) AS total FROM general_courses WHERE is_deleted = TRUE")
        deleted_courses = cur.fetchone()["total"]
    return {
        "course_enrollments": enrollments,
        "comments": comments,
        "notification_users": notifications,
        "student_streaks": streaks,
        "deleted_users": deleted_users,
        "deleted_courses": deleted_courses,
    }


@dataclass
class RunState:
    run_id: str
    action: str
    payload: Dict[str, Any]
    created_at: str = field(default_factory=utc_now_iso)
    status: str = "pending"
    ok: Optional[bool] = None
    finished: bool = False
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    trace: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    event_seq: int = 0
    condition: threading.Condition = field(default_factory=threading.Condition)


class RunRegistry:
    def __init__(self) -> None:
        self._runs: Dict[str, RunState] = {}
        self._lock = threading.Lock()

    def create(self, action: str, payload: Dict[str, Any]) -> RunState:
        run = RunState(run_id=str(uuid.uuid4()), action=action, payload=payload)
        with self._lock:
            self._runs[run.run_id] = run
        return run

    def get(self, run_id: str) -> Optional[RunState]:
        with self._lock:
            return self._runs.get(run_id)

    def append_event(self, run: RunState, event_type: str, data: Dict[str, Any]) -> None:
        with run.condition:
            run.event_seq += 1
            run.events.append(
                {
                    "seq": run.event_seq,
                    "type": event_type,
                    "timestamp": utc_now_iso(),
                    "data": to_jsonable(data),
                }
            )
            run.condition.notify_all()

    def mark_finished(
        self,
        run: RunState,
        ok: bool,
        result: Dict[str, Any],
        error: Optional[str] = None,
    ) -> None:
        with run.condition:
            run.finished = True
            run.ok = ok
            run.status = "success" if ok else "failed"
            run.result = to_jsonable(result)
            run.error = error
            run.condition.notify_all()


registry = RunRegistry()


SUPPORTED_ACTIONS = {
    "view_reports",
    "trigger_updated_at",
    "trigger_init_streak",
    "trigger_publish_guard",
    "transfer_to_admin",
    "search_students",
    "search_courses",
    "enroll",
    "update_progress",
    "progress_comment",
    "soft_delete_user",
    "soft_delete_course",
    "reset",
}

ACTION_ALIASES = {
    "view_report": "view_reports",
    "reports": "view_reports",
    "report_views": "view_reports",
    "trigger_timestamp": "trigger_updated_at",
    "trigger_streak": "trigger_init_streak",
    "trigger_publish": "trigger_publish_guard",
    "transfer_money": "transfer_to_admin",
    "wallet_transfer": "transfer_to_admin",
    "search_student": "search_students",
    "search_course": "search_courses",
    "update_course_progress": "update_progress",
    "progress_comment_transaction": "progress_comment",
    "delete_user": "soft_delete_user",
    "delete_course": "soft_delete_course",
}


# Chuẩn hóa tên action từ client.
def normalize_action(raw_action: Any) -> str:
    if not isinstance(raw_action, str):
        return ""
    action = raw_action.strip()
    if not action:
        return ""
    return ACTION_ALIASES.get(action, action)


RESET_BASELINE_LOCK = threading.Lock()
RESET_BASELINE: Optional[Dict[str, List[Dict[str, Any]]]] = None


# Chụp baseline runtime để phục vụ nút reset.
def capture_reset_baseline(conn: psycopg.Connection) -> Dict[str, List[Dict[str, Any]]]:
    has_user_status = column_exists(conn, "users", "status")
    has_wallets = table_exists(conn, "wallets")
    has_transaction_logs = table_exists(conn, "transaction_logs")
    has_transaction_action_logs = table_exists(conn, "transaction_action_logs")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT enrollment_id, student_id, course_id, progress, enrolled_at
            FROM course_enrollments
            ORDER BY enrollment_id ASC
            """
        )
        enrollments = cur.fetchall()

        cur.execute(
            """
            SELECT comment_id, lesson_id, user_id, content, created_at
            FROM comments
            ORDER BY comment_id ASC
            """
        )
        comments = cur.fetchall()

        cur.execute(
            """
            SELECT notification_id, user_id, title, message, is_read, created_at
            FROM notification_users
            ORDER BY notification_id ASC
            """
        )
        notifications = cur.fetchall()

        cur.execute(
            """
            SELECT student_id, current_streak, highest_streak, last_activity_date
            FROM student_streaks
            ORDER BY student_id ASC
            """
        )
        streaks = cur.fetchall()

        user_status_expr = ", status" if has_user_status else ""
        cur.execute(
            f"""
            SELECT user_id, is_deleted{user_status_expr}
            FROM users
            ORDER BY user_id ASC
            """
        )
        users = cur.fetchall()

        cur.execute(
            """
            SELECT course_id, is_deleted
            FROM general_courses
            ORDER BY course_id ASC
            """
        )
        courses = cur.fetchall()

        wallets: List[Dict[str, Any]] = []
        if has_wallets:
            cur.execute(
                """
                SELECT user_id, balance
                FROM wallets
                ORDER BY user_id ASC
                """
            )
            wallets = cur.fetchall()

        transaction_logs: List[Dict[str, Any]] = []
        if has_transaction_logs:
            cur.execute(
                """
                SELECT transaction_id, from_wallet_user_id, to_wallet_user_id, amount, status, message, created_at
                FROM transaction_logs
                ORDER BY transaction_id ASC
                """
            )
            transaction_logs = cur.fetchall()

        transaction_action_logs: List[Dict[str, Any]] = []
        if has_transaction_action_logs:
            cur.execute(
                """
                SELECT action_log_id, transaction_id, action_type, message, created_at
                FROM transaction_action_logs
                ORDER BY action_log_id ASC
                """
            )
            transaction_action_logs = cur.fetchall()

    return {
        "course_enrollments": enrollments,
        "comments": comments,
        "notification_users": notifications,
        "student_streaks": streaks,
        "users": users,
        "general_courses": courses,
        "wallets": wallets,
        "transaction_logs": transaction_logs,
        "transaction_action_logs": transaction_action_logs,
    }


# Đảm bảo baseline chỉ tạo 1 lần cho mỗi vòng chạy app.
def ensure_reset_baseline(conn: psycopg.Connection) -> Dict[str, List[Dict[str, Any]]]:
    global RESET_BASELINE
    with RESET_BASELINE_LOCK:
        refresh_needed = RESET_BASELINE is None
        if not refresh_needed and RESET_BASELINE is not None:
            if table_exists(conn, "wallets"):
                baseline_wallets = RESET_BASELINE.get("wallets") or []
                if not baseline_wallets:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) AS total FROM wallets")
                        wallet_count = cur.fetchone()["total"]
                    if wallet_count > 0:
                        refresh_needed = True

        if refresh_needed:
            RESET_BASELINE = capture_reset_baseline(conn)
        return deepcopy(RESET_BASELINE)


# Reset dữ liệu demo về baseline runtime đã chụp.
def apply_reset(conn: psycopg.Connection) -> Dict[str, Any]:
    baseline = ensure_reset_baseline(conn)
    counts: Dict[str, Any] = {}
    has_user_status = column_exists(conn, "users", "status")
    has_wallets = table_exists(conn, "wallets")
    has_transaction_logs = table_exists(conn, "transaction_logs")
    has_transaction_action_logs = table_exists(conn, "transaction_action_logs")

    with conn.cursor() as cur:
        if has_transaction_action_logs:
            cur.execute("DELETE FROM transaction_action_logs")
            counts["deleted_transaction_action_logs"] = cur.rowcount

        if has_transaction_logs:
            cur.execute("DELETE FROM transaction_logs")
            counts["deleted_transaction_logs"] = cur.rowcount

        cur.execute("DELETE FROM notification_users")
        counts["deleted_notification_users"] = cur.rowcount

        cur.execute("DELETE FROM comments")
        counts["deleted_comments"] = cur.rowcount

        cur.execute("DELETE FROM course_enrollments")
        counts["deleted_course_enrollments"] = cur.rowcount

        cur.execute("DELETE FROM student_streaks")
        counts["deleted_student_streaks"] = cur.rowcount

        enrollment_rows = [
            (
                row["enrollment_id"],
                row["student_id"],
                row["course_id"],
                row["progress"],
                row["enrolled_at"],
            )
            for row in baseline["course_enrollments"]
        ]
        if enrollment_rows:
            cur.executemany(
                """
                INSERT INTO course_enrollments (
                    enrollment_id, student_id, course_id, progress, enrolled_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                enrollment_rows,
            )
        counts["restored_course_enrollments"] = len(enrollment_rows)

        comment_rows = [
            (
                row["comment_id"],
                row["lesson_id"],
                row["user_id"],
                row["content"],
                row["created_at"],
            )
            for row in baseline["comments"]
        ]
        if comment_rows:
            cur.executemany(
                """
                INSERT INTO comments (
                    comment_id, lesson_id, user_id, content, created_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                comment_rows,
            )
        counts["restored_comments"] = len(comment_rows)

        notification_rows = [
            (
                row["notification_id"],
                row["user_id"],
                row["title"],
                row["message"],
                row["is_read"],
                row["created_at"],
            )
            for row in baseline["notification_users"]
        ]
        if notification_rows:
            cur.executemany(
                """
                INSERT INTO notification_users (
                    notification_id, user_id, title, message, is_read, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                notification_rows,
            )
        counts["restored_notification_users"] = len(notification_rows)

        streak_rows = [
            (
                row["student_id"],
                row["current_streak"],
                row["highest_streak"],
                row["last_activity_date"],
            )
            for row in baseline["student_streaks"]
        ]
        if streak_rows:
            cur.executemany(
                """
                INSERT INTO student_streaks (
                    student_id, current_streak, highest_streak, last_activity_date
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (student_id) DO UPDATE
                SET current_streak = EXCLUDED.current_streak,
                    highest_streak = EXCLUDED.highest_streak,
                    last_activity_date = EXCLUDED.last_activity_date
                """,
                streak_rows,
            )
        counts["restored_student_streaks"] = len(streak_rows)

        user_flags = [(row["is_deleted"], row["user_id"]) for row in baseline["users"]]
        if user_flags:
            if has_user_status:
                user_flags_with_status = [
                    (row["is_deleted"], row.get("status", "active"), row["user_id"])
                    for row in baseline["users"]
                ]
                cur.executemany(
                    """
                    UPDATE users
                    SET is_deleted = %s,
                        status = %s
                    WHERE user_id = %s
                    """,
                    user_flags_with_status,
                )
            else:
                cur.executemany(
                    """
                    UPDATE users
                    SET is_deleted = %s
                    WHERE user_id = %s
                    """,
                    user_flags,
                )
        counts["restored_users_is_deleted"] = len(user_flags)

        course_flags = [(row["is_deleted"], row["course_id"]) for row in baseline["general_courses"]]
        if course_flags:
            cur.executemany(
                """
                UPDATE general_courses
                SET is_deleted = %s
                WHERE course_id = %s
                """,
                course_flags,
            )
        counts["restored_courses_is_deleted"] = len(course_flags)

        if has_wallets:
            wallet_rows = [(row["balance"], row["user_id"]) for row in baseline.get("wallets", [])]
            if wallet_rows:
                cur.executemany(
                    """
                    UPDATE wallets
                    SET balance = %s
                    WHERE user_id = %s
                    """,
                    wallet_rows,
                )
            counts["restored_wallets_balance"] = len(wallet_rows)

        if has_transaction_logs:
            tx_rows = [
                (
                    row["transaction_id"],
                    row["from_wallet_user_id"],
                    row["to_wallet_user_id"],
                    row["amount"],
                    row["status"],
                    row["message"],
                    row["created_at"],
                )
                for row in baseline.get("transaction_logs", [])
            ]
            if tx_rows:
                cur.executemany(
                    """
                    INSERT INTO transaction_logs (
                        transaction_id,
                        from_wallet_user_id,
                        to_wallet_user_id,
                        amount,
                        status,
                        message,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    tx_rows,
                )
            counts["restored_transaction_logs"] = len(tx_rows)

        if has_transaction_action_logs:
            tx_action_rows = [
                (
                    row["action_log_id"],
                    row["transaction_id"],
                    row["action_type"],
                    row["message"],
                    row["created_at"],
                )
                for row in baseline.get("transaction_action_logs", [])
            ]
            if tx_action_rows:
                cur.executemany(
                    """
                    INSERT INTO transaction_action_logs (
                        action_log_id,
                        transaction_id,
                        action_type,
                        message,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    tx_action_rows,
                )
            counts["restored_transaction_action_logs"] = len(tx_action_rows)

    conn.commit()
    return counts

# Chạy một bước pipeline và đẩy sự kiện SSE.
def execute_step(
    run: RunState,
    *,
    step_index: int,
    step_key: str,
    step_name: str,
    sql_label: str,
    sql_lines: Optional[List[str]] = None,
    fn: Callable[[], Dict[str, Any] | None],
) -> Dict[str, Any]:
    start_at = time.perf_counter()
    registry.append_event(
        run,
        "step_started",
        {
            "step_index": step_index,
            "step_key": step_key,
            "step_name": step_name,
            "sql_label": sql_label,
        },
    )

    # Đợi nhẹ để client nhận step_started trước.
    time.sleep(0.15)

    # Đẩy log SQL từng dòng để terminal hiển thị theo thời gian thực.
    for line in (sql_lines or []):
        registry.append_event(run, "sql_log", {"line": line, "step_key": step_key})
        time.sleep(0.06)  # Nghỉ ngắn giữa hai dòng log.

    try:
        details = fn() or {}
        duration_ms = round((time.perf_counter() - start_at) * 1000, 2)
        trace_item = {
            "step_index": step_index,
            "step_key": step_key,
            "step_name": step_name,
            "sql_label": sql_label,
            "status": "success",
            "duration_ms": duration_ms,
            "details": to_jsonable(details),
        }
        run.trace.append(trace_item)
        # Đẩy dữ liệu trả về của bước vừa chạy xong.
        registry.append_event(run, "step_result", {
            "step_key": step_key,
            "step_name": step_name,
            "details": to_jsonable(details),
            "duration_ms": duration_ms,
        })
        registry.append_event(run, "step_finished", trace_item)
        return details
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_at) * 1000, 2)
        trace_item = {
            "step_index": step_index,
            "step_key": step_key,
            "step_name": step_name,
            "sql_label": sql_label,
            "status": "error",
            "duration_ms": duration_ms,
            "error": str(exc),
        }
        run.trace.append(trace_item)
        registry.append_event(run, "step_failed", trace_item)
        raise

# Gửi block SQL mô tả vào terminal SSE.
def emit_sql(run: RunState, step_key: str, lines: list) -> None:
    """Gửi sự kiện sql_log theo từng dòng."""
    import sys
    print(f"[emit_sql] step={step_key} lines={len(lines)}", file=sys.stderr, flush=True)
    time.sleep(0.1)  # Nghỉ ngắn để step_started ra trước.
    for i, line in enumerate(lines):
        registry.append_event(run, "sql_log", {"line": line, "step_key": step_key})
        print(f"[emit_sql]   line {i}: {line[:60]}", file=sys.stderr, flush=True)
        time.sleep(0.06)  # Nghỉ ngắn giữa 2 dòng log.


# Đảm bảo schema cho demo chuyển tiền tồn tại trước khi chạy pipeline.
def ensure_transfer_entities(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'")
        cur.execute("UPDATE users SET status = 'active' WHERE status IS NULL")
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'ck_users_status'
                ) THEN
                    ALTER TABLE users
                        ADD CONSTRAINT ck_users_status
                        CHECK (status IN ('active', 'frozen'));
                END IF;
            END;
            $$;
            """
        )
        cur.execute("ALTER TABLE users ALTER COLUMN status SET NOT NULL")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS wallets (
                user_id UUID PRIMARY KEY,
                balance NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT ck_wallets_balance_non_negative CHECK (balance >= 0),
                CONSTRAINT fk_wallets_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_logs (
                transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                from_wallet_user_id UUID,
                to_wallet_user_id UUID,
                amount NUMERIC(14, 2) NOT NULL,
                status VARCHAR(20) NOT NULL,
                message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT ck_transaction_logs_amount CHECK (amount > 0),
                CONSTRAINT ck_transaction_logs_status CHECK (
                    status IN ('SUCCESS', 'FAILED', 'ROLLED_BACK')
                ),
                CONSTRAINT fk_transaction_logs_from_wallet FOREIGN KEY (from_wallet_user_id) REFERENCES wallets (user_id) ON DELETE RESTRICT,
                CONSTRAINT fk_transaction_logs_to_wallet FOREIGN KEY (to_wallet_user_id) REFERENCES wallets (user_id) ON DELETE RESTRICT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_action_logs (
                action_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                transaction_id UUID,
                action_type VARCHAR(40) NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            ALTER TABLE transaction_action_logs
                DROP CONSTRAINT IF EXISTS fk_tx_action_logs_transaction
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wallets_balance ON wallets (balance)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_transaction_logs_created_at ON transaction_logs (created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_transaction_logs_from_wallet ON transaction_logs (from_wallet_user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_transaction_logs_to_wallet ON transaction_logs (to_wallet_user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_action_logs_created_at ON transaction_action_logs (created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tx_action_logs_tx_id ON transaction_action_logs (transaction_id)")

        cur.execute(
            """
            INSERT INTO wallets (user_id, balance)
            SELECT u.user_id,
                   CASE
                       WHEN r.role_name = 'ADMIN' THEN 5000.00
                       WHEN r.role_name = 'TEACHER' THEN 1000.00
                       ELSE 250.00
                   END AS default_balance
            FROM users AS u
            JOIN roles AS r
                 ON r.role_id = u.role_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM wallets AS w
                WHERE w.user_id = u.user_id
            )
            """
        )

        cur.execute(
            """
            CREATE OR REPLACE FUNCTION fn_transfer_to_admin_wallet(
                p_from_user_id UUID,
                p_amount NUMERIC
            )
            RETURNS TABLE (
                tx_status VARCHAR(20),
                tx_message TEXT,
                tx_id UUID,
                from_balance_after NUMERIC(14, 2),
                admin_balance_after NUMERIC(14, 2)
            )
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_tx_id UUID := gen_random_uuid();
                v_message TEXT;
                v_from_balance NUMERIC(14, 2);
                v_from_status VARCHAR(20);
                v_from_role VARCHAR(50);
                v_admin_user_id UUID;
                v_admin_balance NUMERIC(14, 2);
                v_admin_status VARCHAR(20);
            BEGIN
                INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                VALUES (v_tx_id, 'BEGIN', FORMAT('START transfer from=%s amount=%s', p_from_user_id, p_amount));

                IF p_from_user_id IS NULL THEN
                    v_message := 'Nguon chuyen tien khong duoc NULL.';
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
                END IF;

                IF p_amount IS NULL OR p_amount <= 0 THEN
                    v_message := FORMAT('So tien khong hop le: %s', COALESCE(p_amount::TEXT, 'NULL'));
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
                END IF;

                INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                VALUES (v_tx_id, 'CHECK_SOURCE', FORMAT('Kiem tra vi nguon user_id=%s', p_from_user_id));

                SELECT w.balance,
                       u.status,
                       r.role_name
                INTO v_from_balance,
                     v_from_status,
                     v_from_role
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                WHERE w.user_id = p_from_user_id
                  AND u.is_deleted = FALSE
                FOR UPDATE;

                IF NOT FOUND THEN
                    v_message := FORMAT('Vi nguon %s khong ton tai hoac user da bi xoa mem.', p_from_user_id);
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    INSERT INTO transaction_logs (
                        transaction_id,
                        from_wallet_user_id,
                        to_wallet_user_id,
                        amount,
                        status,
                        message
                    )
                    VALUES (
                        v_tx_id,
                        p_from_user_id,
                        NULL,
                        p_amount,
                        'FAILED',
                        v_message
                    );

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
                END IF;

                IF v_from_role = 'ADMIN' THEN
                    v_message := 'Vi nguon khong duoc la ADMIN. Kich ban chi cho phep hoc vien/giao vien nop ve vi admin.';
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    INSERT INTO transaction_logs (
                        transaction_id,
                        from_wallet_user_id,
                        to_wallet_user_id,
                        amount,
                        status,
                        message
                    )
                    VALUES (
                        v_tx_id,
                        p_from_user_id,
                        p_from_user_id,
                        p_amount,
                        'FAILED',
                        v_message
                    );

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
                END IF;

                IF v_from_status <> 'active' THEN
                    v_message := FORMAT('Tai khoan nguon %s dang %s, khong the chuyen tien.', p_from_user_id, v_from_status);
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    INSERT INTO transaction_logs (
                        transaction_id,
                        from_wallet_user_id,
                        to_wallet_user_id,
                        amount,
                        status,
                        message
                    )
                    VALUES (
                        v_tx_id,
                        p_from_user_id,
                        NULL,
                        p_amount,
                        'FAILED',
                        v_message
                    );

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
                END IF;

                IF v_from_balance < p_amount THEN
                    v_message := FORMAT('So du khong du. Hien co=%s, yeu cau=%s.', v_from_balance, p_amount);
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    INSERT INTO transaction_logs (
                        transaction_id,
                        from_wallet_user_id,
                        to_wallet_user_id,
                        amount,
                        status,
                        message
                    )
                    VALUES (
                        v_tx_id,
                        p_from_user_id,
                        NULL,
                        p_amount,
                        'FAILED',
                        v_message
                    );

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
                END IF;

                INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                VALUES (v_tx_id, 'CHECK_DESTINATION', 'Tim vi ADMIN de nhan tien');

                SELECT w.user_id,
                       w.balance,
                       u.status
                INTO v_admin_user_id,
                     v_admin_balance,
                     v_admin_status
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                WHERE r.role_name = 'ADMIN'
                  AND u.is_deleted = FALSE
                ORDER BY u.created_at ASC
                LIMIT 1
                FOR UPDATE;

                IF v_admin_user_id IS NULL THEN
                    v_message := 'Khong tim thay vi ADMIN de nhan tien.';
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    INSERT INTO transaction_logs (
                        transaction_id,
                        from_wallet_user_id,
                        to_wallet_user_id,
                        amount,
                        status,
                        message
                    )
                    VALUES (
                        v_tx_id,
                        p_from_user_id,
                        NULL,
                        p_amount,
                        'FAILED',
                        v_message
                    );

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
                END IF;

                IF v_admin_status <> 'active' THEN
                    v_message := FORMAT('Tai khoan ADMIN (%s) dang %s, khong the nhan tien.', v_admin_user_id, v_admin_status);
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    INSERT INTO transaction_logs (
                        transaction_id,
                        from_wallet_user_id,
                        to_wallet_user_id,
                        amount,
                        status,
                        message
                    )
                    VALUES (
                        v_tx_id,
                        p_from_user_id,
                        v_admin_user_id,
                        p_amount,
                        'FAILED',
                        v_message
                    );

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
                END IF;

                UPDATE wallets
                SET balance = balance - p_amount
                WHERE user_id = p_from_user_id;

                UPDATE wallets
                SET balance = balance + p_amount
                WHERE user_id = v_admin_user_id;

                SELECT balance
                INTO v_from_balance
                FROM wallets
                WHERE user_id = p_from_user_id;

                SELECT balance
                INTO v_admin_balance
                FROM wallets
                WHERE user_id = v_admin_user_id;

                v_message := FORMAT(
                    'Chuyen tien thanh cong: %s tu %s sang vi ADMIN %s.',
                    p_amount,
                    p_from_user_id,
                    v_admin_user_id
                );

                INSERT INTO transaction_logs (
                    transaction_id,
                    from_wallet_user_id,
                    to_wallet_user_id,
                    amount,
                    status,
                    message
                )
                VALUES (
                    v_tx_id,
                    p_from_user_id,
                    v_admin_user_id,
                    p_amount,
                    'SUCCESS',
                    v_message
                );

                INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                VALUES (v_tx_id, 'COMMIT', v_message);

                RETURN QUERY
                SELECT 'SUCCESS'::VARCHAR, v_message, v_tx_id, v_from_balance, v_admin_balance;
                RETURN;
            EXCEPTION
                WHEN OTHERS THEN
                    v_message := FORMAT('LOI HE THONG: %s', SQLERRM);
                    INSERT INTO transaction_action_logs (transaction_id, action_type, message)
                    VALUES (v_tx_id, 'ROLLBACK', v_message);

                    INSERT INTO transaction_logs (
                        transaction_id,
                        from_wallet_user_id,
                        to_wallet_user_id,
                        amount,
                        status,
                        message
                    )
                    VALUES (
                        v_tx_id,
                        p_from_user_id,
                        v_admin_user_id,
                        GREATEST(COALESCE(p_amount, 0), 0.01),
                        'ROLLED_BACK',
                        v_message
                    );

                    RETURN QUERY
                    SELECT 'ERROR'::VARCHAR, v_message, v_tx_id, NULL::NUMERIC(14, 2), NULL::NUMERIC(14, 2);
                    RETURN;
            END;
            $$;
            """
        )

    conn.commit()


# Kịch bản: enroll và kiểm tra trigger streak.
def run_enroll_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        student_id = validate_uuid(payload.get("student_id"), "student_id")
        course_id = validate_uuid(payload.get("course_id"), "course_id")

        emit_sql(run, "validate_input", [
            f"-- [B1] Kiem tra student_id ton tai:",
            f"SELECT EXISTS(SELECT 1 FROM students WHERE user_id = '{student_id}'::uuid) AS student_exists;",
            f"",
            f"-- [B2] Kiem tra course_id ton tai va chua bi xoa mem:",
            f"SELECT EXISTS(SELECT 1 FROM general_courses",
            f"  WHERE course_id = '{course_id}'::uuid AND is_deleted = FALSE) AS course_exists;",
            f"",
            f"-- [B3] Doc streak hien tai truoc khi enroll:",
            f"SELECT current_streak, highest_streak, last_activity_date",
            f"  FROM student_streaks WHERE student_id = '{student_id}'::uuid;",
        ])

        has_student = run_query_value(
            conn,
            "SELECT EXISTS(SELECT 1 FROM students WHERE user_id = %s::uuid)",
            (student_id,),
        )
        if not has_student:
            raise ValueError("student_id không tồn tại trong bảng students.")

        has_course = run_query_value(
            conn,
            "SELECT EXISTS(SELECT 1 FROM general_courses WHERE course_id = %s::uuid AND is_deleted = FALSE)",
            (course_id,),
        )
        if not has_course:
            raise ValueError("course_id không tồn tại hoặc đã bị xóa mềm.")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_streak, highest_streak, last_activity_date
                FROM student_streaks
                WHERE student_id = %s::uuid
                """,
                (student_id,),
            )
            streak_before = cur.fetchone()

        conn.commit()
        context["validated"] = {"student_id": student_id, "course_id": course_id}
        context["metrics_before"] = {"streak_before": streak_before}
        return {"student_id": student_id, "course_id": course_id, "streak_before": streak_before}

    def step_execute() -> Dict[str, Any]:
        student_id = context["validated"]["student_id"]
        course_id = context["validated"]["course_id"]
        emit_sql(run, "execute_procedure", [
            f"CALL sp_enroll_student(",
            f"  p_student_id => '{student_id}'::uuid,",
            f"  p_course_id  => '{course_id}'::uuid",
            f");",
            f"-- Noi dung ben trong sp_enroll_student():",
            f"--   INSERT INTO course_enrollments (student_id, course_id, progress)",
            f"--   VALUES ('{student_id}', '{course_id}', 0.00)",
            f"--   ON CONFLICT (student_id, course_id) DO NOTHING;",
        ])
        with conn.cursor() as cur:
            cur.execute("CALL sp_enroll_student(%s::uuid, %s::uuid)", (student_id, course_id))
            cur.execute(
                """
                SELECT enrollment_id, progress, enrolled_at
                FROM course_enrollments
                WHERE student_id = %s::uuid
                  AND course_id = %s::uuid
                ORDER BY enrolled_at DESC
                LIMIT 1
                """,
                (student_id, course_id),
            )
            enrollment = cur.fetchone()
        conn.commit()
        context["action_data"] = {"enrollment": enrollment}
        return {"enrollment": enrollment}

    def step_trigger_check() -> Dict[str, Any]:
        student_id = context["validated"]["student_id"]
        emit_sql(run, "check_trigger_side_effects", [
            f"-- TRIGGER FIRED: fn_touch_student_activity()",
            f"-- AFTER INSERT ON course_enrollments FOR EACH ROW",
            f"-- -> Tu dong UPDATE student_streaks: streak += 1",
            f"",
            f"-- Xac nhan ket qua sau trigger:",
            f"SELECT current_streak, highest_streak, last_activity_date",
            f"  FROM student_streaks WHERE student_id = '{student_id}'::uuid;",
        ])
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_streak, highest_streak, last_activity_date
                FROM student_streaks
                WHERE student_id = %s::uuid
                """,
                (student_id,),
            )
            streak_after = cur.fetchone()
        conn.commit()
        return {
            "streak_before": context["metrics_before"]["streak_before"],
            "streak_after": streak_after,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "Enroll thành công. Pipeline đã hoàn tất."
        return {"status": "done"}


    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="SELECT EXISTS tren students va general_courses",
        fn=step_validate,
    )
    sid = context["validated"]["student_id"]
    cid = context["validated"]["course_id"]
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute procedure sp_enroll_student",
        sql_label="CALL sp_enroll_student(student_id, course_id)",
        sql_lines=[
            f"CALL sp_enroll_student(",
            f"  p_student_id => '{sid}'::uuid,",
            f"  p_course_id  => '{cid}'::uuid",
            f");",
            f"-- Noi dung ben trong sp_enroll_student():",
            f"--   INSERT INTO course_enrollments (student_id, course_id, progress)",
            f"--   VALUES ('{sid}', '{cid}', 0.00)",
            f"--   ON CONFLICT (student_id, course_id) DO NOTHING;",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="TRIGGER: trg_touch_student_activity_on_enroll",
        sql_label="SELECT student_streaks de xac nhan trigger da chay",
        sql_lines=[
            f"-- TRIGGER FIRED: fn_touch_student_activity()",
            f"-- AFTER INSERT ON course_enrollments FOR EACH ROW",
            f"-- -> Tu dong UPDATE student_streaks:",
            f"--    SET current_streak += 1, last_activity_date = TODAY",
            f"",
            f"-- Xac nhan ket qua sau trigger:",
            f"SELECT current_streak, highest_streak, last_activity_date",
            f"  FROM student_streaks WHERE student_id = '{sid}'::uuid;",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot tu course_enrollments, student_streaks",
        sql_lines=[
            f"SELECT ce.enrollment_id, su.username, gc.title, ce.progress, ce.enrolled_at",
            f"  FROM course_enrollments ce",
            f"  JOIN users su ON su.user_id = ce.student_id",
            f"  JOIN general_courses gc ON gc.course_id = ce.course_id",
            f" ORDER BY ce.enrolled_at DESC LIMIT 40;",
        ],
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="SELECT tu 3 reporting views moi",
        sql_lines=[
            f"SELECT student_name, course_title, progress, learning_status",
            f"  FROM vw_student_progress_report ORDER BY enrolled_at DESC LIMIT 10;",
            f"SELECT course_title, total_students, avg_progress, avg_rating",
            f"  FROM vw_course_analytics ORDER BY total_students DESC LIMIT 10;",
            f"SELECT full_name, current_streak, total_achievements",
            f"  FROM vw_top_learners_leaderboard LIMIT 10;",
        ],
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Kịch bản: transaction update progress + comment.
def run_progress_comment_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        student_id = validate_uuid(payload.get("student_id"), "student_id")
        course_id = validate_uuid(payload.get("course_id"), "course_id")
        lesson_id = validate_uuid(payload.get("lesson_id"), "lesson_id")
        progress_raw = payload.get("progress")
        comment_text = str(payload.get("comment_text", "")).strip()

        if progress_raw is None:
            raise ValueError("Thiếu trường progress.")
        try:
            progress = Decimal(str(progress_raw))
        except Exception as exc:
            raise ValueError("progress phải là số hợp lệ.") from exc
        if not comment_text:
            raise ValueError("comment_text không được để trống.")

        has_student = run_query_value(
            conn,
            "SELECT EXISTS(SELECT 1 FROM students WHERE user_id = %s::uuid)",
            (student_id,),
        )
        if not has_student:
            raise ValueError("student_id không tồn tại trong bảng students.")

        has_course = run_query_value(
            conn,
            "SELECT EXISTS(SELECT 1 FROM general_courses WHERE course_id = %s::uuid AND is_deleted = FALSE)",
            (course_id,),
        )
        if not has_course:
            raise ValueError("course_id không tồn tại hoặc đã bị xóa mềm.")

        has_lesson = run_query_value(
            conn,
            "SELECT EXISTS(SELECT 1 FROM general_course_lessons WHERE lesson_id = %s::uuid)",
            (lesson_id,),
        )
        if not has_lesson:
            raise ValueError("lesson_id không tồn tại trong bảng general_course_lessons.")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM notification_users WHERE user_id = %s::uuid",
                (student_id,),
            )
            notification_count_before = cur.fetchone()["total"]
        conn.commit()

        context["validated"] = {
            "student_id": student_id,
            "course_id": course_id,
            "lesson_id": lesson_id,
            "progress": progress,
            "comment_text": comment_text,
        }
        context["metrics_before"] = {"notification_count_before": notification_count_before}
        return {
            "student_id": student_id,
            "course_id": course_id,
            "lesson_id": lesson_id,
            "progress": str(progress),
            "notification_count_before": notification_count_before,
        }

    def step_execute() -> Dict[str, Any]:
        data = context["validated"]
        emit_sql(run, "execute_procedure", [
            f"BEGIN; -- === BAT DAU TRANSACTION NGUYEN TU ===",
            f"",
            f"CALL sp_update_course_progress(",
            f"  p_student_id => '{data['student_id']}'::uuid,",
            f"  p_course_id  => '{data['course_id']}'::uuid,",
            f"  p_progress   => {data['progress']}::numeric",
            f");",
            f"-- -> UPDATE course_enrollments SET progress = {data['progress']}",
            f"",
            f"INSERT INTO comments (lesson_id, user_id, content)",
            f"VALUES ('{data['lesson_id']}'::uuid, '{data['student_id']}'::uuid, '{str(data['comment_text'])[:40]}...') RETURNING comment_id;",
            f"",
            f"COMMIT; -- === KET THUC TRANSACTION THANH CONG ===",
        ])
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "CALL sp_update_course_progress(%s::uuid, %s::uuid, %s::numeric)",
                    (data["student_id"], data["course_id"], data["progress"]),
                )
                cur.execute(
                    """
                    INSERT INTO comments (lesson_id, user_id, content)
                    VALUES (%s::uuid, %s::uuid, %s)
                    RETURNING comment_id, created_at
                    """,
                    (data["lesson_id"], data["student_id"], data["comment_text"]),
                )
                inserted_comment = cur.fetchone()
        context["action_data"] = {"inserted_comment": inserted_comment}
        return {"inserted_comment": inserted_comment, "progress": str(data["progress"])}

    def step_trigger_check() -> Dict[str, Any]:
        student_id = context["validated"]["student_id"]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM notification_users WHERE user_id = %s::uuid",
                (student_id,),
            )
            notification_count_after = cur.fetchone()["total"]
            cur.execute(
                """
                SELECT notification_id, title, message, created_at
                FROM notification_users
                WHERE user_id = %s::uuid
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (student_id,),
            )
            latest_notification = cur.fetchone()
        conn.commit()
        notification_count_before = context["metrics_before"]["notification_count_before"]
        return {
            "notification_count_before": notification_count_before,
            "notification_count_after": notification_count_after,
            "delta": notification_count_after - notification_count_before,
            "latest_notification": latest_notification,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "Transaction cập nhật progress + comment đã chạy xong."
        return {"status": "done"}


    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="SELECT EXISTS + parse payload",
        fn=step_validate,
    )
    sid = context["validated"]["student_id"]
    cid = context["validated"]["course_id"]
    lid = context["validated"]["lesson_id"]
    prog = str(context["validated"]["progress"])
    ctxt = context["validated"]["comment_text"]
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Transaction E2E: BEGIN -> CALL -> INSERT -> COMMIT",
        sql_label="BEGIN; CALL sp_update_course_progress; INSERT comments; COMMIT;",
        sql_lines=[
            f"BEGIN; -- === BAT DAU TRANSACTION NGUYEN TU ===",
            f"",
            f"CALL sp_update_course_progress(",
            f"  p_student_id => '{sid}'::uuid,",
            f"  p_course_id  => '{cid}'::uuid,",
            f"  p_progress   => {prog}::numeric",
            f");",
            f"-- -> UPDATE course_enrollments SET progress = {prog}",
            f"",
            f"INSERT INTO comments (lesson_id, user_id, content)",
            f"VALUES ('{lid}'::uuid, '{sid}'::uuid, '{ctxt[:50]}...') RETURNING comment_id, created_at;",
            f"",
            f"COMMIT; -- === KET THUC TRANSACTION THANH CONG ===",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="TRIGGER: trg_notify_course_completion (neu progress=100%)",
        sql_label="SELECT notification_users delta",
        sql_lines=[
            f"-- TRIGGER kich hoat khi progress >= 100:",
            f"-- fn_notify_course_completion() -> INSERT notification_users",
            f"SELECT COUNT(*) AS notif_after FROM notification_users WHERE user_id = '{sid}'::uuid;",
            f"SELECT title, message FROM notification_users WHERE user_id = '{sid}'::uuid ORDER BY created_at DESC LIMIT 1;",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables (enrollments + comments)",
        sql_label="SELECT enrollments, comments bi anh huong",
        sql_lines=[
            f"SELECT * FROM course_enrollments WHERE student_id = '{sid}'::uuid ORDER BY enrolled_at DESC;",
            f"SELECT content, created_at FROM comments WHERE user_id = '{sid}'::uuid ORDER BY created_at DESC LIMIT 5;",
        ],
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="SELECT tu 3 reporting views moi",
        sql_lines=[
            f"SELECT student_name, course_title, progress, learning_status",
            f"  FROM vw_student_progress_report ORDER BY enrolled_at DESC LIMIT 10;",
            f"SELECT course_title, total_students, avg_progress, avg_rating",
            f"  FROM vw_course_analytics ORDER BY total_students DESC LIMIT 10;",
            f"SELECT full_name, current_streak, total_achievements",
            f"  FROM vw_top_learners_leaderboard LIMIT 10;",
        ],
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Kịch bản: đọc 3 reporting view.
def run_view_reports_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    def step_validate() -> Dict[str, Any]:
        context["fingerprint_before"] = fetch_mutation_fingerprint(conn)
        conn.commit()
        return {"payload": run.payload or {}, "fingerprint_before": context["fingerprint_before"]}

    def step_execute() -> Dict[str, Any]:
        views_data = fetch_reporting_views(conn)
        context["action_data"] = {"report_views": views_data}
        return {name: len(rows) for name, rows in views_data.items()}

    def step_trigger_check() -> Dict[str, Any]:
        fingerprint_after = fetch_mutation_fingerprint(conn)
        conn.commit()
        return {
            "fingerprint_before": context["fingerprint_before"],
            "fingerprint_after": fingerprint_after,
            "mutated": context["fingerprint_before"] != fingerprint_after,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "View reports da duoc tai va khong lam thay doi du lieu goc."
        return {"status": "done"}

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="Validate payload cho view reports",
        fn=step_validate,
    )
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute reporting query",
        sql_label="SELECT * FROM 3 reporting views",
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="Mutation check",
        sql_label="Compare table fingerprint before/after",
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Kịch bản: gọi hàm tìm học viên.
def run_trigger_updated_at_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        user_id = validate_uuid(payload.get("user_id"), "user_id")
        new_username = str(payload.get("new_username") or "").strip()
        if not new_username:
            new_username = f"trigger_user_{int(time.time())}"

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, username, updated_at
                FROM users
                WHERE user_id = %s::uuid
                """,
                (user_id,),
            )
            user_before = cur.fetchone()
        if not user_before:
            raise ValueError("user_id khong ton tai.")

        conn.commit()
        context["validated"] = {"user_id": user_id, "new_username": new_username}
        context["metrics_before"] = {"user_before": user_before}
        return {"user_before": user_before, "new_username": new_username}

    def step_execute() -> Dict[str, Any]:
        data = context["validated"]
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION fn_update_timestamp()
                RETURNS TRIGGER
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    NEW.updated_at = CURRENT_TIMESTAMP;
                    RETURN NEW;
                END;
                $$;
                """
            )
            cur.execute("DROP TRIGGER IF EXISTS trg_users_update_timestamp ON users")
            cur.execute("DROP TRIGGER IF EXISTS trg_auto_update_users_timestamp ON users")
            cur.execute(
                """
                CREATE TRIGGER trg_auto_update_users_timestamp
                BEFORE UPDATE ON users
                FOR EACH ROW
                EXECUTE FUNCTION fn_update_timestamp()
                """
            )
            cur.execute(
                """
                UPDATE users
                SET username = %s
                WHERE user_id = %s::uuid
                RETURNING user_id, username, updated_at
                """,
                (data["new_username"], data["user_id"]),
            )
            user_after = cur.fetchone()
        if not user_after:
            raise ValueError("Khong update duoc user.")
        conn.commit()
        context["action_data"] = {"user_after_update_timestamp": user_after}
        return {"user_after_update_timestamp": user_after}

    def step_trigger_check() -> Dict[str, Any]:
        user_before = context["metrics_before"]["user_before"]
        user_after = context["action_data"]["user_after_update_timestamp"]
        return {
            "updated_at_before": user_before["updated_at"],
            "updated_at_after": user_after["updated_at"],
            "updated_at_changed": user_before["updated_at"] != user_after["updated_at"],
            "username_before": user_before["username"],
            "username_after": user_after["username"],
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "SESSION 1 done: trigger timestamp da cap nhat updated_at."
        return {"status": "done"}

    v = context.get("validated") or {}
    uid = v.get("user_id", "?")
    new_name = v.get("new_username", "?")

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="SESSION 1 - Validate input",
        sql_label="Validate user_id + username moi",
        sql_lines=[
            "-- SESSION 1: AUTOMATION TRIGGER (UPDATED_AT)",
            f"SELECT user_id, username, updated_at FROM users WHERE user_id = '{uid}'::uuid;",
            f"-- new_username = '{new_name}'",
        ],
        fn=step_validate,
    )
    uid = context["validated"]["user_id"]
    new_name = context["validated"]["new_username"]
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Update users.username",
        sql_label="UPDATE users ... RETURNING",
        sql_lines=[
            "CREATE OR REPLACE FUNCTION fn_update_timestamp() RETURNS TRIGGER ...;",
            "DROP TRIGGER IF EXISTS trg_users_update_timestamp ON users;",
            "CREATE TRIGGER trg_auto_update_users_timestamp BEFORE UPDATE ON users ...;",
            f"UPDATE users SET username = '{new_name}' WHERE user_id = '{uid}'::uuid;",
            f"SELECT user_id, username, updated_at FROM users WHERE user_id = '{uid}'::uuid;",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="Trigger check: trg_auto_update_users_timestamp",
        sql_label="Compare updated_at before/after",
        sql_lines=[
            "-- Trigger ngam fn_update_timestamp da chay BEFORE UPDATE",
            f"SELECT user_id, username, updated_at FROM users WHERE user_id = '{uid}'::uuid;",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


def run_trigger_init_streak_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        raw_user_id = payload.get("user_id") or "30000000-0000-0000-0000-000000000599"
        user_id = validate_uuid(raw_user_id, "user_id")
        username = str(payload.get("username") or "demo_new_user_01").strip()
        email = str(payload.get("email") or "newuser@local_01").strip()
        grade_level = str(payload.get("grade_level") or "Grade 10").strip()
        school_name = str(payload.get("school_name") or "Demo High School").strip()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT role_id FROM roles WHERE role_name = 'STUDENT' ORDER BY role_id ASC LIMIT 1"
            )
            role_row = cur.fetchone()
            if not role_row:
                raise ValueError("Khong tim thay role STUDENT.")
            role_id = role_row["role_id"]

            cur.execute(
                "SELECT COUNT(*) AS total FROM users WHERE user_id = %s::uuid",
                (user_id,),
            )
            user_exists = cur.fetchone()["total"] > 0
            cur.execute(
                "SELECT COUNT(*) AS total FROM students WHERE user_id = %s::uuid",
                (user_id,),
            )
            student_exists = cur.fetchone()["total"] > 0
            cur.execute(
                "SELECT COUNT(*) AS total FROM student_streaks WHERE student_id = %s::uuid",
                (user_id,),
            )
            streak_exists = cur.fetchone()["total"] > 0

        conn.commit()
        context["validated"] = {
            "user_id": user_id,
            "username": username,
            "email": email,
            "role_id": role_id,
            "grade_level": grade_level,
            "school_name": school_name,
        }
        context["metrics_before"] = {
            "user_exists": user_exists,
            "student_exists": student_exists,
            "streak_exists": streak_exists,
        }
        return {
            "user_id": user_id,
            "username": username,
            "email": email,
            "user_exists": user_exists,
            "student_exists": student_exists,
            "streak_exists": streak_exists,
        }

    def step_execute() -> Dict[str, Any]:
        data = context["validated"]
        has_wallets = table_exists(conn, "wallets")
        has_transaction_logs = table_exists(conn, "transaction_logs")
        has_transaction_action_logs = table_exists(conn, "transaction_action_logs")

        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION fn_init_student_streak()
                RETURNS TRIGGER
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    INSERT INTO student_streaks (
                        student_id, current_streak, highest_streak, last_activity_date
                    )
                    VALUES (NEW.user_id, 0, 0, NULL)
                    ON CONFLICT (student_id) DO NOTHING;
                    RETURN NEW;
                END;
                $$;
                """
            )
            cur.execute("DROP TRIGGER IF EXISTS trg_after_insert_student ON students")
            cur.execute(
                """
                CREATE TRIGGER trg_after_insert_student
                AFTER INSERT ON students
                FOR EACH ROW
                EXECUTE FUNCTION fn_init_student_streak()
                """
            )

            if has_transaction_action_logs and has_transaction_logs:
                cur.execute(
                    """
                    DELETE FROM transaction_action_logs
                    WHERE transaction_id IN (
                        SELECT transaction_id
                        FROM transaction_logs
                        WHERE from_wallet_user_id = %s::uuid
                           OR to_wallet_user_id = %s::uuid
                    )
                    """,
                    (data["user_id"], data["user_id"]),
                )

            if has_transaction_logs:
                cur.execute(
                    """
                    DELETE FROM transaction_logs
                    WHERE from_wallet_user_id = %s::uuid
                       OR to_wallet_user_id = %s::uuid
                    """,
                    (data["user_id"], data["user_id"]),
                )

            if has_wallets:
                cur.execute("DELETE FROM wallets WHERE user_id = %s::uuid", (data["user_id"],))

            cur.execute("DELETE FROM student_streaks WHERE student_id = %s::uuid", (data["user_id"],))
            cur.execute("DELETE FROM students WHERE user_id = %s::uuid", (data["user_id"],))
            cur.execute("DELETE FROM users WHERE user_id = %s::uuid", (data["user_id"],))

            cur.execute(
                """
                INSERT INTO users (user_id, username, password_hash, email, role_id)
                VALUES (%s::uuid, %s, %s, %s, %s)
                """,
                (data["user_id"], data["username"], "hash123", data["email"], data["role_id"]),
            )
            cur.execute(
                "SELECT COUNT(*) AS total FROM student_streaks WHERE student_id = %s::uuid",
                (data["user_id"],),
            )
            streak_before_insert_student = cur.fetchone()["total"]

            cur.execute(
                """
                INSERT INTO students (user_id, grade_level, school_name)
                VALUES (%s::uuid, %s, %s)
                """,
                (data["user_id"], data["grade_level"], data["school_name"]),
            )
            cur.execute(
                """
                SELECT student_id, current_streak, highest_streak, last_activity_date
                FROM student_streaks
                WHERE student_id = %s::uuid
                """,
                (data["user_id"],),
            )
            streak_after = cur.fetchone()

        conn.commit()
        context["action_data"] = {
            "demo_student_created": {
                "user_id": data["user_id"],
                "username": data["username"],
                "email": data["email"],
                "grade_level": data["grade_level"],
                "school_name": data["school_name"],
            },
            "streak_before_insert_student": streak_before_insert_student,
            "streak_after_insert_student": streak_after,
        }
        return {
            "streak_before_insert_student": streak_before_insert_student,
            "streak_after_insert_student": streak_after,
        }

    def step_trigger_check() -> Dict[str, Any]:
        data = context["validated"]
        streak_after = context["action_data"]["streak_after_insert_student"]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT action, created_at
                FROM log
                WHERE action ILIKE %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (f"%trg_after_insert_student%{data['user_id']}%",),
            )
            trigger_log = cur.fetchone()
        conn.commit()
        return {
            "streak_created_by_trigger": bool(streak_after),
            "streak_after_insert_student": streak_after,
            "trigger_log": trigger_log,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "SESSION 2 done: trigger init streak da tao student_streaks tu dong."
        return {"status": "done"}

    v = context.get("validated") or {}
    uid = v.get("user_id", "?")
    uname = v.get("username", "?")
    email = v.get("email", "?")

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="SESSION 2 - Validate input",
        sql_label="Prepare demo user/student",
        sql_lines=[
            "-- SESSION 2: DATA INTEGRITY TRIGGER (INIT STREAK)",
            f"-- demo_user_id = '{uid}'",
            f"-- username = '{uname}', email = '{email}'",
            "SELECT role_id FROM roles WHERE role_name = 'STUDENT' LIMIT 1;",
        ],
        fn=step_validate,
    )
    uid = context["validated"]["user_id"]
    uname = context["validated"]["username"]
    email = context["validated"]["email"]
    grade = context["validated"]["grade_level"]
    school = context["validated"]["school_name"]
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Insert users + students",
        sql_label="INSERT users -> INSERT students",
        sql_lines=[
            "CREATE OR REPLACE FUNCTION fn_init_student_streak() RETURNS TRIGGER ...;",
            "DROP TRIGGER IF EXISTS trg_after_insert_student ON students;",
            "CREATE TRIGGER trg_after_insert_student AFTER INSERT ON students ...;",
            f"DELETE FROM student_streaks WHERE student_id = '{uid}'::uuid;",
            f"DELETE FROM students WHERE user_id = '{uid}'::uuid;",
            f"DELETE FROM users WHERE user_id = '{uid}'::uuid;",
            f"INSERT INTO users (user_id, username, password_hash, email, role_id)",
            f"VALUES ('{uid}'::uuid, '{uname}', 'hash123', '{email}', (SELECT role_id FROM roles WHERE role_name = 'STUDENT'));",
            f"INSERT INTO students (user_id, grade_level, school_name)",
            f"VALUES ('{uid}'::uuid, '{grade}', '{school}');",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="Trigger check: trg_after_insert_student",
        sql_label="Verify student_streaks auto-created",
        sql_lines=[
            "-- Trigger ngam fn_init_student_streak da chay AFTER INSERT ON students",
            f"SELECT student_id, current_streak, highest_streak, last_activity_date",
            f"  FROM student_streaks WHERE student_id = '{uid}'::uuid;",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


def run_trigger_publish_guard_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        course_id = validate_uuid(payload.get("course_id") or "40000000-0000-0000-0000-000000000678", "course_id")
        module_id = validate_uuid(payload.get("module_id") or "41000000-0000-0000-0000-000000000999", "module_id")
        title = str(payload.get("course_title") or "Khoa hoc Test Trigger").strip()

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.user_id
                FROM teachers AS t
                JOIN users AS u
                     ON u.user_id = t.user_id
                WHERE u.is_deleted = FALSE
                ORDER BY u.created_at ASC
                LIMIT 1
                """
            )
            teacher_row = cur.fetchone()
            if not teacher_row:
                raise ValueError("Khong co teacher de demo publish trigger.")
            teacher_id = teacher_row["user_id"]

            cur.execute(
                """
                SELECT category_id
                FROM general_course_categories
                ORDER BY category_id ASC
                LIMIT 1
                """
            )
            category_row = cur.fetchone()
            if not category_row:
                raise ValueError("Khong co category de tao khoa hoc demo.")
            category_id = category_row["category_id"]

            cur.execute(
                """
                SELECT course_id, title, visibility_status, updated_at
                FROM general_courses
                WHERE course_id = %s::uuid
                """,
                (course_id,),
            )
            course_before = cur.fetchone()

        conn.commit()
        context["validated"] = {
            "course_id": course_id,
            "module_id": module_id,
            "course_title": title,
            "teacher_id": teacher_id,
            "category_id": category_id,
        }
        context["metrics_before"] = {"course_before": course_before}
        return {
            "course_id": course_id,
            "module_id": module_id,
            "teacher_id": teacher_id,
            "category_id": category_id,
            "course_before": course_before,
        }

    def step_execute() -> Dict[str, Any]:
        data = context["validated"]
        blocked_error: Optional[str] = None
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION fn_validate_course_publish()
                RETURNS TRIGGER
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    module_count INTEGER;
                BEGIN
                    IF NEW.visibility_status = 'PUBLISHED'
                       AND COALESCE(OLD.visibility_status, '') <> 'PUBLISHED' THEN
                        SELECT COUNT(*)
                        INTO module_count
                        FROM general_course_modules
                        WHERE course_id = NEW.course_id;

                        IF module_count = 0 THEN
                            RAISE EXCEPTION
                                'LOI NGHIEP VU: Khong the publish khoa hoc "%" vi chua co module nao.',
                                NEW.title;
                        END IF;
                    END IF;
                    RETURN NEW;
                END;
                $$;
                """
            )
            cur.execute("DROP TRIGGER IF EXISTS trg_before_publish_course ON general_courses")
            cur.execute(
                """
                CREATE TRIGGER trg_before_publish_course
                BEFORE UPDATE OF visibility_status ON general_courses
                FOR EACH ROW
                EXECUTE FUNCTION fn_validate_course_publish()
                """
            )

            cur.execute(
                """
                INSERT INTO general_courses (
                    course_id, teacher_id, category_id, title, visibility_status, is_deleted
                )
                VALUES (%s::uuid, %s::uuid, %s, %s, 'DRAFT', FALSE)
                ON CONFLICT (course_id) DO UPDATE
                SET teacher_id = EXCLUDED.teacher_id,
                    category_id = EXCLUDED.category_id,
                    title = EXCLUDED.title,
                    visibility_status = 'DRAFT',
                    is_deleted = FALSE
                """,
                (
                    data["course_id"],
                    data["teacher_id"],
                    data["category_id"],
                    data["course_title"],
                ),
            )

            cur.execute(
                "DELETE FROM general_course_modules WHERE course_id = %s::uuid",
                (data["course_id"],),
            )

            cur.execute("SAVEPOINT sp_publish_guard_block")
            try:
                cur.execute(
                    """
                    UPDATE general_courses
                    SET visibility_status = 'PUBLISHED'
                    WHERE course_id = %s::uuid
                    """,
                    (data["course_id"],),
                )
            except Exception as exc:
                blocked_error = str(exc)
                cur.execute("ROLLBACK TO SAVEPOINT sp_publish_guard_block")
            finally:
                cur.execute("RELEASE SAVEPOINT sp_publish_guard_block")

            cur.execute(
                """
                SELECT course_id, title, visibility_status, updated_at
                FROM general_courses
                WHERE course_id = %s::uuid
                """,
                (data["course_id"],),
            )
            course_after_block_attempt = cur.fetchone()

            if blocked_error is None:
                raise ValueError(
                    "Trigger trg_before_publish_course khong chan duoc publish khi chua co module."
                )

            cur.execute(
                """
                INSERT INTO general_course_modules (
                    module_id, course_id, title, order_index
                )
                VALUES (%s::uuid, %s::uuid, %s, 1)
                ON CONFLICT (module_id) DO UPDATE
                SET course_id = EXCLUDED.course_id,
                    title = EXCLUDED.title,
                    order_index = EXCLUDED.order_index
                """,
                (data["module_id"], data["course_id"], "Module 1: Mo dau"),
            )

            cur.execute(
                """
                UPDATE general_courses
                SET visibility_status = 'PUBLISHED'
                WHERE course_id = %s::uuid
                """,
                (data["course_id"],),
            )

            cur.execute(
                """
                SELECT course_id, title, visibility_status, updated_at
                FROM general_courses
                WHERE course_id = %s::uuid
                """,
                (data["course_id"],),
            )
            course_after_publish = cur.fetchone()

            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM general_course_modules
                WHERE course_id = %s::uuid
                """,
                (data["course_id"],),
            )
            module_count = cur.fetchone()["total"]

        conn.commit()
        context["action_data"] = {
            "blocked_publish_error": blocked_error,
            "course_after_block_attempt": course_after_block_attempt,
            "course_after_publish": course_after_publish,
            "module_count": module_count,
        }
        return {
            "blocked_publish_error": blocked_error,
            "course_after_block_attempt": course_after_block_attempt,
            "course_after_publish": course_after_publish,
            "module_count": module_count,
        }

    def step_trigger_check() -> Dict[str, Any]:
        data = context["validated"]
        blocked_error = context["action_data"]["blocked_publish_error"]
        course_after_block = context["action_data"]["course_after_block_attempt"]
        course_after_publish = context["action_data"]["course_after_publish"]

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT action, created_at
                FROM log
                WHERE action ILIKE %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (f"%trg_before_publish_course%{data['course_id']}%",),
            )
            trigger_log = cur.fetchone()
        conn.commit()
        return {
            "blocked_error_present": bool(blocked_error),
            "status_after_block_attempt": None if not course_after_block else course_after_block["visibility_status"],
            "status_after_valid_publish": None if not course_after_publish else course_after_publish["visibility_status"],
            "updated_at_after_valid_publish": None if not course_after_publish else course_after_publish["updated_at"],
            "trigger_log": trigger_log,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "SESSION 3 done: trigger publish guard chan rong va cho phep khi da co module."
        return {"status": "done"}

    v = context.get("validated") or {}
    cid = v.get("course_id", "?")
    mid = v.get("module_id", "?")
    title = v.get("course_title", "Khoa hoc Test Trigger")

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="SESSION 3 - Validate context",
        sql_label="Resolve teacher/category + demo IDs",
        sql_lines=[
            "-- SESSION 3: BUSINESS RULE TRIGGER (PUBLISH GUARD)",
            f"-- demo_course_id = '{cid}', demo_module_id = '{mid}'",
            f"-- demo_course_title = '{title}'",
            "SELECT t.user_id FROM teachers t JOIN users u ON u.user_id=t.user_id WHERE u.is_deleted=FALSE LIMIT 1;",
            "SELECT category_id FROM general_course_categories ORDER BY category_id ASC LIMIT 1;",
        ],
        fn=step_validate,
    )
    cid = context["validated"]["course_id"]
    mid = context["validated"]["module_id"]
    title = context["validated"]["course_title"]
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Draft -> blocked publish -> add module -> publish",
        sql_label="Demonstrate trg_before_publish_course",
        sql_lines=[
            "CREATE OR REPLACE FUNCTION fn_validate_course_publish() RETURNS TRIGGER ...;",
            "DROP TRIGGER IF EXISTS trg_before_publish_course ON general_courses;",
            "CREATE TRIGGER trg_before_publish_course BEFORE UPDATE OF visibility_status ON general_courses ...;",
            f"INSERT INTO general_courses (..., course_id='{cid}', title='{title}', visibility_status='DRAFT') ...;",
            f"DELETE FROM general_course_modules WHERE course_id = '{cid}'::uuid;",
            f"UPDATE general_courses SET visibility_status='PUBLISHED' WHERE course_id = '{cid}'::uuid; -- expected FAIL",
            f"INSERT INTO general_course_modules (module_id, course_id, title, order_index)",
            f"VALUES ('{mid}'::uuid, '{cid}'::uuid, 'Module 1: Mo dau', 1);",
            f"UPDATE general_courses SET visibility_status='PUBLISHED' WHERE course_id = '{cid}'::uuid; -- expected PASS",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="Trigger check: trg_before_publish_course",
        sql_label="Verify blocked attempt + successful publish",
        sql_lines=[
            "-- Trigger ngam fn_validate_course_publish:",
            "-- 1) Chan publish khi module_count = 0",
            "-- 2) Cho phep publish khi da co module",
            f"SELECT course_id, title, visibility_status, updated_at FROM general_courses WHERE course_id = '{cid}'::uuid;",
            f"SELECT COUNT(*) AS module_count FROM general_course_modules WHERE course_id = '{cid}'::uuid;",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


def run_transfer_to_admin_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        ensure_transfer_entities(conn)

        raw_from_user_id = payload.get("from_user_id")
        amount_raw = payload.get("amount")

        with conn.cursor() as cur:
            if raw_from_user_id is None:
                cur.execute(
                    """
                    SELECT w.user_id
                    FROM wallets AS w
                    JOIN users AS u
                         ON u.user_id = w.user_id
                    JOIN roles AS r
                         ON r.role_id = u.role_id
                    WHERE r.role_name <> 'ADMIN'
                      AND u.is_deleted = FALSE
                      AND u.status = 'active'
                    ORDER BY u.created_at ASC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Khong tim thay vi nguon hop le de chuyen tien.")
                from_user_id = row["user_id"]
            else:
                from_user_id = validate_uuid(raw_from_user_id, "from_user_id")

        if amount_raw in (None, ""):
            amount = Decimal("50.00")
        else:
            try:
                amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"))
            except Exception as exc:
                raise ValueError("amount phai la so hop le.") from exc

        if amount <= 0:
            raise ValueError("amount phai lon hon 0.")

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.user_id,
                       u.username,
                       COALESCE(up.full_name, u.username) AS full_name,
                       r.role_name,
                       u.status,
                       w.balance,
                       w.updated_at
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                LEFT JOIN user_profiles AS up
                       ON up.user_id = u.user_id
                WHERE w.user_id = %s::uuid
                """,
                (from_user_id,),
            )
            from_wallet_before = cur.fetchone()
            if not from_wallet_before:
                raise ValueError("from_user_id chua co wallet.")

            cur.execute(
                """
                SELECT w.user_id,
                       u.username,
                       COALESCE(up.full_name, u.username) AS full_name,
                       u.status,
                       w.balance,
                       w.updated_at
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                LEFT JOIN user_profiles AS up
                       ON up.user_id = u.user_id
                WHERE r.role_name = 'ADMIN'
                  AND u.is_deleted = FALSE
                ORDER BY u.created_at ASC
                LIMIT 1
                """,
            )
            admin_wallet_before = cur.fetchone()
            if not admin_wallet_before:
                raise ValueError("Khong tim thay wallet ADMIN de nhan tien.")

        conn.commit()
        context["validated"] = {
            "from_user_id": from_user_id,
            "amount": amount,
            "to_admin_user_id": admin_wallet_before["user_id"],
        }
        context["metrics_before"] = {
            "from_wallet_before": from_wallet_before,
            "admin_wallet_before": admin_wallet_before,
        }
        return {
            "from_wallet_before": from_wallet_before,
            "admin_wallet_before": admin_wallet_before,
            "amount": amount,
        }

    def step_execute() -> Dict[str, Any]:
        data = context["validated"]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM fn_transfer_to_admin_wallet(%s::uuid, %s::numeric)
                """,
                (data["from_user_id"], data["amount"]),
            )
            transfer_result = cur.fetchone()
            if not transfer_result:
                raise ValueError("Khong nhan duoc ket qua tu fn_transfer_to_admin_wallet.")

            tx_id = transfer_result["tx_id"]

            cur.execute(
                """
                SELECT transaction_id,
                       from_wallet_user_id,
                       to_wallet_user_id,
                       amount,
                       status,
                       message,
                       created_at
                FROM transaction_logs
                WHERE transaction_id = %s::uuid
                """,
                (tx_id,),
            )
            transaction_log = cur.fetchone()

        conn.commit()
        context["action_data"] = {
            "transfer_result": transfer_result,
            "transaction_log": transaction_log,
        }
        return {
            "transfer_result": transfer_result,
            "transaction_log": transaction_log,
        }

    def step_trigger_check() -> Dict[str, Any]:
        data = context["validated"]
        result_row = context["action_data"]["transfer_result"]
        tx_id = result_row["tx_id"]

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT w.user_id,
                       u.username,
                       COALESCE(up.full_name, u.username) AS full_name,
                       r.role_name,
                       u.status,
                       w.balance,
                       w.updated_at
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                LEFT JOIN user_profiles AS up
                       ON up.user_id = u.user_id
                WHERE w.user_id = %s::uuid
                """,
                (data["from_user_id"],),
            )
            from_wallet_after = cur.fetchone()

            cur.execute(
                """
                SELECT w.user_id,
                       u.username,
                       COALESCE(up.full_name, u.username) AS full_name,
                       u.status,
                       w.balance,
                       w.updated_at
                FROM wallets AS w
                JOIN users AS u
                     ON u.user_id = w.user_id
                JOIN roles AS r
                     ON r.role_id = u.role_id
                LEFT JOIN user_profiles AS up
                       ON up.user_id = u.user_id
                WHERE w.user_id = %s::uuid
                """,
                (data["to_admin_user_id"],),
            )
            admin_wallet_after = cur.fetchone()

            cur.execute(
                """
                SELECT action_type, message, created_at
                FROM transaction_action_logs
                WHERE transaction_id = %s::uuid
                ORDER BY created_at ASC
                """,
                (tx_id,),
            )
            action_logs = cur.fetchall()

        conn.commit()
        before_from = context["metrics_before"]["from_wallet_before"]
        before_admin = context["metrics_before"]["admin_wallet_before"]
        before_from_balance = Decimal(str(before_from["balance"]))
        before_admin_balance = Decimal(str(before_admin["balance"]))
        after_from_balance = Decimal(str(from_wallet_after["balance"])) if from_wallet_after else before_from_balance
        after_admin_balance = Decimal(str(admin_wallet_after["balance"])) if admin_wallet_after else before_admin_balance
        amount = Decimal(str(data["amount"]))
        success = str(result_row.get("tx_status", "")).upper() == "SUCCESS"

        context["action_data"].update(
            {
                "from_wallet_before": before_from,
                "from_wallet_after": from_wallet_after,
                "admin_wallet_before": before_admin,
                "admin_wallet_after": admin_wallet_after,
                "tx_action_logs": action_logs,
            }
        )
        return {
            "transfer_status": result_row.get("tx_status"),
            "transfer_message": result_row.get("tx_message"),
            "expected_source_decrease": amount if success else Decimal("0.00"),
            "actual_source_decrease": before_from_balance - after_from_balance,
            "expected_admin_increase": amount if success else Decimal("0.00"),
            "actual_admin_increase": after_admin_balance - before_admin_balance,
            "action_log_count": len(action_logs),
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        transfer_result = context.get("action_data", {}).get("transfer_result") or {}
        tx_status = str(transfer_result.get("tx_status", "ERROR")).upper()
        if tx_status == "SUCCESS":
            context["message"] = "Transfer wallet thanh cong. So du nguon giam va vi ADMIN tang dung so tien."
        else:
            context["message"] = f"Transfer wallet khong thanh cong: {transfer_result.get('tx_message')}"
        return {"status": "done"}

    v = context.get("validated") or {}
    source_uid = v.get("from_user_id", "?")
    admin_uid = v.get("to_admin_user_id", "?")
    amount = v.get("amount", Decimal("50.00"))

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate transfer input",
        sql_label="Validate source wallet + admin wallet + amount",
        sql_lines=[
            "-- TRANSFER TXN: hoc vien/giao vien nop tien ve vi ADMIN",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active';",
            "CREATE TABLE IF NOT EXISTS wallets (...);",
            "CREATE TABLE IF NOT EXISTS transaction_logs (...);",
            "CREATE TABLE IF NOT EXISTS transaction_action_logs (...);",
            f"-- from_user_id = '{source_uid}', amount = {amount}",
        ],
        fn=step_validate,
    )
    source_uid = context["validated"]["from_user_id"]
    admin_uid = context["validated"]["to_admin_user_id"]
    amount = context["validated"]["amount"]
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute transfer function",
        sql_label="SELECT * FROM fn_transfer_to_admin_wallet(...)",
        sql_lines=[
            "BEGIN;",
            f"SELECT * FROM fn_transfer_to_admin_wallet('{source_uid}'::uuid, {amount});",
            "-- Trong function:",
            "-- 1) SELECT wallet nguon FOR UPDATE",
            "-- 2) Kiem tra status='active' + du so du",
            "-- 3) SELECT wallet ADMIN FOR UPDATE",
            "-- 4) UPDATE tru tien nguon + cong tien admin",
            "-- 5) INSERT transaction_logs + transaction_action_logs",
            "COMMIT;",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="Verify transfer side-effects",
        sql_label="Read wallets + transaction logs after transfer",
        sql_lines=[
            f"SELECT user_id, balance, updated_at FROM wallets WHERE user_id IN ('{source_uid}'::uuid, '{admin_uid}'::uuid);",
            "SELECT transaction_id, from_wallet_user_id, to_wallet_user_id, amount, status",
            "  FROM transaction_logs ORDER BY created_at DESC LIMIT 5;",
            "SELECT action_type, message, created_at",
            "  FROM transaction_action_logs ORDER BY created_at DESC LIMIT 10;",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="Reload wallets + transaction logs + source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize transfer result",
        fn=step_complete,
    )


# Kịch bản: gọi hàm tìm học viên.
def run_search_students_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        keyword = str(payload.get("keyword", "")).strip()
        keyword_value = keyword if keyword else None
        context["validated"] = {"keyword": keyword_value}
        context["fingerprint_before"] = fetch_mutation_fingerprint(conn)
        conn.commit()
        return {"keyword": keyword_value, "fingerprint_before": context["fingerprint_before"]}

    def step_execute() -> Dict[str, Any]:
        keyword_value = context["validated"]["keyword"]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM fn_search_students(%s::text)
                """,
                (keyword_value,),
            )
            rows = cur.fetchall()
        conn.commit()
        context["action_data"] = {"search_students_rows": rows}
        return {"row_count": len(rows)}

    def step_trigger_check() -> Dict[str, Any]:
        fingerprint_after = fetch_mutation_fingerprint(conn)
        conn.commit()
        return {
            "fingerprint_before": context["fingerprint_before"],
            "fingerprint_after": fingerprint_after,
            "mutated": context["fingerprint_before"] != fingerprint_after,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "Function fn_search_students da duoc thuc thi."
        return {"status": "done"}

    kw_display = context.get("validated", {}).get("keyword") or "NULL"

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="Parse keyword cho fn_search_students",
        sql_lines=[
            f"-- Chuan bi tham so tim kiem:",
            f"-- keyword = '{kw_display}' (NULL = tim tat ca)",
        ],
        fn=step_validate,
    )
    kw_display = context["validated"]["keyword"] or "NULL"
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute function fn_search_students",
        sql_label="SELECT * FROM fn_search_students(keyword)",
        sql_lines=[
            f"-- Goi function tim kiem hoc vien full-text:",
            f"SELECT student_id, username, full_name, grade_level, school_name, created_at",
            f"  FROM fn_search_students(p_keyword => '{kw_display}'::text)",
            f" -- loc san: WHERE is_deleted = FALSE",
            f" -- va ILIKE '%{kw_display}%' tren username/full_name/school_name;",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="Xac nhan: ham khong thay doi du lieu (READ-ONLY)",
        sql_label="Verify fingerprint before = after",
        sql_lines=[
            f"-- fn_search_students() la STABLE function - khong INSERT/UPDATE/DELETE",
            f"-- Xac nhan bang cach so sanh fingerprint truoc vs sau:",
            f"SELECT COUNT(*) FROM course_enrollments; -- phai bang nhau",
            f"SELECT COUNT(*) FROM comments;           -- phai bang nhau",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Kịch bản: gọi hàm tìm khóa học nâng cao.
def run_search_courses_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        keyword = str(payload.get("keyword", "")).strip()
        status = str(payload.get("status", "")).strip().upper()
        category_raw = payload.get("category_id")
        category_id: Optional[int]
        if category_raw in (None, "", "null"):
            category_id = None
        else:
            try:
                category_id = int(category_raw)
            except ValueError as exc:
                raise ValueError("category_id phai la so nguyen hoac de trong.") from exc

        keyword_value = keyword if keyword else None
        status_value = status if status else None
        context["validated"] = {
            "keyword": keyword_value,
            "category_id": category_id,
            "status": status_value,
        }
        context["fingerprint_before"] = fetch_mutation_fingerprint(conn)
        conn.commit()
        return {
            "keyword": keyword_value,
            "category_id": category_id,
            "status": status_value,
            "fingerprint_before": context["fingerprint_before"],
        }

    def step_execute() -> Dict[str, Any]:
        data = context["validated"]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM fn_search_courses_advanced(%s::text, %s::int, %s::text)
                """,
                (data["keyword"], data["category_id"], data["status"]),
            )
            rows = cur.fetchall()
        conn.commit()
        context["action_data"] = {"search_courses_rows": rows}
        return {"row_count": len(rows)}

    def step_trigger_check() -> Dict[str, Any]:
        fingerprint_after = fetch_mutation_fingerprint(conn)
        conn.commit()
        return {
            "fingerprint_before": context["fingerprint_before"],
            "fingerprint_after": fingerprint_after,
            "mutated": context["fingerprint_before"] != fingerprint_after,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "Function fn_search_courses_advanced da duoc thuc thi."
        return {"status": "done"}

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="Parse keyword/category/status",
        fn=step_validate,
    )
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute function",
        sql_label="SELECT * FROM fn_search_courses_advanced(...)",
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="Mutation check",
        sql_label="Compare table fingerprint before/after",
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Kịch bản: cập nhật tiến độ và kiểm tra notification.
def run_update_progress_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        student_id = validate_uuid(payload.get("student_id"), "student_id")
        course_id = validate_uuid(payload.get("course_id"), "course_id")
        progress_raw = payload.get("progress")
        if progress_raw is None:
            raise ValueError("Thieu truong progress.")
        try:
            progress = Decimal(str(progress_raw))
        except Exception as exc:
            raise ValueError("progress phai la so hop le.") from exc
        if progress < 0 or progress > 100:
            raise ValueError("progress phai nam trong khoang 0..100.")

        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM notification_users WHERE user_id = %s::uuid",
                (student_id,),
            )
            notification_before = cur.fetchone()["total"]
        conn.commit()
        context["validated"] = {
            "student_id": student_id,
            "course_id": course_id,
            "progress": progress,
        }
        context["metrics_before"] = {"notification_before": notification_before}
        return {
            "student_id": student_id,
            "course_id": course_id,
            "progress": str(progress),
            "notification_before": notification_before,
        }

    def step_execute() -> Dict[str, Any]:
        data = context["validated"]
        with conn.cursor() as cur:
            cur.execute(
                "CALL sp_update_course_progress(%s::uuid, %s::uuid, %s::numeric)",
                (data["student_id"], data["course_id"], data["progress"]),
            )
            cur.execute(
                """
                SELECT enrollment_id, progress, enrolled_at
                FROM course_enrollments
                WHERE student_id = %s::uuid
                  AND course_id = %s::uuid
                ORDER BY enrolled_at DESC
                LIMIT 1
                """,
                (data["student_id"], data["course_id"]),
            )
            enrollment = cur.fetchone()
        conn.commit()
        context["action_data"] = {"updated_enrollment": enrollment}
        return {"updated_enrollment": enrollment}

    def step_trigger_check() -> Dict[str, Any]:
        student_id = context["validated"]["student_id"]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM notification_users WHERE user_id = %s::uuid",
                (student_id,),
            )
            notification_after = cur.fetchone()["total"]
            cur.execute(
                """
                SELECT notification_id, title, message, created_at
                FROM notification_users
                WHERE user_id = %s::uuid
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (student_id,),
            )
            latest_notification = cur.fetchone()
        conn.commit()
        before = context["metrics_before"]["notification_before"]
        return {
            "notification_before": before,
            "notification_after": notification_after,
            "delta": notification_after - before,
            "latest_notification": latest_notification,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "Procedure sp_update_course_progress da duoc thuc thi."
        return {"status": "done"}

    v = context.get("validated") or {}
    sid = v.get("student_id", "?")
    cid = v.get("course_id", "?")
    prog = str(v.get("progress", "?"))

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="Validate student_id/course_id/progress",
        sql_lines=[
            f"-- Validate tham so:",
            f"-- student_id = '{sid}'",
            f"-- course_id  = '{cid}'",
            f"-- progress   = {prog} (phai trong [0, 100])",
            f"SELECT COUNT(*) AS notif_before FROM notification_users WHERE user_id = '{sid}'::uuid;",
        ],
        fn=step_validate,
    )
    sid = context["validated"]["student_id"]
    cid = context["validated"]["course_id"]
    prog = str(context["validated"]["progress"])
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute procedure sp_update_course_progress",
        sql_label="CALL sp_update_course_progress(...)",
        sql_lines=[
            f"CALL sp_update_course_progress(",
            f"  p_student_id => '{sid}'::uuid,",
            f"  p_course_id  => '{cid}'::uuid,",
            f"  p_progress   => {prog}::numeric",
            f");",
            f"-- -> UPDATE course_enrollments",
            f"--   SET progress = ROUND({prog}, 2)",
            f"--  WHERE student_id = '{sid}' AND course_id = '{cid}';",
            f"",
            f"-- Kiem tra ket qua:",
            f"SELECT enrollment_id, progress FROM course_enrollments",
            f" WHERE student_id = '{sid}'::uuid AND course_id = '{cid}'::uuid;",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="TRIGGER: trg_notify_course_completion (neu progress=100%)",
        sql_label="Check notification_users delta",
        sql_lines=[
            f"-- Neu progress >= 100, trigger da INSERT vao notification_users:",
            f"-- fn_notify_course_completion() -> INSERT notification_users",
            f"SELECT COUNT(*) AS notif_after FROM notification_users WHERE user_id = '{sid}'::uuid;",
            f"SELECT title, message, created_at FROM notification_users",
            f" WHERE user_id = '{sid}'::uuid ORDER BY created_at DESC LIMIT 1;",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Kịch bản: xóa mềm user và check trigger updated_at.
def run_soft_delete_user_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        user_id = validate_uuid(payload.get("user_id"), "user_id")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.user_id, u.username, u.is_deleted, u.updated_at
                FROM users AS u
                WHERE u.user_id = %s::uuid
                """,
                (user_id,),
            )
            user_before = cur.fetchone()
        if not user_before:
            raise ValueError("user_id khong ton tai.")
        conn.commit()
        context["validated"] = {"user_id": user_id}
        context["metrics_before"] = {"user_before": user_before}
        return {"user_before": user_before}

    def step_execute() -> Dict[str, Any]:
        user_id = context["validated"]["user_id"]
        with conn.cursor() as cur:
            cur.execute("CALL sp_soft_delete_user(%s::uuid)", (user_id,))
            cur.execute(
                """
                SELECT user_id, username, is_deleted, updated_at
                FROM users
                WHERE user_id = %s::uuid
                """,
                (user_id,),
            )
            user_after = cur.fetchone()
        conn.commit()
        context["action_data"] = {"user_after_soft_delete": user_after}
        return {"user_after_soft_delete": user_after}

    def step_trigger_check() -> Dict[str, Any]:
        user_id = context["validated"]["user_id"]
        user_before = context["metrics_before"]["user_before"]
        user_after = context["action_data"]["user_after_soft_delete"]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM fn_search_students(NULL::text)
                WHERE student_id = %s::uuid
                """,
                (user_id,),
            )
            visible_in_student_search = cur.fetchone()["total"]
        conn.commit()
        return {
            "user_before": user_before,
            "updated_at_before": user_before["updated_at"],
            "updated_at_after": user_after["updated_at"],
            "updated_at_changed": user_before["updated_at"] != user_after["updated_at"],
            "visible_in_fn_search_students": visible_in_student_search,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "Procedure sp_soft_delete_user da duoc thuc thi."
        return {"status": "done"}

    v = context.get("validated") or {}
    uid = v.get("user_id", "?")

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="Validate user_id",
        sql_lines=[
            f"-- Doc thong tin user truoc khi xoa mem:",
            f"SELECT user_id, username, is_deleted, updated_at",
            f"  FROM users WHERE user_id = '{uid}'::uuid;",
        ],
        fn=step_validate,
    )
    uid = context["validated"]["user_id"]
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute procedure sp_soft_delete_user",
        sql_label="CALL sp_soft_delete_user(user_id)",
        sql_lines=[
            f"CALL sp_soft_delete_user(p_user_id => '{uid}'::uuid);",
            f"-- -> UPDATE users",
            f"--   SET is_deleted = TRUE",
            f"--  WHERE user_id = '{uid}';",
            f"",
            f"-- Kiem tra sau khi xoa mem:",
            f"SELECT user_id, username, is_deleted, updated_at",
            f"  FROM users WHERE user_id = '{uid}'::uuid;",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="TRIGGER: trg_auto_update_users_timestamp + Kiem tra an",
        sql_label="Check fn_search_students visibility",
        sql_lines=[
            f"-- TRIGGER da chay: fn_update_timestamp()",
            f"-- BEFORE UPDATE ON users -> tu dong cap nhat updated_at",
            f"",
            f"SELECT updated_at FROM users WHERE user_id = '{uid}'::uuid;",
            f"-- Xac nhan user da bi an khoi ket qua tim kiem:",
            f"SELECT COUNT(*) AS visible",
            f"  FROM fn_search_students(NULL::text)",
            f" WHERE student_id = '{uid}'::uuid;",
            f"-- Ket qua mong doi: visible = 0 (is_deleted=TRUE bi loc ra)",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Kịch bản: xóa mềm khóa học và check trigger updated_at.
def run_soft_delete_course_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    payload = run.payload

    def step_validate() -> Dict[str, Any]:
        course_id = validate_uuid(payload.get("course_id"), "course_id")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT course_id, title, is_deleted, updated_at
                FROM general_courses
                WHERE course_id = %s::uuid
                """,
                (course_id,),
            )
            course_before = cur.fetchone()
        if not course_before:
            raise ValueError("course_id khong ton tai.")
        conn.commit()
        context["validated"] = {"course_id": course_id}
        context["metrics_before"] = {"course_before": course_before}
        return {"course_before": course_before}

    def step_execute() -> Dict[str, Any]:
        course_id = context["validated"]["course_id"]
        with conn.cursor() as cur:
            cur.execute("CALL sp_soft_delete_course(%s::uuid)", (course_id,))
            cur.execute(
                """
                SELECT course_id, title, is_deleted, updated_at
                FROM general_courses
                WHERE course_id = %s::uuid
                """,
                (course_id,),
            )
            course_after = cur.fetchone()
        conn.commit()
        context["action_data"] = {"course_after_soft_delete": course_after}
        return {"course_after_soft_delete": course_after}

    def step_trigger_check() -> Dict[str, Any]:
        course_id = context["validated"]["course_id"]
        course_before = context["metrics_before"]["course_before"]
        course_after = context["action_data"]["course_after_soft_delete"]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM fn_search_courses_advanced(NULL::text, NULL::int, NULL::text)
                WHERE course_id = %s::uuid
                """,
                (course_id,),
            )
            visible_in_search = cur.fetchone()["total"]
        conn.commit()
        return {
            "course_before": course_before,
            "updated_at_before": course_before["updated_at"],
            "updated_at_after": course_after["updated_at"],
            "updated_at_changed": course_before["updated_at"] != course_after["updated_at"],
            "visible_in_fn_search_courses_advanced": visible_in_search,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "Procedure sp_soft_delete_course da duoc thuc thi."
        return {"status": "done"}

    v = context.get("validated") or {}
    cid = v.get("course_id", "?")

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="Validate course_id",
        sql_lines=[
            f"-- Doc thong tin khoa hoc truoc khi xoa mem:",
            f"SELECT course_id, title, is_deleted, updated_at",
            f"  FROM general_courses WHERE course_id = '{cid}'::uuid;",
        ],
        fn=step_validate,
    )
    cid = context["validated"]["course_id"]
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute procedure sp_soft_delete_course",
        sql_label="CALL sp_soft_delete_course(course_id)",
        sql_lines=[
            f"CALL sp_soft_delete_course(p_course_id => '{cid}'::uuid);",
            f"-- -> UPDATE general_courses",
            f"--   SET is_deleted = TRUE",
            f"--  WHERE course_id = '{cid}';",
            f"",
            f"SELECT course_id, title, is_deleted, updated_at",
            f"  FROM general_courses WHERE course_id = '{cid}'::uuid;",
        ],
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="TRIGGER: trg_auto_update_courses_timestamp + Kiem tra an",
        sql_label="Check fn_search_courses_advanced visibility",
        sql_lines=[
            f"-- TRIGGER da chay: fn_update_timestamp()",
            f"-- BEFORE UPDATE ON general_courses -> tu dong cap nhat updated_at",
            f"",
            f"SELECT updated_at FROM general_courses WHERE course_id = '{cid}'::uuid;",
            f"-- Xac nhan khoa hoc da bi an khoi ket qua tim kiem:",
            f"SELECT COUNT(*) AS visible",
            f"  FROM fn_search_courses_advanced(NULL::text, NULL::int, NULL::text)",
            f" WHERE course_id = '{cid}'::uuid;",
            f"-- Ket qua mong doi: visible = 0",
        ],
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot source tables",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="Reload 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Kịch bản: reset dữ liệu demo về baseline runtime.
def run_reset_pipeline(run: RunState, conn: psycopg.Connection, context: Dict[str, Any]) -> None:
    def step_validate() -> Dict[str, Any]:
        if run.payload:
            raise ValueError("Action reset không nhận payload.")
        conn.commit()
        return {"payload": "empty"}

    def step_execute() -> Dict[str, Any]:
        reset_counts = apply_reset(conn)
        context["action_data"] = {"reset_counts": reset_counts}
        return reset_counts

    def step_trigger_check() -> Dict[str, Any]:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM course_enrollments")
            enrollment_count = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM comments")
            comment_count = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM notification_users")
            notification_count = cur.fetchone()["total"]
        conn.commit()
        return {
            "course_enrollments": enrollment_count,
            "comments": comment_count,
            "notification_users": notification_count,
        }

    def step_refresh_tables() -> Dict[str, Any]:
        tables_after = fetch_source_tables(conn)
        context["tables_after"] = tables_after
        return {name: len(rows) for name, rows in tables_after.items()}

    def step_refresh_views() -> Dict[str, Any]:
        views_after = fetch_reporting_views(conn)
        context["views_after"] = views_after
        return {name: len(rows) for name, rows in views_after.items()}

    def step_complete() -> Dict[str, Any]:
        context["message"] = "Reset demo completed. Data restored from runtime baseline snapshot."
        return {"status": "done"}

    execute_step(
        run,
        step_index=1,
        step_key="validate_input",
        step_name="Validate input",
        sql_label="Kiểm tra payload reset",
        fn=step_validate,
    )
    execute_step(
        run,
        step_index=2,
        step_key="execute_procedure",
        step_name="Execute reset SQL",
        sql_label="Restore tables from runtime baseline snapshot",
        fn=step_execute,
    )
    execute_step(
        run,
        step_index=3,
        step_key="check_trigger_side_effects",
        step_name="Post-reset consistency check",
        sql_label="SELECT COUNT(*) từ các bảng chính",
        fn=step_trigger_check,
    )
    execute_step(
        run,
        step_index=4,
        step_key="refresh_source_tables",
        step_name="Refresh source tables",
        sql_label="SELECT snapshot từ course_enrollments/student_streaks/notification_users/comments",
        fn=step_refresh_tables,
    )
    execute_step(
        run,
        step_index=5,
        step_key="refresh_reporting_views",
        step_name="Refresh reporting views",
        sql_label="SELECT snapshot từ 3 reporting views",
        fn=step_refresh_views,
    )
    execute_step(
        run,
        step_index=6,
        step_key="complete",
        step_name="Complete",
        sql_label="Finalize run result",
        fn=step_complete,
    )


# Worker chính xử lý toàn bộ vòng đời một run.
def run_worker(run: RunState) -> None:
    run.status = "running"
    registry.append_event(
        run,
        "run_started",
        {
            "run_id": run.run_id,
            "action": run.action,
            "created_at": run.created_at,
        },
    )

    ok = False
    error_message: Optional[str] = None
    result: Dict[str, Any]

    try:
        with get_db_connection() as conn:
            if run.action in {"transfer_to_admin", "reset"}:
                ensure_transfer_entities(conn)
            ensure_reset_baseline(conn)
            tables_before = fetch_source_tables(conn)
            views_before = fetch_reporting_views(conn)
            context: Dict[str, Any] = {}
            if run.action == "view_reports":
                run_view_reports_pipeline(run, conn, context)
            elif run.action == "trigger_updated_at":
                run_trigger_updated_at_pipeline(run, conn, context)
            elif run.action == "trigger_init_streak":
                run_trigger_init_streak_pipeline(run, conn, context)
            elif run.action == "trigger_publish_guard":
                run_trigger_publish_guard_pipeline(run, conn, context)
            elif run.action == "transfer_to_admin":
                run_transfer_to_admin_pipeline(run, conn, context)
            elif run.action == "search_students":
                run_search_students_pipeline(run, conn, context)
            elif run.action == "search_courses":
                run_search_courses_pipeline(run, conn, context)
            elif run.action == "enroll":
                run_enroll_pipeline(run, conn, context)
            elif run.action == "update_progress":
                run_update_progress_pipeline(run, conn, context)
            elif run.action == "progress_comment":
                run_progress_comment_pipeline(run, conn, context)
            elif run.action == "soft_delete_user":
                run_soft_delete_user_pipeline(run, conn, context)
            elif run.action == "soft_delete_course":
                run_soft_delete_course_pipeline(run, conn, context)
            elif run.action == "reset":
                run_reset_pipeline(run, conn, context)
            else:
                raise ValueError(f"Action khong duoc ho tro: {run.action}")

            tables_after = context.get("tables_after") or fetch_source_tables(conn)
            views_after = context.get("views_after") or fetch_reporting_views(conn)
            message = context.get("message", "Pipeline đã chạy thành công.")

            result = {
                "ok": True,
                "message": message,
                "trace_summary": run.trace,
                "tables_before": tables_before,
                "tables_after": tables_after,
                "views_before": views_before,
                "views_after": views_after,
                "action_data": context.get("action_data", {}),
            }
            ok = True
            write_audit_log(
                run_id=run.run_id,
                action=run.action,
                status="SUCCESS",
                error_message=None,
            )
    except Exception as exc:
        error_message = str(exc)
        rollback_actions = {
            "trigger_updated_at",
            "trigger_init_streak",
            "trigger_publish_guard",
            "transfer_to_admin",
            "enroll",
            "update_progress",
            "progress_comment",
            "soft_delete_user",
            "soft_delete_course",
            "reset",
        }
        failure_status = "ROLLED_BACK" if run.action in rollback_actions else "FAILED"
        write_audit_log(
            run_id=run.run_id,
            action=run.action,
            status=failure_status,
            error_message=error_message,
        )
        result = {
            "ok": False,
            "message": error_message,
            "trace_summary": run.trace,
        }

    registry.append_event(
        run,
        "run_finished",
        {
            "run_id": run.run_id,
            "action": run.action,
            "ok": ok,
            "message": result.get("message"),
        },
    )
    registry.mark_finished(run, ok=ok, result=result, error=error_message)


# Đóng gói một event theo chuẩn SSE.
def format_sse_event(event: Dict[str, Any]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False)
    base = (
        f"id: {event['seq']}\n"
        f"event: {event['type']}\n"
        f"data: {payload}\n\n"
    )
    # Chèn padding để vượt ngưỡng buffer 4096 byte của WSGI.
    # Làm vậy giúp event được đẩy ra ngay.
    padding_needed = max(0, 4097 - len(base.encode('utf-8')))
    if padding_needed > 0:
        base += f": {' ' * padding_needed}\n\n"
    return base.encode("utf-8")


# Stream event SSE cho client theo thứ tự.
def stream_run_events(run: RunState):
    """Phát event tuần tự để giao diện nhận log realtime."""
    index = 0
    while True:
        timeout = False
        with run.condition:
            # Chờ tới khi có ít nhất 1 event mới.
            while index >= len(run.events) and not run.finished:
                run.condition.wait(timeout=0.2)  # Nghỉ ngắn để bắt event nhanh.
                if index >= len(run.events) and not run.finished:
                    timeout = True
                    break

            next_event = run.events[index] if index < len(run.events) else None
            if next_event:
                index += 1

        if timeout and next_event is None:
            yield b": keepalive\n\n"
            continue

        if next_event:
            yield format_sse_event(next_event)
            # Sau mỗi sql_log thì đẩy thêm 1 comment để flush ngay.
            if next_event.get("type") == "sql_log":
                yield b": \n\n"
        else:
            with run.condition:
                if run.finished and index >= len(run.events):
                    break


load_local_env(os.path.join(os.path.dirname(__file__), ".env"))


app = Flask(__name__, static_folder="static")


@app.get("/")
# Trả trang giao diện chính.
def index() -> Response:
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/init")
# Trả dữ liệu khởi tạo cho frontend.
def api_init():
    with get_db_connection() as conn:
        if not column_exists(conn, "users", "status") or not table_exists(conn, "wallets"):
            ensure_transfer_entities(conn)
        ensure_reset_baseline(conn)
        response = {
            "ok": True,
            "lookups": fetch_lookup_data(conn),
            "tables": fetch_source_tables(conn),
            "views": fetch_reporting_views(conn),
        }
    return jsonify(to_jsonable(response))


@app.post("/api/runs")
# Tạo run mới từ action + payload.
def create_run():
    body = request.get_json(silent=True) or {}
    action = normalize_action(body.get("action"))
    payload = body.get("payload") or {}

    if action not in SUPPORTED_ACTIONS:
        return jsonify({"ok": False, "message": "action không hợp lệ."}), 400
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "message": "payload phải là object JSON."}), 400

    run = registry.create(action=action, payload=payload)
    thread = threading.Thread(target=run_worker, args=(run,), daemon=True, name=f"run-{run.run_id[:8]}")
    thread.start()
    return jsonify({"ok": True, "run_id": run.run_id})


@app.get("/api/runs/<run_id>/events")
# Stream event của một run cụ thể.
def run_events(run_id: str):
    run = registry.get(run_id)
    if run is None:
        return jsonify({"ok": False, "message": "Khong tim thay run_id."}), 404

    def generate():
        yield from stream_run_events(run)

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
        "Transfer-Encoding": "chunked",
    }
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers=headers,
        direct_passthrough=True,
    )


@app.get("/api/runs/<run_id>/result")
# Trả kết quả cuối cùng của run.
def run_result(run_id: str):
    run = registry.get(run_id)
    if run is None:
        return jsonify({"ok": False, "message": "Không tìm thấy run_id."}), 404
    if not run.finished:
        return jsonify({"ok": False, "message": "Run chưa hoàn tất.", "status": run.status}), 202
    return jsonify(to_jsonable(run.result))


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=False,
        threaded=True,
    )




