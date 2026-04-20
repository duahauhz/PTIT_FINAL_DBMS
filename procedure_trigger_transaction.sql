-- =========================================================
-- PostgreSQL Utility Script (chuáº©n hÃ³a theo ERD.sql)
-- Má»¥c tiÃªu:
-- 1) Táº¡o view phá»¥c vá»¥ bÃ¡o cÃ¡o demo.
-- 2) Táº¡o trigger/function nghiá»‡p vá»¥ há»c táº­p.
-- 3) Táº¡o procedure/function tÃ¬m kiáº¿m vÃ  cáº­p nháº­t tiáº¿n Ä‘á»™.
-- 4) Cung cáº¥p máº«u transaction demo end-to-end + chuyen tien vi admin.
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

-- 0.1 BO SUNG HE THONG VI GIAO DICH (WALLET + TRANSACTION LOG)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active';

UPDATE users
SET status = 'active'
WHERE status IS NULL;

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

ALTER TABLE users
    ALTER COLUMN status SET NOT NULL;

CREATE TABLE IF NOT EXISTS wallets (
    user_id UUID PRIMARY KEY,
    balance NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_wallets_balance_non_negative CHECK (balance >= 0),
    CONSTRAINT fk_wallets_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

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
);

CREATE TABLE IF NOT EXISTS transaction_action_logs (
    action_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id UUID,
    action_type VARCHAR(40) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE transaction_action_logs
    DROP CONSTRAINT IF EXISTS fk_tx_action_logs_transaction;

CREATE INDEX IF NOT EXISTS idx_wallets_balance ON wallets (balance);
CREATE INDEX IF NOT EXISTS idx_transaction_logs_created_at ON transaction_logs (created_at);
CREATE INDEX IF NOT EXISTS idx_transaction_logs_from_wallet ON transaction_logs (from_wallet_user_id);
CREATE INDEX IF NOT EXISTS idx_transaction_logs_to_wallet ON transaction_logs (to_wallet_user_id);
CREATE INDEX IF NOT EXISTS idx_tx_action_logs_created_at ON transaction_action_logs (created_at);
CREATE INDEX IF NOT EXISTS idx_tx_action_logs_tx_id ON transaction_action_logs (transaction_id);

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
);

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

-- 2.3 AUTOMATION TRIGGER: TỰ ĐỘNG CẬP NHẬT UPDATED_AT
CREATE OR REPLACE FUNCTION fn_update_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

-- Don dep cac trigger ten cu/ten moi de tranh trung lap.
DROP TRIGGER IF EXISTS trg_users_update_timestamp ON users;
DROP TRIGGER IF EXISTS trg_auto_update_users_timestamp ON users;
CREATE TRIGGER trg_auto_update_users_timestamp
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION fn_update_timestamp();

DROP TRIGGER IF EXISTS trg_courses_update_timestamp ON general_courses;
DROP TRIGGER IF EXISTS trg_auto_update_courses_timestamp ON general_courses;
CREATE TRIGGER trg_auto_update_courses_timestamp
BEFORE UPDATE ON general_courses
FOR EACH ROW
EXECUTE FUNCTION fn_update_timestamp();

DROP TRIGGER IF EXISTS trg_wallets_update_timestamp ON wallets;
DROP TRIGGER IF EXISTS trg_auto_update_wallets_timestamp ON wallets;
CREATE TRIGGER trg_auto_update_wallets_timestamp
BEFORE UPDATE ON wallets
FOR EACH ROW
EXECUTE FUNCTION fn_update_timestamp();

-- 2.4 DATA INTEGRITY TRIGGER: TỰ ĐỘNG KHỞI TẠO STREAK CHO HỌC VIÊN MỚI
CREATE OR REPLACE FUNCTION fn_init_student_streak()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO student_streaks (
        student_id,
        current_streak,
        highest_streak,
        last_activity_date
    )
    VALUES (
        NEW.user_id,
        0,
        0,
        NULL
    )
    ON CONFLICT (student_id) DO NOTHING;

    INSERT INTO log (action)
    VALUES (
        FORMAT(
            'trg_after_insert_student: auto init streak for student_id=%s',
            NEW.user_id
        )
    );

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_after_insert_student ON students;
CREATE TRIGGER trg_after_insert_student
AFTER INSERT ON students
FOR EACH ROW
EXECUTE FUNCTION fn_init_student_streak();

-- 2.5 BUSINESS RULE TRIGGER: CHẶN PUBLISH KHÓA HỌC RỖNG (CHƯA CÓ MODULE)
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

        INSERT INTO log (action)
        VALUES (
            FORMAT(
                'trg_before_publish_course: allow publish course_id=%s with module_count=%s',
                NEW.course_id,
                module_count
            )
        );
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_before_publish_course ON general_courses;
CREATE TRIGGER trg_before_publish_course
BEFORE UPDATE OF visibility_status ON general_courses
FOR EACH ROW
EXECUTE FUNCTION fn_validate_course_publish();

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

-- 3.7 FUNCTION TRANSACTION: CHUYEN TIEN VE VI ADMIN
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

-- 4.3 Transaction chuyen tien hoc phi vao vi ADMIN
-- BEGIN;
-- SELECT *
-- FROM fn_transfer_to_admin_wallet(
--     '30000000-0000-0000-0000-000000000001'::uuid, -- vi nguon (hoc vien/giao vien)
--     50.00                                          -- so tien
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
-- SELECT w.user_id, u.username, r.role_name, u.status, w.balance
-- FROM wallets w
-- JOIN users u ON u.user_id = w.user_id
-- JOIN roles r ON r.role_id = u.role_id
-- ORDER BY r.role_name, u.username;
-- SELECT transaction_id, from_wallet_user_id, to_wallet_user_id, amount, status, message, created_at
-- FROM transaction_logs
-- ORDER BY created_at DESC
-- LIMIT 20;
-- SELECT action_type, message, created_at
-- FROM transaction_action_logs
-- ORDER BY created_at DESC
-- LIMIT 20;
-- SELECT action, created_at FROM log ORDER BY created_at DESC LIMIT 20;

-- =========================================================
-- 6) DEMO TRIGGER THEO TUNG SESSION (CHAY RIENG TUNG KHOI)
-- =========================================================

-- -----------------------------------------------------------------
-- SESSION 1 - AUTOMATION TRIGGER: updated_at tu dong doi khi UPDATE
-- Trigger ngam: trg_auto_update_users_timestamp / trg_auto_update_courses_timestamp
-- -----------------------------------------------------------------
-- SELECT user_id, username, updated_at
-- FROM users
-- WHERE user_id = '30000000-0000-0000-0000-000000000001';
--
-- UPDATE users
-- SET username = 'student_minh_pro'
-- WHERE user_id = '30000000-0000-0000-0000-000000000001';
--
-- SELECT user_id, username, updated_at
-- FROM users
-- WHERE user_id = '30000000-0000-0000-0000-000000000001';
-- -- Quan sat: updated_at thay doi du ban khong set bang tay.

-- -----------------------------------------------------------------
-- SESSION 2 - DATA INTEGRITY TRIGGER: tu dong tao streak cho hoc vien moi
-- Trigger ngam: trg_after_insert_student -> fn_init_student_streak
-- -----------------------------------------------------------------
-- INSERT INTO users (user_id, username, password_hash, email, role_id)
-- VALUES (
--     '30000000-0000-0000-0000-000000000599',
--     'demo_new_user_01',
--     'hash123',
--     'newuser@local_01',
--     (SELECT role_id FROM roles WHERE role_name = 'STUDENT')
-- )
-- ON CONFLICT (user_id) DO UPDATE
-- SET username = EXCLUDED.username,
--     password_hash = EXCLUDED.password_hash,
--     email = EXCLUDED.email,
--     role_id = EXCLUDED.role_id;
--
-- SELECT *
-- FROM student_streaks
-- WHERE student_id = '30000000-0000-0000-0000-000000000599';
--
-- INSERT INTO students (user_id, grade_level, school_name)
-- VALUES ('30000000-0000-0000-0000-000000000599', 'Grade 10', 'Demo High School')
-- ON CONFLICT (user_id) DO UPDATE
-- SET grade_level = EXCLUDED.grade_level,
--     school_name = EXCLUDED.school_name;
--
-- SELECT *
-- FROM student_streaks
-- WHERE student_id = '30000000-0000-0000-0000-000000000599';
-- -- Quan sat: dong streak duoc tao/giu dong bo tu trigger, khong can INSERT tay.

-- -----------------------------------------------------------------
-- SESSION 3 - BUSINESS RULE TRIGGER: chan publish khoa hoc rong
-- Trigger ngam: trg_before_publish_course -> fn_validate_course_publish
-- -----------------------------------------------------------------
-- INSERT INTO general_courses (course_id, teacher_id, category_id, title, visibility_status)
-- VALUES (
--     '40000000-0000-0000-0000-000000000678',
--     '20000000-0000-0000-0000-000000000001',
--     1,
--     'Khoa hoc Test Trigger',
--     'DRAFT'
-- )
-- ON CONFLICT (course_id) DO UPDATE
-- SET teacher_id = EXCLUDED.teacher_id,
--     category_id = EXCLUDED.category_id,
--     title = EXCLUDED.title,
--     visibility_status = EXCLUDED.visibility_status;
--
-- -- Buoc 1 (co chu dich): publish khi chua co module -> trigger se vang loi va chan update
-- -- UPDATE general_courses
-- -- SET visibility_status = 'PUBLISHED'
-- -- WHERE course_id = '40000000-0000-0000-0000-000000000678';
--
-- -- Buoc 2: bo sung module hop le, sau do publish lai
-- INSERT INTO general_course_modules (module_id, course_id, title, order_index)
-- VALUES ('41000000-0000-0000-0000-000000000999', '40000000-0000-0000-0000-000000000678', 'Module 1: Mo dau', 1)
-- ON CONFLICT (module_id) DO UPDATE
-- SET course_id = EXCLUDED.course_id,
--     title = EXCLUDED.title,
--     order_index = EXCLUDED.order_index;
--
-- UPDATE general_courses
-- SET visibility_status = 'PUBLISHED'
-- WHERE course_id = '40000000-0000-0000-0000-000000000678';
--
-- SELECT course_id, title, visibility_status
-- FROM general_courses
-- WHERE course_id = '40000000-0000-0000-0000-000000000678';
-- -- Quan sat: trigger cho phep publish khi da co module.

