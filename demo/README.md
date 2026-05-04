# SQL Pipeline Demo (Flask + PostgreSQL)

Demo này mô phỏng dữ liệu thật chạy theo pipeline:

1. Validate input
2. Execute procedure/transaction
3. Trigger side-effects check
4. Refresh source tables
5. Refresh reporting views
6. Complete

Frontend nhận tiến độ từng bước qua SSE (`EventSource`) và hiển thị Step Cards theo thời gian thực.

## Kịch bản trong giao diện
Giao diện chia thành 12 kịch bản riêng đúng theo `procedure_trigger_transaction.sql`:

1. View reports (3 views)
2. Trigger session 1: `trg_users_update_timestamp`
3. Trigger session 2: `trg_after_insert_student`
4. Trigger session 3: `trg_before_publish_course`
5. Function `fn_search_students`
6. Function `fn_search_courses_advanced`
7. Procedure `sp_enroll_student`
8. Procedure `sp_update_course_progress`
9. Transaction end-to-end (`CALL sp_update_course_progress` + `INSERT comments`)
10. Transaction chuyển tiền vào ví ADMIN (`fn_transfer_to_admin_wallet`)
11. Procedure `sp_soft_delete_user`
12. Procedure `sp_soft_delete_course`

Mỗi kịch bản có nút `Reset DB` riêng, gọi action `reset` để đưa dữ liệu về baseline demo.

## 1) Chuẩn bị DB
Chạy theo đúng thứ tự:

1. `ERD.sql`
2. `seed_data.sql`
3. `procedure_trigger_transaction.sql`

## 2) Cài dependency
```powershell
cd e:\DBMS\FINAL\PTIT_FINAL_DBMS\demo
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
cd e:\DBMS\FINAL\PTIT_FINAL_DBMS\demo
python app.py
```

Mở trình duyệt: `http://127.0.0.1:5000`

Trang mặc định là giao diện DBMS Studio 12 screen. Phần Views/Triggers đã được tách nút thao tác chi tiết ngay trong các screen tương ứng.

## 5) API chính
- `GET /api/init`: tải lookup + snapshot ban đầu.
- `POST /api/runs`: tạo run mới.
  - `action`:
    - `view_reports`
    - `trigger_updated_at`
    - `trigger_init_streak`
    - `trigger_publish_guard`
    - `search_students`
    - `search_courses`
    - `enroll`
    - `update_progress`
    - `progress_comment`
    - `transfer_to_admin`
    - `soft_delete_user`
    - `soft_delete_course`
    - `reset`
- `GET /api/runs/{run_id}/events`: stream SSE thời gian thực.
- `GET /api/runs/{run_id}/result`: lấy kết quả cuối, gồm before/after + trace.

## 6) Lưu ý kỹ thuật
- Action `progress_comment` chạy transaction thật: `CALL sp_update_course_progress` + `INSERT comments`.
- Action `transfer_to_admin` chạy transaction ví: lock row nguồn/đích (`FOR UPDATE`), kiểm tra `status`, số dư, ghi `transaction_logs` và `transaction_action_logs`.
- Trigger `trg_notify_course_completion` sẽ tạo dữ liệu thật trong `notification_users`.
- Action `reset` reset baseline trong các bảng demo chính, bao gồm cả bảng ví/giao dịch nếu có.
