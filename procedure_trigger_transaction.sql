-- =========================================================
-- PostgreSQL Utility Script (chuẩn hóa theo ERD.sql)
-- Mục tiêu:
-- 1) Tạo view phục vụ báo cáo demo.
-- 2) Tạo trigger/function nghiệp vụ học tập.
-- 3) Tạo procedure/function tìm kiếm và cập nhật tiến độ.
-- 4) Cung cấp mẫu transaction demo end-to-end (không payment/refund).
--
-- Yêu cầu: chạy ERD.sql trước khi chạy file này.
-- =========================================================

SET search_path TO public;

-- =========================================================
-- 1) VIEW BÁO CÁO DEMO
-- =========================================================

-- 1.1 Số lượt đăng ký khóa học theo ngày
CREATE OR REPLACE VIEW vw_enrollments_by_day AS
SELECT DATE(ce.enrolled_at) AS enroll_day,
       COUNT(*) AS total_enrollments
FROM course_enrollments AS ce
GROUP BY DATE(ce.enrolled_at)
ORDER BY enroll_day;

-- 1.2 Top khóa học theo số học viên
CREATE OR REPLACE VIEW vw_top_courses AS
SELECT gc.course_id,
       gc.title,
       gc.visibility_status,
       COUNT(ce.enrollment_id) AS total_students,
       COALESCE(ROUND(AVG(ce.progress)::numeric, 2), 0.00)::numeric(5, 2) AS avg_progress
FROM general_courses AS gc
LEFT JOIN course_enrollments AS ce
       ON ce.course_id = gc.course_id
GROUP BY gc.course_id, gc.title, gc.visibility_status
ORDER BY total_students DESC, gc.title ASC;

-- 1.3 Top học viên hoạt động tích cực
-- Điểm hoạt động = trung bình tiến độ + (số bình luận * 5)
CREATE OR REPLACE VIEW vw_top_active_students AS
WITH progress_stats AS (
    SELECT ce.student_id,
           COUNT(*) AS enrolled_courses,
           COUNT(*) FILTER (WHERE ce.progress >= 100) AS completed_courses,
           COALESCE(ROUND(AVG(ce.progress)::numeric, 2), 0.00)::numeric(5, 2) AS avg_progress
    FROM course_enrollments AS ce
    GROUP BY ce.student_id
),
comment_stats AS (
    SELECT c.user_id AS student_id,
           COUNT(*) AS comment_count
    FROM comments AS c
    GROUP BY c.user_id
)
SELECT s.user_id AS student_id,
       u.username,
       up.full_name,
       COALESCE(ps.enrolled_courses, 0) AS enrolled_courses,
       COALESCE(ps.completed_courses, 0) AS completed_courses,
       COALESCE(ps.avg_progress, 0.00)::numeric(5, 2) AS avg_progress,
       COALESCE(cs.comment_count, 0) AS comment_count,
       (COALESCE(ps.avg_progress, 0) + COALESCE(cs.comment_count, 0) * 5)::numeric(7, 2) AS activity_score
FROM students AS s
JOIN users AS u
     ON u.user_id = s.user_id
LEFT JOIN user_profiles AS up
       ON up.user_id = s.user_id
LEFT JOIN progress_stats AS ps
       ON ps.student_id = s.user_id
LEFT JOIN comment_stats AS cs
       ON cs.student_id = s.user_id
ORDER BY activity_score DESC, avg_progress DESC, comment_count DESC, u.username ASC;

-- 1.4 Tiến độ từng học viên theo từng khóa học
CREATE OR REPLACE VIEW vw_user_course_progress AS
SELECT ce.student_id,
       u.username,
       gc.course_id,
       gc.title AS course_title,
       ce.progress,
       CASE
           WHEN ce.progress >= 100 THEN 'COMPLETED'
           WHEN ce.progress > 0 THEN 'IN_PROGRESS'
           ELSE 'NOT_STARTED'
       END AS progress_status,
       ce.enrolled_at
FROM course_enrollments AS ce
JOIN users AS u
     ON u.user_id = ce.student_id
JOIN general_courses AS gc
     ON gc.course_id = ce.course_id;

-- =========================================================
-- 2) TRIGGER FUNCTIONS + TRIGGERS
-- =========================================================

-- 2.1 Chạm hoạt động học viên sau khi enroll
CREATE OR REPLACE FUNCTION fn_touch_student_activity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_prev_streak student_streaks%ROWTYPE;
    v_new_streak INTEGER;
BEGIN
    SELECT *
    INTO v_prev_streak
    FROM student_streaks
    WHERE student_id = NEW.student_id
    FOR UPDATE;

    IF NOT FOUND THEN
        INSERT INTO student_streaks (
            student_id,
            current_streak,
            highest_streak,
            last_activity_date
        )
        VALUES (
            NEW.student_id,
            1,
            1,
            CURRENT_DATE
        );
        RETURN NEW;
    END IF;

    IF v_prev_streak.last_activity_date = CURRENT_DATE THEN
        v_new_streak := v_prev_streak.current_streak;
    ELSIF v_prev_streak.last_activity_date = CURRENT_DATE - 1 THEN
        v_new_streak := v_prev_streak.current_streak + 1;
    ELSE
        v_new_streak := 1;
    END IF;

    UPDATE student_streaks
    SET current_streak = v_new_streak,
        highest_streak = GREATEST(v_prev_streak.highest_streak, v_new_streak),
        last_activity_date = CURRENT_DATE
    WHERE student_id = NEW.student_id;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_touch_student_activity_on_enroll ON course_enrollments;
CREATE TRIGGER trg_touch_student_activity_on_enroll
AFTER INSERT ON course_enrollments
FOR EACH ROW
EXECUTE FUNCTION fn_touch_student_activity();

-- 2.2 Gửi thông báo khi hoàn thành khóa học
CREATE OR REPLACE FUNCTION fn_notify_course_completion()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.progress >= 100 AND COALESCE(OLD.progress, 0) < 100 THEN
        INSERT INTO notification_users (
            user_id,
            title,
            message
        )
        SELECT NEW.student_id,
               'Hoàn thành khóa học',
               FORMAT('Chúc mừng! Bạn đã hoàn thành khóa học "%s".', gc.title)
        FROM general_courses AS gc
        WHERE gc.course_id = NEW.course_id;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_notify_course_completion ON course_enrollments;
CREATE TRIGGER trg_notify_course_completion
AFTER UPDATE OF progress ON course_enrollments
FOR EACH ROW
WHEN (OLD.progress IS DISTINCT FROM NEW.progress)
EXECUTE FUNCTION fn_notify_course_completion();
-- 2.3 Cập nhật thẻ updated_at tự động
CREATE OR REPLACE FUNCTION fn_update_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_auto_update_users_timestamp ON users;
CREATE TRIGGER trg_auto_update_users_timestamp
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION fn_update_timestamp();

DROP TRIGGER IF EXISTS trg_auto_update_courses_timestamp ON general_courses;
CREATE TRIGGER trg_auto_update_courses_timestamp
BEFORE UPDATE ON general_courses
FOR EACH ROW
EXECUTE FUNCTION fn_update_timestamp();

-- =========================================================
-- 3) FUNCTIONS/PROCEDURES NGHIỆP VỤ
-- =========================================================

-- 3.1 Tìm học viên theo từ khóa
CREATE OR REPLACE FUNCTION fn_search_students(p_keyword TEXT DEFAULT NULL)
RETURNS TABLE (
    student_id UUID,
    username VARCHAR(50),
    full_name VARCHAR(100),
    grade_level VARCHAR(50),
    school_name VARCHAR(150),
    created_at TIMESTAMPTZ
)
LANGUAGE sql
STABLE
AS $$
SELECT s.user_id AS student_id,
       u.username,
       up.full_name,
       s.grade_level,
       s.school_name,
       u.created_at
FROM students AS s
JOIN users AS u
     ON u.user_id = s.user_id
LEFT JOIN user_profiles AS up
       ON up.user_id = s.user_id
WHERE u.is_deleted = FALSE
  AND (NULLIF(BTRIM(p_keyword), '') IS NULL
       OR u.username ILIKE '%' || BTRIM(p_keyword) || '%'
       OR COALESCE(up.full_name, '') ILIKE '%' || BTRIM(p_keyword) || '%'
       OR COALESCE(s.school_name, '') ILIKE '%' || BTRIM(p_keyword) || '%')
ORDER BY u.created_at DESC;
$$;

-- 3.2 Tìm khóa học nâng cao
CREATE OR REPLACE FUNCTION fn_search_courses_advanced(
    p_keyword TEXT DEFAULT NULL,
    p_category_id INTEGER DEFAULT NULL,
    p_status TEXT DEFAULT NULL
)
RETURNS TABLE (
    course_id UUID,
    title VARCHAR(255),
    category_name VARCHAR(100),
    teacher_id UUID,
    teacher_username VARCHAR(50),
    visibility_status VARCHAR(20),
    total_students BIGINT,
    avg_progress NUMERIC(5, 2)
)
LANGUAGE sql
STABLE
AS $$
SELECT gc.course_id,
       gc.title,
       gcc.name AS category_name,
       gc.teacher_id,
       tu.username AS teacher_username,
       gc.visibility_status,
       COUNT(ce.enrollment_id)::BIGINT AS total_students,
       COALESCE(ROUND(AVG(ce.progress)::numeric, 2), 0.00)::numeric(5, 2) AS avg_progress
FROM general_courses AS gc
JOIN general_course_categories AS gcc
     ON gcc.category_id = gc.category_id
JOIN teachers AS t
     ON t.user_id = gc.teacher_id
JOIN users AS tu
     ON tu.user_id = t.user_id
LEFT JOIN course_enrollments AS ce
       ON ce.course_id = gc.course_id
WHERE gc.is_deleted = FALSE
  AND (NULLIF(BTRIM(p_keyword), '') IS NULL
       OR gc.title ILIKE '%' || BTRIM(p_keyword) || '%')
  AND (p_category_id IS NULL OR gc.category_id = p_category_id)
  AND (NULLIF(BTRIM(p_status), '') IS NULL OR gc.visibility_status = UPPER(BTRIM(p_status)))
GROUP BY gc.course_id,
         gc.title,
         gcc.name,
         gc.teacher_id,
         tu.username,
         gc.visibility_status
ORDER BY total_students DESC, gc.title ASC;
$$;

-- 3.3 Procedure enroll học viên vào khóa học
CREATE OR REPLACE PROCEDURE sp_enroll_student(
    p_student_id UUID,
    p_course_id UUID
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_student_id IS NULL OR p_course_id IS NULL THEN
        RAISE EXCEPTION 'student_id và course_id không được NULL';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM students
        WHERE user_id = p_student_id
    ) THEN
        RAISE EXCEPTION 'Không tìm thấy học viên: %', p_student_id;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM general_courses
        WHERE course_id = p_course_id
    ) THEN
        RAISE EXCEPTION 'Không tìm thấy khóa học: %', p_course_id;
    END IF;

    INSERT INTO course_enrollments (
        student_id,
        course_id,
        progress
    )
    VALUES (
        p_student_id,
        p_course_id,
        0.00
    )
    ON CONFLICT (student_id, course_id) DO NOTHING;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Học viên % đã đăng ký khóa học % trước đó', p_student_id, p_course_id;
    END IF;
END;
$$;

-- 3.4 Procedure cập nhật tiến độ học
CREATE OR REPLACE PROCEDURE sp_update_course_progress(
    p_student_id UUID,
    p_course_id UUID,
    p_progress NUMERIC
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_student_id IS NULL OR p_course_id IS NULL THEN
        RAISE EXCEPTION 'student_id và course_id không được NULL';
    END IF;

    IF p_progress IS NULL OR p_progress < 0 OR p_progress > 100 THEN
        RAISE EXCEPTION 'progress phải nằm trong khoảng 0..100, nhận: %', p_progress;
    END IF;

    UPDATE course_enrollments
    SET progress = ROUND(p_progress, 2)
    WHERE student_id = p_student_id
      AND course_id = p_course_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Không tìm thấy enrollment cho student_id=% và course_id=%',
            p_student_id,
            p_course_id;
    END IF;
END;
$$;
-- 3.5 Procedure Xóa mềm Tài khoản (Học viên/Giáo viên)
CREATE OR REPLACE PROCEDURE sp_soft_delete_user(
    p_user_id UUID
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE users 
    SET is_deleted = TRUE 
    WHERE user_id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Không tìm thấy user_id: %', p_user_id;
    END IF;
END;
$$;

-- 3.6 Procedure Xóa mềm Khóa học
CREATE OR REPLACE PROCEDURE sp_soft_delete_course(
    p_course_id UUID
)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE general_courses 
    SET is_deleted = TRUE 
    WHERE course_id = p_course_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Không tìm thấy course_id: %', p_course_id;
    END IF;
END;
$$;

-- =========================================================
-- 4) MẪU TRANSACTION DEMO (END-TO-END)
-- =========================================================
-- Lưu ý:
-- - Đây là mẫu chạy tay cho buổi demo.
-- - Cần có dữ liệu tối thiểu trong users/students/teachers/general_courses/general_course_lessons.
-- - Thay UUID theo dữ liệu thực tế của bạn trước khi chạy.

-- 4.1 Transaction enroll học viên vào khóa học
-- BEGIN;
-- CALL sp_enroll_student(
--     '00000000-0000-0000-0000-000000000101'::uuid, -- p_student_id
--     '00000000-0000-0000-0000-000000000201'::uuid  -- p_course_id
-- );
-- COMMIT;

-- 4.2 Transaction cập nhật tiến độ + tạo bình luận để mô phỏng hoạt động học tập
-- BEGIN;
-- CALL sp_update_course_progress(
--     '00000000-0000-0000-0000-000000000101'::uuid, -- p_student_id
--     '00000000-0000-0000-0000-000000000201'::uuid, -- p_course_id
--     100.00                                          -- p_progress
-- );
--
-- INSERT INTO comments (
--     lesson_id,
--     user_id,
--     content
-- )
-- VALUES (
--     '00000000-0000-0000-0000-000000000301'::uuid, -- lesson_id
--     '00000000-0000-0000-0000-000000000101'::uuid, -- user_id (student)
--     'Bài học rất dễ hiểu, mình đã hoàn thành khóa học.'
-- );
-- COMMIT;

-- =========================================================
-- 5) TRUY VẤN NHANH PHỤC VỤ BÁO CÁO
-- =========================================================
-- SELECT * FROM vw_enrollments_by_day;
-- SELECT * FROM vw_top_courses;
-- SELECT * FROM vw_top_active_students;
-- SELECT * FROM vw_user_course_progress;
