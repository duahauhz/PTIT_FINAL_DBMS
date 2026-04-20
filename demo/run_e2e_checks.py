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
    enrollments = init_data["tables"]["course_enrollments"]

    existing_pairs = {(row["student_id"], row["course_id"]) for row in enrollments}
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
        try:
            progress = float(row["progress"])
        except Exception:
            progress = 0.0
        if progress < 100.0:
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

    return {
        "search_keyword": students[0]["username"][:3],
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
        scenarios = [
            "view_reports",
            "search_students",
            "search_courses",
            "enroll",
            "update_progress",
            "progress_comment",
            "soft_delete_user",
            "soft_delete_course",
        ]
        missing = []
        for scenario in scenarios:
            if f'data-scenario="{scenario}"' not in index_text:
                missing.append(f"data-scenario:{scenario}")
            has_output = f'id="scenario-output-{scenario}"' in index_text
            has_panel = f'id="panel-{scenario}"' in index_text
            if not (has_output or has_panel):
                missing.append(f"panel-or-output:{scenario}")

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
            rows.append(("ui_static_8_scenarios", False, ", ".join(missing)))
            print(f"[FAIL] ui_static_8_scenarios: missing {', '.join(missing)}", flush=True)
        else:
            rows.append(("ui_static_8_scenarios", True, "all hooks present"))
            print("[PASS] ui_static_8_scenarios: all hooks present", flush=True)

        run_case("reset_baseline", "reset", {}, True, rows)

        status_init, init_data = json_request("GET", "/api/init", None, timeout=10)
        if status_init != 200 or not init_data.get("ok"):
            rows.append(("init_after_reset", False, f"HTTP {status_init}: {init_data}"))
            print(f"[FAIL] init_after_reset: HTTP {status_init}: {init_data}", flush=True)
            return 2

        payloads = choose_payloads(init_data)

        def readonly_check(_events, result):
            step = trace_step(result, "check_trigger_side_effects")
            if not step:
                return False, "missing step check_trigger_side_effects"
            mutated = step.get("details", {}).get("mutated")
            return mutated is False, f"mutated={mutated}"

        run_case("view_reports_readonly", "view_reports", {}, True, rows, readonly_check)
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
            ok = (c1, c2, c3, c4) == (6, 5, 3, 3)
            return ok, f"counts={c1}/{c2}/{c3}/{c4}"

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
