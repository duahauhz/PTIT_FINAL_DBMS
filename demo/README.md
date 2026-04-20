# SQL Pipeline Demo (Flask + PostgreSQL)

Demo này minh họa dữ liệu thật chạy theo pipeline:

1. Validate input
2. Execute procedure/transaction
3. Trigger side-effects check
4. Refresh source tables
5. Refresh reporting views
6. Complete

Frontend nhận tiến độ từng bước qua SSE (`EventSource`) và hiển thị Step Cards theo thời gian thực.

## Kịch bản trong giao diện
Giao diện chia thành 8 kịch bản riêng đúng theo `procedure_trigger_transaction.sql`:

1. View reports (4 views)
2. Function `fn_search_students`
3. Function `fn_search_courses_advanced`
4. Procedure `sp_enroll_student`
5. Procedure `sp_update_course_progress`
6. Transaction end-to-end (`CALL sp_update_course_progress` + `INSERT comments`)
7. Procedure `sp_soft_delete_user`
8. Procedure `sp_soft_delete_course`

Mỗi kịch bản có nút `Reset DB` riêng, gọi action `reset` để đưa dữ liệu về baseline demo.

## 1) Chuẩn bị DB
Chạy theo đúng thứ tự:

1. `ERD.sql`
2. `seed_data.sql`
3. `procedure_trigger_transaction.sql`

## 2) Cài dependency
```powershell
cd e:\DBMS\PTIT_final_dbms\FINAL\FINAL\demo
python -m pip install -r requirements.txt
```

## 3) Cấu hình kết nối
Tạo file `.env` từ `.env.example`:
```powershell
copy .env.example .env
```

Cập nhật các biến `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`.

Lưu ý: nếu bạn đã có `DATABASE_URL`, app sẽ ưu tiên biến này.

## 4) Chạy app
```powershell
cd e:\DBMS\PTIT_final_dbms\FINAL\FINAL\demo
python app.py
```

Mở trình duyệt:
`http://127.0.0.1:5000`

## 5) API chính
- `GET /api/init`: tải lookup + snapshot ban đầu.
- `POST /api/runs`: tạo run mới.
  - `action`:
    - `view_reports`
    - `search_students`
    - `search_courses`
    - `enroll`
    - `update_progress`
    - `progress_comment`
    - `soft_delete_user`
    - `soft_delete_course`
    - `reset`
- `GET /api/runs/{run_id}/events`: stream SSE thời gian thực.
- `GET /api/runs/{run_id}/result`: lấy kết quả cuối, gồm before/after + trace.

## 6) Lưu ý kỹ thuật
- Action `progress_comment` chạy transaction thật: `CALL sp_update_course_progress` + `INSERT comments`.
- Trigger `trg_notify_course_completion` sẽ tạo dữ liệu thật trong `notification_users`.
- Action `reset` chỉ reset baseline trong phạm vi bảng demo:
  - `course_enrollments`
  - `student_streaks`
  - `notification_users`
  - `comments`
