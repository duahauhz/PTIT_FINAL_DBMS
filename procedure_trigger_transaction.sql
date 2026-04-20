-- =========================================================
-- PostgreSQL Utility Script (chuáº©n hÃ³a theo ERD.sql)
-- Má»¥c tiÃªu:
-- 1) Táº¡o view phá»¥c vá»¥ bÃ¡o cÃ¡o demo.
-- 2) Táº¡o trigger/function nghiá»‡p vá»¥ há»c táº­p.
-- 3) Táº¡o procedure/function tÃ¬m kiáº¿m vÃ  cáº­p nháº­t tiáº¿n Ä‘á»™.
-- 4) Cung cáº¥p máº«u transaction demo end-to-end (khÃ´ng payment/refund).
--
-- YÃªu cáº§u: cháº¡y ERD.sql trÆ°á»›c khi cháº¡y file nÃ y.
-- =========================================================

SET search_path TO public;

-- =========================================================
-- 0) ENTITY LOG HE THONG
-- =========================================================
CREATE TABLE IF NOT EXISTS log (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_log_created_at ON log (created_at);

-- =========================================================
-- 1) VIEW BÃO CÃO DEMO
-- =========================================================

-- 1.1 VIEW BÁO CÁO HỌC TẬP TỔNG QUÁT
DROP VIEW IF EXISTS vw_enrollments_by_day;
DROP VIEW IF EXISTS vw_top_courses;
DROP VIEW IF EXISTS vw_top_active_students;
DROP VIEW IF EXISTS vw_user_course_progress;

CREATE OR REPLACE VIEW vw_student_progress_report AS
SELECT 
    up.full_name AS student_name,
    u.email,
    s.school_name,
    c.title AS course_title,
    ce.progress,
    ce.enrolled_at,
    CASE 
        WHEN ce.progress = 100 THEN 'Hoàn thành'
        WHEN ce.progress > 0 THEN 'Đang học'
        ELSE 'Mới đăng ký'
    END AS learning_status
FROM users u
JOIN user_profiles up ON u.user_id = up.user_id
JOIN students s ON u.user_id = s.user_id
JOIN course_enrollments ce ON s.user_id = ce.student_id
JOIN general_courses c ON ce.course_id = c.course_id
WHERE u.is_deleted = FALSE;

-- 1.2 THỐNG KÊ HIỆU NĂNG KHÓA HỌC
CREATE OR REPLACE VIEW vw_course_analytics AS
SELECT 
    c.course_id,
    c.title AS course_title,
    up.full_name AS teacher_name,
    COUNT(ce.enrollment_id) AS total_students,
    ROUND(AVG(ce.progress), 2) AS avg_progress,
    (SELECT ROUND(AVG(rating), 1) FROM user_feedbacks WHERE context LIKE '%' || c.title || '%') AS avg_rating
FROM general_courses c
JOIN teachers t ON c.teacher_id = t.user_id
JOIN user_profiles up ON t.user_id = up.user_id
LEFT JOIN course_enrollments ce ON c.course_id = ce.course_id
WHERE c.is_deleted = FALSE
GROUP BY c.course_id, c.title, up.full_name;

-- 1.3 VIEW HIỂN THỊ BẢNG XẾP HẠNG
CREATE OR REPLACE VIEW vw_top_learners_leaderboard AS
SELECT 
    up.full_name,
    up.avatar_url,
    ss.current_streak,
    ss.highest_streak,
    (SELECT COUNT(*) FROM user_achievements ua WHERE ua.user_id = s.user_id) AS total_achievements
FROM students s
JOIN user_profiles up ON s.user_id = up.user_id
JOIN student_streaks ss ON s.user_id = ss.student_id
ORDER BY ss.current_streak DESC, total_achievements DESC;
-- =========================================================
-- 2) TRIGGER FUNCTIONS + TRIGGERS
-- =========================================================

-- 2.1 Cháº¡m hoáº¡t Ä‘á»™ng há»c viÃªn sau khi enroll
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
        INSERT INTO log (action)
        VALUES (
            FORMAT(
                'trg_touch_student_activity_on_enroll: student_id=%s, current_streak=%s',
                NEW.student_id,
                1
            )
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

    INSERT INTO log (action)
    VALUES (
        FORMAT(
            'trg_touch_student_activity_on_enroll: student_id=%s, current_streak=%s',
            NEW.student_id,
            v_new_streak
        )
    );

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_touch_student_activity_on_enroll ON course_enrollments;
CREATE TRIGGER trg_touch_student_activity_on_enroll
AFTER INSERT ON course_enrollments
FOR EACH ROW
EXECUTE FUNCTION fn_touch_student_activity();

-- 2.2 Gá»­i thÃ´ng bÃ¡o khi hoÃ n thÃ nh khÃ³a há»c
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
               'HoÃ n thÃ nh khÃ³a há»c',
               FORMAT('ChÃºc má»«ng! Báº¡n Ä‘Ã£ hoÃ n thÃ nh khÃ³a há»c "%s".', gc.title)
        FROM general_courses AS gc
        WHERE gc.course_id = NEW.course_id;

        INSERT INTO log (action)
        VALUES (
            FORMAT(
                'trg_notify_course_completion: student_id=%s, course_id=%s, progress=%s',
                NEW.student_id,
                NEW.course_id,
                NEW.progress
            )
        );
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
-- 2.3 Cáº­p nháº­t tháº» updated_at tá»± Ä‘á»™ng
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
-- 3) FUNCTIONS/PROCEDURES NGHIá»†P Vá»¤
-- =========================================================

-- 3.1 TÃ¬m há»c viÃªn theo tá»« khÃ³a
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

-- 3.2 TÃ¬m khÃ³a há»c nÃ¢ng cao
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

-- 3.3 Procedure enroll há»c viÃªn vÃ o khÃ³a há»c
CREATE OR REPLACE PROCEDURE sp_enroll_student(
    p_student_id UUID,
    p_course_id UUID
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_student_id IS NULL OR p_course_id IS NULL THEN
        RAISE EXCEPTION 'student_id vÃ  course_id khÃ´ng Ä‘Æ°á»£c NULL';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM students
        WHERE user_id = p_student_id
    ) THEN
        RAISE EXCEPTION 'KhÃ´ng tÃ¬m tháº¥y há»c viÃªn: %', p_student_id;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM general_courses
        WHERE course_id = p_course_id
    ) THEN
        RAISE EXCEPTION 'KhÃ´ng tÃ¬m tháº¥y khÃ³a há»c: %', p_course_id;
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
        RAISE EXCEPTION 'Há»c viÃªn % Ä‘Ã£ Ä‘Äƒng kÃ½ khÃ³a há»c % trÆ°á»›c Ä‘Ã³', p_student_id, p_course_id;
    END IF;

    INSERT INTO log (action)
    VALUES (
        FORMAT(
            'sp_enroll_student: student_id=%s, course_id=%s',
            p_student_id,
            p_course_id
        )
    );
END;
$$;

-- 3.4 Procedure cáº­p nháº­t tiáº¿n Ä‘á»™ há»c
CREATE OR REPLACE PROCEDURE sp_update_course_progress(
    p_student_id UUID,
    p_course_id UUID,
    p_progress NUMERIC
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_student_id IS NULL OR p_course_id IS NULL THEN
        RAISE EXCEPTION 'student_id vÃ  course_id khÃ´ng Ä‘Æ°á»£c NULL';
    END IF;

    IF p_progress IS NULL OR p_progress < 0 OR p_progress > 100 THEN
        RAISE EXCEPTION 'progress pháº£i náº±m trong khoáº£ng 0..100, nháº­n: %', p_progress;
    END IF;

    UPDATE course_enrollments
    SET progress = ROUND(p_progress, 2)
    WHERE student_id = p_student_id
      AND course_id = p_course_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'KhÃ´ng tÃ¬m tháº¥y enrollment cho student_id=% vÃ  course_id=%',
            p_student_id,
            p_course_id;
    END IF;

    INSERT INTO log (action)
    VALUES (
        FORMAT(
            'sp_update_course_progress: student_id=%s, course_id=%s, progress=%s',
            p_student_id,
            p_course_id,
            ROUND(p_progress, 2)
        )
    );
END;
$$;
-- 3.5 Procedure XÃ³a má»m TÃ i khoáº£n (Há»c viÃªn/GiÃ¡o viÃªn)
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
        RAISE EXCEPTION 'KhÃ´ng tÃ¬m tháº¥y user_id: %', p_user_id;
    END IF;

    INSERT INTO log (action)
    VALUES (
        FORMAT('sp_soft_delete_user: user_id=%s', p_user_id)
    );
END;
$$;

-- 3.6 Procedure XÃ³a má»m KhÃ³a há»c
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
        RAISE EXCEPTION 'KhÃ´ng tÃ¬m tháº¥y course_id: %', p_course_id;
    END IF;

    INSERT INTO log (action)
    VALUES (
        FORMAT('sp_soft_delete_course: course_id=%s', p_course_id)
    );
END;
$$;

-- =========================================================
-- 4) MáºªU TRANSACTION DEMO (END-TO-END)
-- =========================================================
-- LÆ°u Ã½:
-- - ÄÃ¢y lÃ  máº«u cháº¡y tay cho buá»•i demo.
-- - Cáº§n cÃ³ dá»¯ liá»‡u tá»‘i thiá»ƒu trong users/students/teachers/general_courses/general_course_lessons.
-- - Thay UUID theo dá»¯ liá»‡u thá»±c táº¿ cá»§a báº¡n trÆ°á»›c khi cháº¡y.

-- 4.1 Transaction enroll há»c viÃªn vÃ o khÃ³a há»c
-- BEGIN;
-- CALL sp_enroll_student(
--     '00000000-0000-0000-0000-000000000101'::uuid, -- p_student_id
--     '00000000-0000-0000-0000-000000000201'::uuid  -- p_course_id
-- );`
-- COMMIT;

-- 4.2 Transaction cáº­p nháº­t tiáº¿n Ä‘á»™ + táº¡o bÃ¬nh luáº­n Ä‘á»ƒ mÃ´ phá»ng hoáº¡t Ä‘á»™ng há»c táº­p
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
--     'BÃ i há»c ráº¥t dá»… hiá»ƒu, mÃ¬nh Ä‘Ã£ hoÃ n thÃ nh khÃ³a há»c.'
-- );
-- COMMIT;

-- =========================================================
-- 5) TRUY Váº¤N NHANH PHá»¤C Vá»¤ BÃO CÃO
-- =========================================================
-- SELECT course_title, progress, learning_status 
-- FROM vw_student_progress_report
-- WHERE email = 'minh.student@signlearn.local'
-- ORDER BY progress DESC;
--
-- SELECT course_title, total_students, avg_rating
-- FROM vw_course_analytics
-- WHERE avg_rating >= 4.0 
--   AND teacher_name = 'Nguyen Thi Lan';
--
-- SELECT full_name, current_streak, total_achievements
-- FROM vw_top_learners_leaderboard
-- ORDER BY current_streak DESC, total_achievements DESC
-- LIMIT 5;
-- SELECT action, created_at FROM log ORDER BY created_at DESC LIMIT 20;

