from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple


BASE_PORT = 5051
BASE_URL = f"http://127.0.0.1:{BASE_PORT}"
DEMO_DIR = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def json_request(method: str, path: str, payload: Dict[str, Any] | None = None, timeout: float = 20.0) -> Tuple[int, Dict[str, Any]]:
    url = BASE_URL + path
    headers: Dict[str, str] = {}
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"ok": False, "message": body}
        return exc.code, parsed
    except Exception as exc:
        return 0, {"ok": False, "message": str(exc)}


def text_request(path: str, timeout: float = 10.0) -> Tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE_URL + path, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, str(exc)


def wait_server_ready(timeout: float = 20.0) -> bool:
    started = time.time()
    while time.time() - started < timeout:
        try:
            with socket.create_connection(("127.0.0.1", BASE_PORT), timeout=0.3):
                pass
            status, data = json_request("GET", "/api/init", None, timeout=3)
            if status == 200 and data.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def create_run(action: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    return json_request("POST", "/api/runs", {"action": action, "payload": payload}, timeout=10)


def read_sse_events(run_id: str, timeout: float = 40.0) -> List[Dict[str, Any]]:
    url = BASE_URL + f"/api/runs/{run_id}/events"
    req = urllib.request.Request(url, method="GET")

    events: List[Dict[str, Any]] = []
    event_type: str | None = None
    data_lines: List[str] = []

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            raw = resp.readline()
            if not raw:
                break

            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                if event_type is not None:
                    payload: Dict[str, Any] = {}
                    if data_lines:
                        text = "\n".join(data_lines)
                        try:
                            payload = json.loads(text)
                        except Exception:
                            payload = {"raw": text}
                    events.append({"event": event_type, "payload": payload})
                    if event_type == "run_finished":
                        break
                event_type = None
                data_lines = []
                continue

            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())

    return events


def wait_result(run_id: str, timeout: float = 30.0) -> Tuple[int, Dict[str, Any]]:
    started = time.time()
    while time.time() - started < timeout:
        status, data = json_request("GET", f"/api/runs/{run_id}/result", None, timeout=6)
        if status == 202:
            time.sleep(0.2)
            continue
        return status, data
    return 0, {"ok": False, "message": "timeout waiting /result"}


def trace_step(result: Dict[str, Any], step_key: str) -> Dict[str, Any] | None:
    for item in result.get("trace_summary", []):
        if item.get("step_key") == step_key:
            return item
    return None


def choose_payloads(init_data: Dict[str, Any]) -> Dict[str, Any]:
    students = init_data["lookups"]["students"]
    courses = init_data["lookups"]["courses"]
    lessons = init_data["lookups"]["lessons"]
    users = init_data["lookups"]["users"]
    wallet_senders = init_data["lookups"].get("wallet_senders", [])
    enrollments = init_data["tables"]["course_enrollments"]

    existing_pairs = {(row["student_id"], row["course_id"]) for row in enrollments}
    active_course_ids = {row["course_id"] for row in courses}
    active_student_ids = {row["user_id"] for row in students}
    enroll_pair = None
    for student in students:
        for course in courses:
            pair = (student["user_id"], course["course_id"])
            if pair not in existing_pairs:
                enroll_pair = pair
                break
        if enroll_pair:
            break
    if not enroll_pair:
        enroll_pair = (students[0]["user_id"], courses[0]["course_id"])

    progress_target = None
    for row in enrollments:
        if row["course_id"] not in active_course_ids or row["student_id"] not in active_student_ids:
            continue
        try:
            progress = float(row["progress"])
        except Exception:
            progress = 0.0
        if progress < 100.0:
            progress_target = row
            break
    if not progress_target:
        for row in enrollments:
            if row["course_id"] in active_course_ids and row["student_id"] in active_student_ids:
                progress_target = row
                break
    if not progress_target:
        progress_target = enrollments[0]

    student_user = None
    for user in users:
        if user.get("role_name") == "STUDENT" and not user.get("is_deleted"):
            student_user = user
            break
    if not student_user:
        student_user = users[0]

    timestamp_user = None
    for user in users:
        if user.get("role_name") in {"ADMIN", "TEACHER"} and not user.get("is_deleted"):
            timestamp_user = user
            break
    if not timestamp_user:
        timestamp_user = users[0]

    transfer_sender = None
    if wallet_senders:
        for row in wallet_senders:
            if str(row.get("status", "")).lower() == "active":
                transfer_sender = row
                break
        if not transfer_sender:
            transfer_sender = wallet_senders[0]

    return {
        "search_keyword": students[0]["username"][:3],
        "trigger_updated_at": {
            "user_id": timestamp_user["user_id"],
            "new_username": f"trigger_ts_{int(time.time())}",
        },
        "enroll": {
            "student_id": enroll_pair[0],
            "course_id": enroll_pair[1],
        },
        "update_progress": {
            "student_id": progress_target["student_id"],
            "course_id": progress_target["course_id"],
            "progress": 100,
        },
        "progress_comment": {
            "student_id": progress_target["student_id"],
            "course_id": progress_target["course_id"],
            "lesson_id": lessons[0]["lesson_id"],
            "progress": 100,
            "comment_text": "E2E transaction check " + str(int(time.time())),
        },
        "soft_delete_user": {
            "user_id": student_user["user_id"],
            "username": student_user["username"],
        },
        "soft_delete_course": {
            "course_id": courses[0]["course_id"],
            "course_title": courses[0]["title"],
        },
        "transfer_to_admin": {
            "from_user_id": transfer_sender["user_id"] if transfer_sender else None,
            "amount": 25.00,
        },
    }


def run_case(
    name: str,
    action: str,
    payload: Dict[str, Any],
    expect_ok: bool,
    rows: List[Tuple[str, bool, str]],
    extra_check=None,
) -> Tuple[List[Dict[str, Any]] | None, Dict[str, Any] | None]:
    def add(ok: bool, detail: str) -> None:
        rows.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    try:
        status, data = create_run(action, payload)
    except Exception as exc:
        add(False, f"create_run exception: {exc}")
        return None, None
    if status != 200 or not data.get("ok"):
        add(False, f"create_run failed HTTP {status}: {data}")
        return None, None

    run_id = data["run_id"]
    try:
        events = read_sse_events(run_id)
    except Exception as exc:
        add(False, f"SSE error: {exc}")
        return None, None

    status_r, result = wait_result(run_id)
    if status_r != 200:
        add(False, f"result HTTP {status_r}: {result}")
        return events, result

    event_types = [event.get("event") for event in events]
    step_finished = sum(1 for t in event_types if t == "step_finished")
    step_failed = sum(1 for t in event_types if t == "step_failed")
    base_ok = bool(event_types) and event_types[0] == "run_started" and event_types[-1] == "run_finished"
    if expect_ok:
        sse_ok = base_ok and step_finished == 6 and step_failed == 0
    else:
        sse_ok = base_ok and step_failed >= 1

    if not sse_ok:
        add(False, f"unexpected SSE sequence: {event_types}")
        return events, result

    if bool(result.get("ok")) != expect_ok:
        add(False, f"expected ok={expect_ok}, got ok={result.get('ok')} msg={result.get('message')}")
        return events, result

    if extra_check is not None:
        ok, detail = extra_check(events, result)
        add(ok, detail)
    else:
        add(True, f"ok={result.get('ok')} msg={result.get('message')}")

    return events, result


def main() -> int:
    rows: List[Tuple[str, bool, str]] = []

    def add_result(name: str, ok: bool, detail: str) -> None:
        rows.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=str(DEMO_DIR),
        env={**dict(**__import__("os").environ), "PORT": str(BASE_PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    try:
        if not wait_server_ready():
            rows.append(("startup", False, "server not ready"))
            print("[FAIL] startup: server not ready", flush=True)
            return 1

        rows.append(("startup", True, f"server started on {BASE_URL}"))
        print(f"[PASS] startup: server started on {BASE_URL}", flush=True)

        index_text = (DEMO_DIR / "static" / "index.html").read_text(encoding="utf-8")
        appjs_text = (DEMO_DIR / "static" / "app.js").read_text(encoding="utf-8")
        app_py_text = (DEMO_DIR / "app.py").read_text(encoding="utf-8")
        scenarios = [
            "view_reports",
            "trigger_updated_at",
            "trigger_init_streak",
            "trigger_publish_guard",
            "search_students",
            "search_courses",
            "enroll",
            "update_progress",
            "progress_comment",
            "transfer_to_admin",
            "soft_delete_user",
            "soft_delete_course",
        ]
        detail_scenarios = {
            "view_reports": [
                "btn-detail-student-progress",
                "btn-detail-course-analytics",
                "btn-detail-top-learners",
            ],
        }
        missing = []
        for scenario in scenarios:
            if f'data-scenario="{scenario}"' not in index_text:
                missing.append(f"data-scenario:{scenario}")
            has_output = f'id="scenario-output-{scenario}"' in index_text
            has_panel = f'id="panel-{scenario}"' in index_text
            if not (has_output or has_panel):
                missing.append(f"panel-or-output:{scenario}")

            if scenario in detail_scenarios:
                for marker in detail_scenarios[scenario]:
                    if marker not in index_text:
                        missing.append(f"detail-control:{scenario}:{marker}")
            else:
                has_run_btn = (
                    f'data-action="{scenario}" data-scenario="{scenario}"' in index_text
                    or f'data-scenario="{scenario}" data-action="{scenario}"' in index_text
                )
                if not has_run_btn:
                    missing.append(f"run-button:{scenario}")

        has_scenario_registry = ("const SUPPORTED_SCENARIOS = [" in appjs_text) or ("const PIPELINE_STEPS = [" in appjs_text)
        if not has_scenario_registry:
            missing.append("scenario-registry-in-js")
        if "run_started" not in appjs_text:
            missing.append("sse-run_started-handler")
        if missing:
            rows.append(("ui_static_scenarios", False, ", ".join(missing)))
            print(f"[FAIL] ui_static_scenarios: missing {', '.join(missing)}", flush=True)
        else:
            rows.append(("ui_static_scenarios", True, "all hooks present"))
            print("[PASS] ui_static_scenarios: all hooks present", flush=True)

        view_trigger_contract_missing = []
        for required in [
            "vw_student_progress_report",
            "vw_course_analytics",
            "vw_top_learners_leaderboard",
            "trg_users_update_timestamp",
            "trg_before_publish_course",
            "detail-student-progress",
        ]:
            if required not in index_text:
                view_trigger_contract_missing.append(f"ui:{required}")
        for required in [
            "30000000-0000-0000-0000-000000000001",
            "40000000-0000-0000-0000-000000000999",
            "CREATE TRIGGER trg_users_update_timestamp",
            "CREATE TRIGGER trg_before_publish_course",
        ]:
            if required not in app_py_text:
                view_trigger_contract_missing.append(f"backend:{required}")
        contract_ok = not view_trigger_contract_missing
        rows.append((
            "view_trigger_contract_static",
            contract_ok,
            "ok" if contract_ok else ", ".join(view_trigger_contract_missing),
        ))
        print(
            f"[{'PASS' if contract_ok else 'FAIL'}] view_trigger_contract_static: "
            f"{'ok' if contract_ok else ', '.join(view_trigger_contract_missing)}",
            flush=True,
        )

        run_case("reset_baseline", "reset", {}, True, rows)

        status_init, init_data = json_request("GET", "/api/init", None, timeout=10)
        if status_init != 200 or not init_data.get("ok"):
            rows.append(("init_after_reset", False, f"HTTP {status_init}: {init_data}"))
            print(f"[FAIL] init_after_reset: HTTP {status_init}: {init_data}", flush=True)
            return 2

        payloads = choose_payloads(init_data)
        baseline_table_counts = {
            "course_enrollments": len(init_data.get("tables", {}).get("course_enrollments", [])),
            "comments": len(init_data.get("tables", {}).get("comments", [])),
            "notification_users": len(init_data.get("tables", {}).get("notification_users", [])),
            "student_streaks": len(init_data.get("tables", {}).get("student_streaks", [])),
        }
        baseline_wallet_balances = {
            row.get("user_id"): str(row.get("balance"))
            for row in init_data.get("tables", {}).get("wallets", [])
        }
        baseline_usernames = {
            row.get("user_id"): row.get("username")
            for row in init_data.get("lookups", {}).get("users", [])
        }
        expected_view_keys = {
            "vw_student_progress_report",
            "vw_course_analytics",
            "vw_top_learners_leaderboard",
        }
        legacy_view_keys = {
            "vw_enrollments_by_day",
            "vw_top_courses",
            "vw_top_active_students",
            "vw_user_course_progress",
        }

        init_views = init_data.get("views") or {}
        init_view_keys = set(init_views.keys())
        init_ok = (init_view_keys == expected_view_keys) and not bool(init_view_keys & legacy_view_keys)
        init_detail = f"init_views={sorted(init_view_keys)}"
        add_result("init_views_contract", init_ok, init_detail)

        status_root, root_html = text_request("/")
        add_result(
            "root_12_screen_page",
            status_root == 200
            and "DBMS Studio" in root_html
            and "btn-detail-student-progress" in root_html
            and "trigger_publish_guard" in root_html,
            f"HTTP {status_root}",
        )
        alias_details = []
        alias_ok = True
        for alias_path in ["/studio", "/dbms-demo"]:
            status_alias, alias_html = text_request(alias_path)
            ok_alias = (
                status_alias == 200
                and "DBMS Studio" in alias_html
                and "btn-detail-student-progress" in alias_html
                and "trigger_publish_guard" in alias_html
            )
            alias_ok = alias_ok and ok_alias
            alias_details.append(f"{alias_path}=HTTP {status_alias}")
        add_result("root_aliases_current_ui", alias_ok, ", ".join(alias_details))

        dedicated_views_ok = True
        dedicated_details = []
        for path, key in [
            ("/api/demo/views/student-progress", "vw_student_progress_report"),
            ("/api/demo/views/course-analytics", "vw_course_analytics"),
            ("/api/demo/views/top-learners", "vw_top_learners_leaderboard"),
        ]:
            status_v, data_v = json_request("GET", path, None, timeout=10)
            ok_v = status_v == 200 and data_v.get("success") is True and data_v.get("data", {}).get("view") == key
            rows_count = len(data_v.get("data", {}).get("rows", []))
            dedicated_views_ok = dedicated_views_ok and ok_v and rows_count > 0
            dedicated_details.append(f"{key}:{rows_count}")
        add_result("dedicated_view_apis", dedicated_views_ok, ", ".join(dedicated_details))

        status_before, user_before_data = json_request("GET", "/api/demo/triggers/user-before", None, timeout=10)
        before_user = user_before_data.get("data", {}).get("user", {})
        status_update, user_update_data = json_request(
            "POST",
            "/api/demo/triggers/update-username",
            {"new_username": f"dedicated_minh_{int(time.time())}"},
            timeout=10,
        )
        update_payload = user_update_data.get("data", {})
        dedicated_user_ok = (
            status_before == 200
            and user_before_data.get("success") is True
            and before_user.get("user_id") == "30000000-0000-0000-0000-000000000001"
            and status_update == 200
            and user_update_data.get("success") is True
            and update_payload.get("updated_at_changed") is True
        )
        add_result(
            "dedicated_updated_at_api",
            dedicated_user_ok,
            f"user={before_user.get('user_id')} changed={update_payload.get('updated_at_changed')}",
        )

        status_reset_course, reset_course_data = json_request(
            "POST", "/api/demo/triggers/reset-course-publish-demo", {}, timeout=10
        )
        reset_course_state = reset_course_data.get("data", {}).get("state", {}).get("course", {})
        status_block, block_data = json_request(
            "POST", "/api/demo/triggers/publish-without-module", {}, timeout=10
        )
        status_add, add_data = json_request("POST", "/api/demo/triggers/add-module", {}, timeout=10)
        status_publish, publish_data = json_request("POST", "/api/demo/triggers/publish-with-module", {}, timeout=10)
        publish_state = publish_data.get("data", {}).get("state", {}).get("course", {})
        dedicated_publish_ok = (
            status_reset_course == 200
            and reset_course_data.get("success") is True
            and reset_course_state.get("visibility_status") == "DRAFT"
            and status_block == 200
            and block_data.get("success") is False
            and status_add == 200
            and add_data.get("success") is True
            and status_publish == 200
            and publish_data.get("success") is True
            and publish_state.get("visibility_status") == "PUBLISHED"
        )
        add_result(
            "dedicated_publish_trigger_api",
            dedicated_publish_ok,
            (
                f"reset={reset_course_state.get('visibility_status')} "
                f"blocked={block_data.get('success') is False} "
                f"published={publish_state.get('visibility_status')}"
            ),
        )
        status_cleanup_course, cleanup_course_data = json_request(
            "POST", "/api/demo/triggers/reset-course-publish-demo", {}, timeout=10
        )
        cleanup_state = cleanup_course_data.get("data", {}).get("state", {}).get("course", {})
        add_result(
            "dedicated_course_cleanup",
            status_cleanup_course == 200
            and cleanup_course_data.get("success") is True
            and cleanup_state.get("visibility_status") == "DRAFT",
            f"status={cleanup_state.get('visibility_status')} modules={cleanup_state.get('module_count')}",
        )

        def reset_dedicated_check(_events, result):
            target_user_id = "30000000-0000-0000-0000-000000000001"
            expected_username = baseline_usernames.get(target_user_id)
            users_after = result.get("tables_after", {}).get("users", [])
            actual = next(
                (row.get("username") for row in users_after if row.get("user_id") == target_user_id),
                None,
            )
            return actual == expected_username, f"user={target_user_id} username={actual} expected={expected_username}"

        run_case("reset_after_dedicated_view_trigger", "reset", {}, True, rows, reset_dedicated_check)

        def readonly_check(_events, result):
            step = trace_step(result, "check_trigger_side_effects")
            if not step:
                return False, "missing step check_trigger_side_effects"
            mutated = step.get("details", {}).get("mutated")
            return mutated is False, f"mutated={mutated}"

        def view_reports_check(events, result):
            ok_readonly, detail_readonly = readonly_check(events, result)
            report_views = (result.get("action_data", {}) or {}).get("report_views", {}) or {}
            keys = set(report_views.keys())
            has_exact_keys = keys == expected_view_keys
            has_legacy = bool(keys & legacy_view_keys)
            ok = ok_readonly and has_exact_keys and not has_legacy
            detail = f"{detail_readonly}; keys={sorted(keys)}"
            return ok, detail

        run_case("view_reports_readonly", "view_reports", {}, True, rows, view_reports_check)

        def trigger_updated_at_check(_events, result):
            step = trace_step(result, "check_trigger_side_effects")
            details = {} if not step else step.get("details", {})
            changed = details.get("updated_at_changed")
            after = (result.get("action_data", {}) or {}).get("user_after_update_timestamp") or {}
            ok = bool(changed) and bool(after.get("updated_at"))
            return ok, f"updated_at_changed={changed} user={after.get('user_id')}"

        run_case(
            "trigger_updated_at_demo",
            "trigger_updated_at",
            payloads["trigger_updated_at"],
            True,
            rows,
            trigger_updated_at_check,
        )

        def reset_username_check(_events, result):
            target_user_id = payloads["trigger_updated_at"]["user_id"]
            expected_username = baseline_usernames.get(target_user_id)
            users_after = result.get("tables_after", {}).get("users", [])
            actual = next(
                (row.get("username") for row in users_after if row.get("user_id") == target_user_id),
                None,
            )
            return actual == expected_username, f"user={target_user_id} username={actual} expected={expected_username}"

        run_case("reset_after_trigger_updated_at", "reset", {}, True, rows, reset_username_check)

        def trigger_init_streak_check(_events, result):
            after = (result.get("action_data", {}) or {}).get("streak_after_insert_student") or {}
            created = bool(after) and str(after.get("current_streak")) == "0"
            return created, f"streak_row_present={bool(after)} current_streak={after.get('current_streak')}"

        run_case("trigger_init_streak_demo", "trigger_init_streak", {}, True, rows, trigger_init_streak_check)

        def trigger_publish_guard_check(_events, result):
            ad = result.get("action_data", {}) or {}
            blocked = bool(ad.get("blocked_publish_error"))
            course_after = ad.get("course_after_publish") or {}
            published = course_after.get("visibility_status") == "PUBLISHED"
            return blocked and published, f"blocked={blocked} published={published}"

        run_case("trigger_publish_guard_demo", "trigger_publish_guard", {}, True, rows, trigger_publish_guard_check)

        run_case("search_students_readonly", "search_students", {"keyword": payloads["search_keyword"]}, True, rows, readonly_check)
        run_case("search_courses_readonly", "search_courses", {"keyword": "", "category_id": "", "status": ""}, True, rows, readonly_check)

        def enroll_check(_events, result):
            before = len(result.get("tables_before", {}).get("course_enrollments", []))
            after = len(result.get("tables_after", {}).get("course_enrollments", []))
            enrollment = result.get("action_data", {}).get("enrollment")
            return (after == before + 1 and bool(enrollment), f"course_enrollments {before}->{after} enrollment_present={bool(enrollment)}")

        run_case("enroll_mutate", "enroll", payloads["enroll"], True, rows, enroll_check)

        def reset_count_check(_events, result):
            tables_after = result.get("tables_after", {})
            c1 = len(tables_after.get("course_enrollments", []))
            c2 = len(tables_after.get("comments", []))
            c3 = len(tables_after.get("notification_users", []))
            c4 = len(tables_after.get("student_streaks", []))
            expected = (
                baseline_table_counts["course_enrollments"],
                baseline_table_counts["comments"],
                baseline_table_counts["notification_users"],
                baseline_table_counts["student_streaks"],
            )
            actual = (c1, c2, c3, c4)
            ok = actual == expected
            return ok, f"counts={actual} expected={expected}"

        run_case("reset_after_enroll", "reset", {}, True, rows, reset_count_check)

        def update_check(_events, result):
            enrollment = result.get("action_data", {}).get("updated_enrollment") or {}
            try:
                progress = float(enrollment.get("progress"))
            except Exception:
                progress = -1.0
            step = trace_step(result, "check_trigger_side_effects")
            delta = None if not step else step.get("details", {}).get("delta")
            ok = progress == 100.0 and (delta is None or float(delta) >= 0)
            return ok, f"updated_progress={progress} notification_delta={delta}"

        run_case("update_progress_mutate", "update_progress", payloads["update_progress"], True, rows, update_check)
        run_case("reset_after_update_progress", "reset", {}, True, rows, reset_count_check)

        def progress_comment_check(_events, result):
            before = len(result.get("tables_before", {}).get("comments", []))
            after = len(result.get("tables_after", {}).get("comments", []))
            inserted = result.get("action_data", {}).get("inserted_comment")
            ok = after == before + 1 and bool(inserted)
            return ok, f"comments {before}->{after} inserted={bool(inserted)}"

        run_case("progress_comment_transaction", "progress_comment", payloads["progress_comment"], True, rows, progress_comment_check)
        run_case("reset_after_progress_comment", "reset", {}, True, rows, reset_count_check)

        def transfer_to_admin_check(_events, result):
            ad = result.get("action_data", {}) or {}
            transfer = ad.get("transfer_result") or {}
            tx_log = ad.get("transaction_log") or {}
            from_before = ad.get("from_wallet_before") or {}
            from_after = ad.get("from_wallet_after") or {}
            admin_before = ad.get("admin_wallet_before") or {}
            admin_after = ad.get("admin_wallet_after") or {}

            try:
                amount = float(payloads["transfer_to_admin"]["amount"])
                src_before = float(from_before.get("balance"))
                src_after = float(from_after.get("balance"))
                dst_before = float(admin_before.get("balance"))
                dst_after = float(admin_after.get("balance"))
                source_delta_ok = abs((src_before - src_after) - amount) < 0.001
                admin_delta_ok = abs((dst_after - dst_before) - amount) < 0.001
            except Exception:
                source_delta_ok = False
                admin_delta_ok = False

            status_ok = transfer.get("tx_status") == "SUCCESS" and tx_log.get("status") == "SUCCESS"
            ok = status_ok and source_delta_ok and admin_delta_ok
            return ok, (
                f"tx_status={transfer.get('tx_status')} tx_log_status={tx_log.get('status')} "
                f"source_delta_ok={source_delta_ok} admin_delta_ok={admin_delta_ok}"
            )

        run_case(
            "transfer_to_admin_transaction",
            "transfer_to_admin",
            payloads["transfer_to_admin"],
            True,
            rows,
            transfer_to_admin_check,
        )

        def reset_with_wallet_check(_events, result):
            ok_counts, detail_counts = reset_count_check(_events, result)
            wallet_rows = result.get("tables_after", {}).get("wallets", [])
            after_wallet_balances = {
                row.get("user_id"): str(row.get("balance"))
                for row in wallet_rows
            }
            wallet_ok = True
            for uid, bal in baseline_wallet_balances.items():
                if uid not in after_wallet_balances:
                    wallet_ok = False
                    break
                if after_wallet_balances[uid] != bal:
                    wallet_ok = False
                    break
            return wallet_ok and ok_counts, f"{detail_counts}; wallet_restored={wallet_ok}"

        run_case("reset_after_transfer", "reset", {}, True, rows, reset_with_wallet_check)

        def soft_delete_user_check(_events, result):
            after = result.get("action_data", {}).get("user_after_soft_delete") or {}
            return after.get("is_deleted") is True, f"user_is_deleted={after.get('is_deleted')}"

        run_case("soft_delete_user_mutate", "soft_delete_user", {"user_id": payloads["soft_delete_user"]["user_id"]}, True, rows, soft_delete_user_check)

        def search_deleted_user_check(_events, result):
            rows_out = result.get("action_data", {}).get("search_students_rows", [])
            usernames = {row.get("username") for row in rows_out}
            target = payloads["soft_delete_user"]["username"]
            return target not in usernames, f"target={target} rows={len(rows_out)}"

        run_case("search_after_soft_delete_user", "search_students", {"keyword": payloads["soft_delete_user"]["username"]}, True, rows, search_deleted_user_check)
        run_case("reset_after_soft_delete_user", "reset", {}, True, rows, reset_count_check)

        def soft_delete_course_check(_events, result):
            after = result.get("action_data", {}).get("course_after_soft_delete") or {}
            return after.get("is_deleted") is True, f"course_is_deleted={after.get('is_deleted')}"

        run_case("soft_delete_course_mutate", "soft_delete_course", {"course_id": payloads["soft_delete_course"]["course_id"]}, True, rows, soft_delete_course_check)

        def search_deleted_course_check(_events, result):
            rows_out = result.get("action_data", {}).get("search_courses_rows", [])
            titles = {row.get("title") for row in rows_out}
            target = payloads["soft_delete_course"]["course_title"]
            return target not in titles, f"target={target} rows={len(rows_out)}"

        run_case(
            "search_after_soft_delete_course",
            "search_courses",
            {"keyword": payloads["soft_delete_course"]["course_title"], "category_id": "", "status": ""},
            True,
            rows,
            search_deleted_course_check,
        )
        run_case("reset_after_soft_delete_course", "reset", {}, True, rows, reset_count_check)

        run_case("negative_invalid_uuid", "enroll", {"student_id": "invalid-uuid", "course_id": payloads["enroll"]["course_id"]}, False, rows)
        run_case("negative_missing_field", "enroll", {"course_id": payloads["enroll"]["course_id"]}, False, rows)
        run_case(
            "negative_progress_over_100",
            "update_progress",
            {
                "student_id": payloads["update_progress"]["student_id"],
                "course_id": payloads["update_progress"]["course_id"],
                "progress": 120,
            },
            False,
            rows,
        )
        run_case("negative_reset_has_payload", "reset", {"unexpected": 1}, False, rows)
        run_case("alias_view_report", "view_report", {}, True, rows)

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

    passed = sum(1 for _name, ok, _detail in rows if ok)
    failed = len(rows) - passed
    print("\n=== TEST SUMMARY ===")
    print(f"Total: {len(rows)} | Pass: {passed} | Fail: {failed}")
    if failed:
        print("Failed cases:")
        for name, ok, detail in rows:
            if not ok:
                print(f"- {name}: {detail}")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
