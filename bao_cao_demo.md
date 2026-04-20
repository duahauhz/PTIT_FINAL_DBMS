# Báo Cáo Kỹ Thuật: Hệ Thống Cơ Sở Dữ Liệu Học Ngôn Ngữ Ký Hiệu

> [!NOTE]
> Tài liệu này tổng hợp logic thiết kế cơ sở dữ liệu nâng cao (View, Procedure, Trigger, Transaction). 
> Kịch bản được thiết kế hoàn thiện cho các buổi bảo vệ hoặc trình diễn (demo) bài tập lớn.

---

## I. Xây Dựng Khung Nhìn (Views)
Nhằm phục vụ quá trình trích xuất dữ liệu nhanh gọn lên hệ thống Dashboard của Quản trị viên, hệ thống sử dụng các View báo cáo thống kê phức tạp (có sử dụng `JOIN` nhiều bảng, nhóm dữ liệu `GROUP BY` và các hàm phân tích phân đoạn).

| Tên View | Chức năng cốt lõi | Ý nghĩa nghiệp vụ |
| :--- | :--- | :--- |
| `vw_enrollments_by_day` | Thống kê số lượng ghi danh theo từng ngày. | Hỗ trợ theo dõi lượng truy cập và độ quan tâm của học viên theo thời gian thực. |
| `vw_top_courses` | Xếp hạng khóa học theo số học viên và `avg_progress`. | Giúp trung tâm phân tích xu hướng và biết khóa nào đang giảng dạy hiệu quả nhất. |
| `vw_top_active_students` | Chấm **Điểm Hoạt Động** (Tiến độ + Số bình luận * 5). | Tự động xếp hạng cá nhân năng nổ nhất, nhằm vinh danh hoặc trao thưởng hàng tháng. |
| `vw_user_course_progress` | Hiển thị trạng thái học: DRAFT, PUBLISHED. | Ẩn chi tiết mã hóa DB phức tạp, trả trực tiếp Data thô cho Frontend. |

---

## II. Lập Trình Thủ Tục Và Hàm (Functions & Procedures)
Để bảo mật dữ liệu ở tầng Backend và tối ưu hóa truy vấn, hệ thống thiết lập các Hàm và Thủ tục xử lý nội tại:

### 1. Nhóm xử lý tra cứu (Functions)
- **`fn_search_students(keyword)`**: Hỗ trợ tìm kiếm mờ (`ILIKE`) theo tên, username hoặc trường học. Có kết hợp `COALESCE` nhằm loại trừ các lỗi kết xuất do chuỗi `NULL`.
- **`fn_search_courses_advanced(...)`**: Xây dựng bộ lọc động linh hoạt tìm khóa học theo từ khoá, mã danh mục và trạng thái trực tuyến.

### 2. Nhóm thay đổi dữ liệu (Stored Procedures)
- **`sp_enroll_student`**: 
  Tham số hóa quy trình ghi danh. Có logic kiểm tra khóa ngoại để xác định Student và Course có tồn tại thực sự hay không trước khi thực hiện lệnh `INSERT`.
- **`sp_update_course_progress`**: 
  Thủ tục cập nhật tiến độ học tập có đính kèm **bắt lỗi (Exception)** để khống chế giá trị đầu vào của tiến trình học chỉ được giới hạn ở ngưỡng `0.00` đến `100.00`.

> [!TIP]
> Việc phân tách rạch ròi giữa Query dạng `SELECT` (dùng Function) và Thao tác biến đổi `INSERT/UPDATE` (dùng Procedure) là minh chứng cho một kiến trúc lập trình CSDL xuất sắc.

---

## III. Ràng Buộc Dữ Liệu Chủ Động (Triggers)
Cơ sở dữ liệu của hệ thống được lập trình rẽ nhánh tự động (Data-driven logic) nhằm phản hồi ngay lập tức với tương tác người dùng:

### 📍 Auto-Update Streaks (Tự Động Tính Chuỗi Ngày)
- **Kích hoạt (`trg_touch_student_activity_on_enroll`)**: Ngay khi học sinh có tương tác, Trigger lập tức rẽ nhánh đọc lại thời gian online gần nhất.
- **Xử lý**: Nếu online sang ngày mới, hệ thống tự tăng biến `current_streak` và cập nhật `highest_streak`. Hệ thống trò chơi hóa (gamification) vận hành độc lập hoàn toàn với Backend.

### 📍 Auto-Notification (Cấp Phát Thông Báo Tự Động)
- **Kích hoạt (`trg_notify_course_completion`)**: Giám sát dòng lệnh Update tiến trình học tập (`progress`).
- **Xử lý**: Nếu đột biến cập nhật đạt tỷ lệ 100%, hệ thống tự động đẩy chuỗi vào bảng `notification_users` dọn sẵn một tin nhắn chúc mừng tới học viên.

---

## IV. Kiểm Soát Giao Dịch Đồng Thời (Transactions)
Với các tác vụ mang tính liên chuỗi bảo đảm quy luật **ACID (Atomicity, Consistency, Isolation, Durability)**, hệ thống lập phiên giao dịch để tránh tình trạng "lưu một nửa".

> [!IMPORTANT]
> **Kịch Bản Bảo Vệ Toàn Vẹn Học Tập**
> Khi người dùng "Hoàn thành bài học", hệ thống phải thực hiện CÙNG LÚC 2 thao tác:
> 1. Gọi `sp_update_course_progress` lên 100%.
> 2. Chèn 1 bài viết đánh giá (`INSERT INTO comments`).

```sql
BEGIN;

CALL sp_update_course_progress(... 100.00);
INSERT INTO comments (lesson_id, user_id, content) VALUES (...);

COMMIT;
```

**Cơ chế Fail-Safe (Rollback):**
Tất cả biểu mẫu trên được bọc trong khối lệnh `BEGIN ... COMMIT`. Nếu chèn bình luận bị lỗi rác (ví dụ lỗi DB do ký tự EMOJI lạ), toàn bộ lệnh cập nhật Progress 100% trước đó cũng **Lập Tức Hủy Bỏ (Rollback)**. Điều này loại bỏ hoàn toàn viễn cảnh học viên *nhận được điểm hoàn thành bài thi* dù hệ thống *không thể trích xuất bình luận/bài luận cuối môn*.
