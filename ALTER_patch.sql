-- =========================================================
-- SCRIPT CẬP NHẬT CSDL THỰC TẾ (PATCH)
-- Chạy file này trực tiếp vào DB hiện tại của bạn
-- Mục đích: Bổ sung tính năng Xóa mềm (Soft-Delete), 
-- Last Updated (updated_at) và Đánh chỉ mục (Indexes)
-- =========================================================

-- 1) BỔ SUNG CỘT Soft-Delete & Updated_at cho Users
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

-- 2) BỔ SUNG CỘT Soft-Delete & Updated_at cho Khóa học
ALTER TABLE general_courses 
ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

-- 2.5) BỔ SUNG CỘT Soft-Delete & Updated_at cho Từ Điển
ALTER TABLE dictionary_entries 
ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

-- 3) ĐỒNG BỘ DỮ LIỆU VỚI BẢN SEED TRƯỚC ĐÓ 
-- (Đề phòng một số ROW cũ hiển thị NULL)
UPDATE users 
SET is_deleted = FALSE 
WHERE is_deleted IS NULL;

UPDATE users 
SET updated_at = created_at 
WHERE updated_at IS NULL;

UPDATE general_courses 
SET is_deleted = FALSE 
WHERE is_deleted IS NULL;

-- 4) TẠO CHỈ MỤC (INDEX) TỐI ƯU HÓA TRUY VẤN
-- Tăng tốc cho hàm fn_search_students
CREATE INDEX IF NOT EXISTS idx_users_username_search ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_is_deleted_search ON users (is_deleted);

-- Tăng tốc cho hàm fn_search_courses_advanced
CREATE INDEX IF NOT EXISTS idx_courses_title_search ON general_courses (title);
CREATE INDEX IF NOT EXISTS idx_courses_is_deleted_search ON general_courses (is_deleted);

-- =========================================================
-- 5) DỮ LIỆU DEMO BỔ SUNG ĐỂ TEST TÍNH NĂNG MỚI
-- =========================================================

-- 5.1 Thêm 1 Tài khoản Học viên rác để làm "bia tập bắn"
INSERT INTO users (user_id, username, password_hash, email, role_id, created_at)
VALUES (
    '30000000-0000-0000-0000-000000000099',
    'student_test_xoa',
    '$2b$12$studenttestxoaplaceholder000000000000',
    'test.xoa@signlearn.local',
    (SELECT role_id FROM roles WHERE role_name = 'STUDENT'),
    CURRENT_TIMESTAMP
) ON CONFLICT DO NOTHING;

INSERT INTO students (user_id, grade_level, school_name)
VALUES ('30000000-0000-0000-0000-000000000099', 'Demo Xoa', 'Truong Rác')
ON CONFLICT DO NOTHING;

INSERT INTO user_profiles (user_id, full_name, phone_number)
VALUES ('30000000-0000-0000-0000-000000000099', 'Học Sinh Sắp Bị Xóa', '0999999999')
ON CONFLICT DO NOTHING;

-- 5.2 Test chức năng Xóa mềm (GỌI PROCEDURE)
-- Gọi hàm xóa mềm học viên vừa tạo
CALL sp_soft_delete_user('30000000-0000-0000-0000-000000000099');
-- (Sau khi chạy, lên hàm fn_search_students tìm 'xóa' sẽ không thấy 
-- nhưng data trong bảng users thực tế vẫn còn và is_deleted = t)

-- 5.3 Test Trigger biến đổi thời gian Cập nhật (updated_at)
-- Thử đổi Email tài khoản Admin trong DB
UPDATE users
SET email = 'admin_demo.updated@signlearn.local'
WHERE user_id = '10000000-0000-0000-0000-000000000001';
-- (Lúc này Trigger sẽ ép thời gian ở thẻ "updated_at" của Admin thành ngày giờ phút giây hiện tại!)
