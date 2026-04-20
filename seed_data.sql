-- Seed data cho toan bo schema
-- Chay sau khi da chay ERD.sql
-- Script co tinh idempotent o muc co ban (chay lai khong tao trung phan lon du lieu)
BEGIN;
-- =========================================================
-- 1) ROLES
-- =========================================================
INSERT INTO roles (role_name)
VALUES ('ADMIN'),
    ('TEACHER'),
    ('STUDENT') ON CONFLICT (role_name) DO NOTHING;
-- =========================================================
-- 2) USERS + PROFILES + STUDENTS/TEACHERS + SESSIONS
-- =========================================================
INSERT INTO users (
        user_id,
        username,
        password_hash,
        email,
        role_id,
        created_at,
        updated_at,
        is_deleted
    )
VALUES (
        '10000000-0000-0000-0000-000000000001',
        'admin_root',
        '$2b$12$adminplaceholderhash0000000000000000000000000000000',
        'admin@signlearn.local',
        (
            SELECT role_id
            FROM roles
            WHERE role_name = 'ADMIN'
        ),
        CURRENT_TIMESTAMP - INTERVAL '45 days',
        CURRENT_TIMESTAMP - INTERVAL '45 days',
        FALSE
    ),
    (
        '20000000-0000-0000-0000-000000000001',
        'teacher_lan',
        '$2b$12$teacherlanplaceholderhash000000000000000000000000000',
        'lan.teacher@signlearn.local',
        (
            SELECT role_id
            FROM roles
            WHERE role_name = 'TEACHER'
        ),
        CURRENT_TIMESTAMP - INTERVAL '30 days',
        CURRENT_TIMESTAMP - INTERVAL '30 days',
        FALSE
    ),
    (
        '20000000-0000-0000-0000-000000000002',
        'teacher_huy',
        '$2b$12$teacherhuyplaceholderhash000000000000000000000000000',
        'huy.teacher@signlearn.local',
        (
            SELECT role_id
            FROM roles
            WHERE role_name = 'TEACHER'
        ),
        CURRENT_TIMESTAMP - INTERVAL '28 days',
        CURRENT_TIMESTAMP - INTERVAL '28 days',
        FALSE
    ),
    (
        '30000000-0000-0000-0000-000000000001',
        'student_minh',
        '$2b$12$studentminhplaceholderhash00000000000000000000000000',
        'minh.student@signlearn.local',
        (
            SELECT role_id
            FROM roles
            WHERE role_name = 'STUDENT'
        ),
        CURRENT_TIMESTAMP - INTERVAL '20 days',
        CURRENT_TIMESTAMP - INTERVAL '20 days',
        FALSE
    ),
    (
        '30000000-0000-0000-0000-000000000002',
        'student_an',
        '$2b$12$studentanplaceholderhash0000000000000000000000000000',
        'an.student@signlearn.local',
        (
            SELECT role_id
            FROM roles
            WHERE role_name = 'STUDENT'
        ),
        CURRENT_TIMESTAMP - INTERVAL '18 days',
        CURRENT_TIMESTAMP - INTERVAL '18 days',
        FALSE
    ),
    (
        '30000000-0000-0000-0000-000000000003',
        'student_hoa',
        '$2b$12$studenthoaplaceholderhash000000000000000000000000000',
        'hoa.student@signlearn.local',
        (
            SELECT role_id
            FROM roles
            WHERE role_name = 'STUDENT'
        ),
        CURRENT_TIMESTAMP - INTERVAL '16 days',
        CURRENT_TIMESTAMP - INTERVAL '16 days',
        FALSE
    ) ON CONFLICT (user_id) DO
UPDATE
SET username = EXCLUDED.username,
    password_hash = EXCLUDED.password_hash,
    email = EXCLUDED.email,
    role_id = EXCLUDED.role_id,
    updated_at = EXCLUDED.updated_at,
    is_deleted = EXCLUDED.is_deleted;
INSERT INTO user_profiles (
        user_id,
        full_name,
        avatar_url,
        phone_number,
        date_of_birth
    )
VALUES (
        '10000000-0000-0000-0000-000000000001',
        'System Admin',
        'https://cdn.local/avatar/admin.png',
        '0901000001',
        DATE '1990-01-01'
    ),
    (
        '20000000-0000-0000-0000-000000000001',
        'Nguyen Thi Lan',
        'https://cdn.local/avatar/lan.png',
        '0902000001',
        DATE '1988-05-10'
    ),
    (
        '20000000-0000-0000-0000-000000000002',
        'Tran Quang Huy',
        'https://cdn.local/avatar/huy.png',
        '0902000002',
        DATE '1987-09-15'
    ),
    (
        '30000000-0000-0000-0000-000000000001',
        'Le Minh',
        'https://cdn.local/avatar/minh.png',
        '0903000001',
        DATE '2011-03-21'
    ),
    (
        '30000000-0000-0000-0000-000000000002',
        'Pham Ngoc An',
        'https://cdn.local/avatar/an.png',
        '0903000002',
        DATE '2010-11-08'
    ),
    (
        '30000000-0000-0000-0000-000000000003',
        'Do Thu Hoa',
        'https://cdn.local/avatar/hoa.png',
        '0903000003',
        DATE '2012-07-19'
    ) ON CONFLICT (user_id) DO
UPDATE
SET full_name = EXCLUDED.full_name,
    avatar_url = EXCLUDED.avatar_url,
    phone_number = EXCLUDED.phone_number,
    date_of_birth = EXCLUDED.date_of_birth;
INSERT INTO teachers (user_id, bio, department)
VALUES (
        '20000000-0000-0000-0000-000000000001',
        'Giao vien ngon ngu ky hieu, 8 nam kinh nghiem.',
        'Khoa Giao tiep'
    ),
    (
        '20000000-0000-0000-0000-000000000002',
        'Phu trach noi dung microlearning va thuc hanh.',
        'Khoa Dao tao'
    ) ON CONFLICT (user_id) DO
UPDATE
SET bio = EXCLUDED.bio,
    department = EXCLUDED.department;
INSERT INTO students (user_id, grade_level, school_name)
VALUES (
        '30000000-0000-0000-0000-000000000001',
        'Grade 6',
        'THCS Nguyen Du'
    ),
    (
        '30000000-0000-0000-0000-000000000002',
        'Grade 7',
        'THCS Tran Phu'
    ),
    (
        '30000000-0000-0000-0000-000000000003',
        'Grade 5',
        'TH Le Quy Don'
    ) ON CONFLICT (user_id) DO
UPDATE
SET grade_level = EXCLUDED.grade_level,
    school_name = EXCLUDED.school_name;
INSERT INTO authentication_sessions (
        session_id,
        user_id,
        session_key,
        otp_code,
        expires_at,
        created_at
    )
VALUES (
        '70000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000001',
        'sess_minh_001',
        '123456',
        CURRENT_TIMESTAMP + INTERVAL '2 days',
        CURRENT_TIMESTAMP - INTERVAL '1 day'
    ),
    (
        '70000000-0000-0000-0000-000000000002',
        '30000000-0000-0000-0000-000000000002',
        'sess_an_001',
        '654321',
        CURRENT_TIMESTAMP + INTERVAL '2 days',
        CURRENT_TIMESTAMP - INTERVAL '1 day'
    ),
    (
        '70000000-0000-0000-0000-000000000003',
        '20000000-0000-0000-0000-000000000001',
        'sess_lan_001',
        NULL,
        CURRENT_TIMESTAMP + INTERVAL '3 days',
        CURRENT_TIMESTAMP - INTERVAL '2 days'
    ),
    (
        '70000000-0000-0000-0000-000000000004',
        '20000000-0000-0000-0000-000000000002',
        'sess_huy_001',
        NULL,
        CURRENT_TIMESTAMP + INTERVAL '3 days',
        CURRENT_TIMESTAMP - INTERVAL '2 days'
    ) ON CONFLICT (session_id) DO
UPDATE
SET user_id = EXCLUDED.user_id,
    session_key = EXCLUDED.session_key,
    otp_code = EXCLUDED.otp_code,
    expires_at = EXCLUDED.expires_at,
    created_at = EXCLUDED.created_at;
-- =========================================================
-- 3) DICTIONARY
-- =========================================================
INSERT INTO dictionary_categories (name, description)
VALUES ('Chao hoi', 'Cac tu ky hieu chao hoi co ban'),
    ('Hoc duong', 'Tu vung dung trong truong hoc'),
    ('Sinh hoat', 'Tu vung giao tiep hang ngay') ON CONFLICT (name) DO
UPDATE
SET description = EXCLUDED.description;
INSERT INTO dictionary_entries (entry_id, category_id, word, meaning, updated_at, is_deleted)
VALUES (
        '80000000-0000-0000-0000-000000000001',
        (
            SELECT category_id
            FROM dictionary_categories
            WHERE name = 'Chao hoi'
        ),
        'Xin chao',
        'Loi chao than thien',
        CURRENT_TIMESTAMP - INTERVAL '5 days',
        FALSE
    ),
    (
        '80000000-0000-0000-0000-000000000002',
        (
            SELECT category_id
            FROM dictionary_categories
            WHERE name = 'Chao hoi'
        ),
        'Cam on',
        'Bieu dat su biet on',
        CURRENT_TIMESTAMP - INTERVAL '4 days',
        FALSE
    ),
    (
        '80000000-0000-0000-0000-000000000003',
        (
            SELECT category_id
            FROM dictionary_categories
            WHERE name = 'Hoc duong'
        ),
        'Lop hoc',
        'Chi khong gian hoc tap',
        CURRENT_TIMESTAMP - INTERVAL '3 days',
        FALSE
    ),
    (
        '80000000-0000-0000-0000-000000000004',
        (
            SELECT category_id
            FROM dictionary_categories
            WHERE name = 'Sinh hoat'
        ),
        'An com',
        'Hanh dong dung bua',
        CURRENT_TIMESTAMP - INTERVAL '2 days',
        FALSE
    ) ON CONFLICT (entry_id) DO
UPDATE
SET category_id = EXCLUDED.category_id,
    word = EXCLUDED.word,
    meaning = EXCLUDED.meaning,
    updated_at = EXCLUDED.updated_at,
    is_deleted = EXCLUDED.is_deleted;
INSERT INTO dictionary_variations (
        variation_id,
        entry_id,
        region,
        video_url,
        description
    )
VALUES (
        '81000000-0000-0000-0000-000000000001',
        '80000000-0000-0000-0000-000000000001',
        'mien_bac',
        'https://video.local/dict/xin-chao-bac.mp4',
        'Bien the thong dung mien Bac'
    ),
    (
        '81000000-0000-0000-0000-000000000002',
        '80000000-0000-0000-0000-000000000001',
        'mien_nam',
        'https://video.local/dict/xin-chao-nam.mp4',
        'Bien the thong dung mien Nam'
    ),
    (
        '81000000-0000-0000-0000-000000000003',
        '80000000-0000-0000-0000-000000000002',
        'mien_bac',
        'https://video.local/dict/cam-on-bac.mp4',
        'Cam on theo cach pho bien'
    ),
    (
        '81000000-0000-0000-0000-000000000004',
        '80000000-0000-0000-0000-000000000003',
        'toan_quoc',
        'https://video.local/dict/lop-hoc.mp4',
        'Ky hieu lop hoc'
    ),
    (
        '81000000-0000-0000-0000-000000000005',
        '80000000-0000-0000-0000-000000000004',
        'toan_quoc',
        'https://video.local/dict/an-com.mp4',
        'Ky hieu an com'
    ) ON CONFLICT (variation_id) DO
UPDATE
SET entry_id = EXCLUDED.entry_id,
    region = EXCLUDED.region,
    video_url = EXCLUDED.video_url,
    description = EXCLUDED.description;
-- =========================================================
-- 4) GENERAL COURSES + MODULES + LESSONS + MATERIALS
-- =========================================================
INSERT INTO general_course_categories (name)
VALUES ('Co ban'),
    ('Giao tiep hoc duong'),
    ('Nang cao') ON CONFLICT (name) DO NOTHING;
INSERT INTO general_courses (
        course_id,
        teacher_id,
        category_id,
        title,
        description,
        visibility_status,
        updated_at,
        is_deleted
    )
VALUES (
        '40000000-0000-0000-0000-000000000001',
        '20000000-0000-0000-0000-000000000001',
        (
            SELECT category_id
            FROM general_course_categories
            WHERE name = 'Co ban'
        ),
        'Nhap mon ngon ngu ky hieu',
        'Khoa hoc nen tang cho nguoi moi bat dau.',
        'PUBLISHED',
        CURRENT_TIMESTAMP - INTERVAL '15 days',
        FALSE
    ),
    (
        '40000000-0000-0000-0000-000000000002',
        '20000000-0000-0000-0000-000000000002',
        (
            SELECT category_id
            FROM general_course_categories
            WHERE name = 'Giao tiep hoc duong'
        ),
        'Giao tiep trong lop hoc',
        'Mau cau va tinh huong trong moi truong hoc duong.',
        'PUBLISHED',
        CURRENT_TIMESTAMP - INTERVAL '10 days',
        FALSE
    ),
    (
        '40000000-0000-0000-0000-000000000003',
        '20000000-0000-0000-0000-000000000001',
        (
            SELECT category_id
            FROM general_course_categories
            WHERE name = 'Nang cao'
        ),
        'Luyen phan xa hoi thoai',
        'Thuc hanh phan xa giao tiep nang cao.',
        'DRAFT',
        CURRENT_TIMESTAMP - INTERVAL '2 days',
        FALSE
    ) ON CONFLICT (course_id) DO
UPDATE
SET teacher_id = EXCLUDED.teacher_id,
    category_id = EXCLUDED.category_id,
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    visibility_status = EXCLUDED.visibility_status,
    updated_at = EXCLUDED.updated_at,
    is_deleted = EXCLUDED.is_deleted;
INSERT INTO general_course_modules (module_id, course_id, title, order_index)
VALUES (
        '41000000-0000-0000-0000-000000000001',
        '40000000-0000-0000-0000-000000000001',
        'Module 1: Chu cai va chao hoi',
        1
    ),
    (
        '41000000-0000-0000-0000-000000000002',
        '40000000-0000-0000-0000-000000000001',
        'Module 2: Mau cau thong dung',
        2
    ),
    (
        '41000000-0000-0000-0000-000000000003',
        '40000000-0000-0000-0000-000000000002',
        'Module 1: Giao tiep voi ban be',
        1
    ),
    (
        '41000000-0000-0000-0000-000000000004',
        '40000000-0000-0000-0000-000000000002',
        'Module 2: Lam viec nhom',
        2
    ),
    (
        '41000000-0000-0000-0000-000000000005',
        '40000000-0000-0000-0000-000000000003',
        'Module 1: Tinh huong nang cao',
        1
    ) ON CONFLICT (module_id) DO
UPDATE
SET course_id = EXCLUDED.course_id,
    title = EXCLUDED.title,
    order_index = EXCLUDED.order_index;
INSERT INTO general_course_lessons (lesson_id, module_id, title, order_index)
VALUES (
        '42000000-0000-0000-0000-000000000001',
        '41000000-0000-0000-0000-000000000001',
        'Bai 1: Chu cai A-M',
        1
    ),
    (
        '42000000-0000-0000-0000-000000000002',
        '41000000-0000-0000-0000-000000000001',
        'Bai 2: Chu cai N-Z',
        2
    ),
    (
        '42000000-0000-0000-0000-000000000003',
        '41000000-0000-0000-0000-000000000002',
        'Bai 3: Chao hoi va gioi thieu',
        1
    ),
    (
        '42000000-0000-0000-0000-000000000004',
        '41000000-0000-0000-0000-000000000003',
        'Bai 1: Hoi dap trong lop',
        1
    ),
    (
        '42000000-0000-0000-0000-000000000005',
        '41000000-0000-0000-0000-000000000004',
        'Bai 2: Lam viec nhom co ban',
        1
    ),
    (
        '42000000-0000-0000-0000-000000000006',
        '41000000-0000-0000-0000-000000000005',
        'Bai 1: Tu vung boi canh nang cao',
        1
    ) ON CONFLICT (lesson_id) DO
UPDATE
SET module_id = EXCLUDED.module_id,
    title = EXCLUDED.title,
    order_index = EXCLUDED.order_index;
INSERT INTO learning_materials (
        material_id,
        lesson_id,
        title,
        content_url,
        material_transcript
    )
VALUES (
        '43000000-0000-0000-0000-000000000001',
        '42000000-0000-0000-0000-000000000001',
        'Video chu cai A-M',
        'https://cdn.local/materials/chu-cai-a-m.mp4',
        '{"duration_sec": 420, "level": "basic"}'::jsonb
    ),
    (
        '43000000-0000-0000-0000-000000000002',
        '42000000-0000-0000-0000-000000000002',
        'Video chu cai N-Z',
        'https://cdn.local/materials/chu-cai-n-z.mp4',
        '{"duration_sec": 400, "level": "basic"}'::jsonb
    ),
    (
        '43000000-0000-0000-0000-000000000003',
        '42000000-0000-0000-0000-000000000003',
        'Tai lieu chao hoi',
        'https://cdn.local/materials/chao-hoi.pdf',
        '{"pages": 18, "language": "vi"}'::jsonb
    ),
    (
        '43000000-0000-0000-0000-000000000004',
        '42000000-0000-0000-0000-000000000004',
        'Tinh huong hoi dap',
        'https://cdn.local/materials/hoi-dap.mp4',
        '{"duration_sec": 360, "level": "intermediate"}'::jsonb
    ),
    (
        '43000000-0000-0000-0000-000000000005',
        '42000000-0000-0000-0000-000000000005',
        'Mau cau lam viec nhom',
        'https://cdn.local/materials/lam-viec-nhom.pdf',
        '{"pages": 12, "language": "vi"}'::jsonb
    ),
    (
        '43000000-0000-0000-0000-000000000006',
        '42000000-0000-0000-0000-000000000006',
        'Tu vung nang cao',
        'https://cdn.local/materials/tu-vung-nang-cao.mp4',
        '{"duration_sec": 540, "level": "advanced"}'::jsonb
    ) ON CONFLICT (material_id) DO
UPDATE
SET lesson_id = EXCLUDED.lesson_id,
    title = EXCLUDED.title,
    content_url = EXCLUDED.content_url,
    material_transcript = EXCLUDED.material_transcript;
INSERT INTO course_enrollments (
        enrollment_id,
        student_id,
        course_id,
        progress,
        enrolled_at
    )
VALUES (
        '50000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000001',
        '40000000-0000-0000-0000-000000000001',
        100.00,
        CURRENT_TIMESTAMP - INTERVAL '10 days'
    ),
    (
        '50000000-0000-0000-0000-000000000002',
        '30000000-0000-0000-0000-000000000001',
        '40000000-0000-0000-0000-000000000002',
        65.50,
        CURRENT_TIMESTAMP - INTERVAL '8 days'
    ),
    (
        '50000000-0000-0000-0000-000000000003',
        '30000000-0000-0000-0000-000000000002',
        '40000000-0000-0000-0000-000000000001',
        80.00,
        CURRENT_TIMESTAMP - INTERVAL '7 days'
    ),
    (
        '50000000-0000-0000-0000-000000000004',
        '30000000-0000-0000-0000-000000000002',
        '40000000-0000-0000-0000-000000000002',
        35.00,
        CURRENT_TIMESTAMP - INTERVAL '6 days'
    ),
    (
        '50000000-0000-0000-0000-000000000005',
        '30000000-0000-0000-0000-000000000003',
        '40000000-0000-0000-0000-000000000001',
        20.00,
        CURRENT_TIMESTAMP - INTERVAL '5 days'
    ),
    (
        '50000000-0000-0000-0000-000000000006',
        '30000000-0000-0000-0000-000000000003',
        '40000000-0000-0000-0000-000000000003',
        10.00,
        CURRENT_TIMESTAMP - INTERVAL '3 days'
    ) ON CONFLICT (enrollment_id) DO
UPDATE
SET progress = EXCLUDED.progress,
    enrolled_at = EXCLUDED.enrolled_at;
INSERT INTO comments (
        comment_id,
        lesson_id,
        user_id,
        content,
        created_at
    )
VALUES (
        '51000000-0000-0000-0000-000000000001',
        '42000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000001',
        'Em hoc chu cai rat de hieu.',
        CURRENT_TIMESTAMP - INTERVAL '9 days'
    ),
    (
        '51000000-0000-0000-0000-000000000002',
        '42000000-0000-0000-0000-000000000003',
        '30000000-0000-0000-0000-000000000001',
        'Phan chao hoi rat huu ich.',
        CURRENT_TIMESTAMP - INTERVAL '7 days'
    ),
    (
        '51000000-0000-0000-0000-000000000003',
        '42000000-0000-0000-0000-000000000004',
        '30000000-0000-0000-0000-000000000002',
        'Bai hoi dap can them vi du.',
        CURRENT_TIMESTAMP - INTERVAL '5 days'
    ),
    (
        '51000000-0000-0000-0000-000000000004',
        '42000000-0000-0000-0000-000000000005',
        '30000000-0000-0000-0000-000000000003',
        'Em da luyen tap voi nhom ban.',
        CURRENT_TIMESTAMP - INTERVAL '2 days'
    ),
    (
        '51000000-0000-0000-0000-000000000005',
        '42000000-0000-0000-0000-000000000006',
        '20000000-0000-0000-0000-000000000001',
        'Nhom minh se bo sung tai lieu nang cao.',
        CURRENT_TIMESTAMP - INTERVAL '1 day'
    ) ON CONFLICT (comment_id) DO
UPDATE
SET lesson_id = EXCLUDED.lesson_id,
    user_id = EXCLUDED.user_id,
    content = EXCLUDED.content,
    created_at = EXCLUDED.created_at;
-- =========================================================
-- 5) MICROLEARNING
-- =========================================================
INSERT INTO microlearning_topics (title, description)
SELECT v.title,
    v.description
FROM (
        VALUES (
                'Chu de 1: Chao hoi nhanh',
                'Bai hoc ngan de giao tiep mo dau'
            ),
            (
                'Chu de 2: Truong hoc moi ngay',
                'Tinh huong lop hoc thuong gap'
            ),
            (
                'Chu de 3: Tu vung theo ngu canh',
                'Nang cao kha nang phan xa'
            )
    ) AS v(title, description)
WHERE NOT EXISTS (
        SELECT 1
        FROM microlearning_topics t
        WHERE t.title = v.title
    );
INSERT INTO microlearning_units (unit_id, topic_id, title, order_index)
VALUES (
        '60000000-0000-0000-0000-000000000001',
        (
            SELECT topic_id
            FROM microlearning_topics
            WHERE title = 'Chu de 1: Chao hoi nhanh'
            ORDER BY topic_id
            LIMIT 1
        ), 'Unit 1: Cau mo dau', 1
    ), (
        '60000000-0000-0000-0000-000000000002', (
            SELECT topic_id
            FROM microlearning_topics
            WHERE title = 'Chu de 1: Chao hoi nhanh'
            ORDER BY topic_id
            LIMIT 1
        ), 'Unit 2: Dap lai lich su', 2
    ), (
        '60000000-0000-0000-0000-000000000003', (
            SELECT topic_id
            FROM microlearning_topics
            WHERE title = 'Chu de 2: Truong hoc moi ngay'
            ORDER BY topic_id
            LIMIT 1
        ), 'Unit 1: Hoi bai tap', 1
    ), (
        '60000000-0000-0000-0000-000000000004', (
            SELECT topic_id
            FROM microlearning_topics
            WHERE title = 'Chu de 3: Tu vung theo ngu canh'
            ORDER BY topic_id
            LIMIT 1
        ), 'Unit 1: Tu khoa theo chu de', 1
    ) ON CONFLICT (unit_id) DO
UPDATE
SET topic_id = EXCLUDED.topic_id,
    title = EXCLUDED.title,
    order_index = EXCLUDED.order_index;
INSERT INTO microlearning_lessons (lesson_id, unit_id, title, order_index)
VALUES (
        '61000000-0000-0000-0000-000000000001',
        '60000000-0000-0000-0000-000000000001',
        'Micro lesson 1',
        1
    ),
    (
        '61000000-0000-0000-0000-000000000002',
        '60000000-0000-0000-0000-000000000002',
        'Micro lesson 2',
        1
    ),
    (
        '61000000-0000-0000-0000-000000000003',
        '60000000-0000-0000-0000-000000000003',
        'Micro lesson 3',
        1
    ),
    (
        '61000000-0000-0000-0000-000000000004',
        '60000000-0000-0000-0000-000000000004',
        'Micro lesson 4',
        1
    ) ON CONFLICT (lesson_id) DO
UPDATE
SET unit_id = EXCLUDED.unit_id,
    title = EXCLUDED.title,
    order_index = EXCLUDED.order_index;
INSERT INTO microlearning_lesson_parts (
        part_id,
        lesson_id,
        title,
        part_type,
        content,
        order_index
    )
VALUES (
        '62000000-0000-0000-0000-000000000001',
        '61000000-0000-0000-0000-000000000001',
        'Part video mo dau',
        'VIDEO',
        'Video 90 giay mo ta cau chao',
        1
    ),
    (
        '62000000-0000-0000-0000-000000000002',
        '61000000-0000-0000-0000-000000000001',
        'Part quiz nhanh',
        'QUIZ',
        'Cau hoi kiem tra ngay sau video',
        2
    ),
    (
        '62000000-0000-0000-0000-000000000003',
        '61000000-0000-0000-0000-000000000002',
        'Part flashcard',
        'FLASHCARD',
        'Bo flashcard cau dap lai',
        1
    ),
    (
        '62000000-0000-0000-0000-000000000004',
        '61000000-0000-0000-0000-000000000003',
        'Part tinh huong',
        'SCENARIO',
        'Tinh huong hoi bai tap',
        1
    ),
    (
        '62000000-0000-0000-0000-000000000005',
        '61000000-0000-0000-0000-000000000004',
        'Part quiz nang cao',
        'QUIZ',
        'Cau hoi theo ngu canh',
        1
    ) ON CONFLICT (part_id) DO
UPDATE
SET lesson_id = EXCLUDED.lesson_id,
    title = EXCLUDED.title,
    part_type = EXCLUDED.part_type,
    content = EXCLUDED.content,
    order_index = EXCLUDED.order_index;
INSERT INTO microlearning_questions (
        question_id,
        part_id,
        question_text,
        question_type,
        options_json,
        correct_answer
    )
VALUES (
        '63000000-0000-0000-0000-000000000001',
        '62000000-0000-0000-0000-000000000002',
        'Ky hieu nao phu hop de bat dau loi chao?',
        'MULTIPLE_CHOICE',
        '["Xin chao","Tam biet","Xin loi","Dong y"]'::jsonb,
        'Xin chao'
    ),
    (
        '63000000-0000-0000-0000-000000000002',
        '62000000-0000-0000-0000-000000000005',
        'Tinh huong hoc nhom, cau nao dung?',
        'MULTIPLE_CHOICE',
        '["Ban co the lap lai?","Khong quan tam","Dung lai ngay","Khong can"]'::jsonb,
        'Ban co the lap lai?'
    ),
    (
        '63000000-0000-0000-0000-000000000003',
        '62000000-0000-0000-0000-000000000004',
        'Danh dau cau phu hop khi hoi bai tap.',
        'TRUE_FALSE',
        '{"true_label":"Co","false_label":"Khong"}'::jsonb,
        'Co'
    ) ON CONFLICT (question_id) DO
UPDATE
SET part_id = EXCLUDED.part_id,
    question_text = EXCLUDED.question_text,
    question_type = EXCLUDED.question_type,
    options_json = EXCLUDED.options_json,
    correct_answer = EXCLUDED.correct_answer;
-- =========================================================
-- 6) GAMIFICATION
-- =========================================================
INSERT INTO student_streaks (
        student_id,
        current_streak,
        highest_streak,
        last_activity_date
    )
VALUES (
        '30000000-0000-0000-0000-000000000001',
        5,
        7,
        CURRENT_DATE - 1
    ),
    (
        '30000000-0000-0000-0000-000000000002',
        2,
        4,
        CURRENT_DATE
    ),
    (
        '30000000-0000-0000-0000-000000000003',
        1,
        3,
        CURRENT_DATE - 2
    ) ON CONFLICT (student_id) DO
UPDATE
SET current_streak = EXCLUDED.current_streak,
    highest_streak = EXCLUDED.highest_streak,
    last_activity_date = EXCLUDED.last_activity_date;
INSERT INTO achievements (title, description, icon_url)
SELECT v.title,
    v.description,
    v.icon_url
FROM (
        VALUES (
                'First Lesson',
                'Hoan thanh bai hoc dau tien',
                'https://cdn.local/icons/first-lesson.png'
            ),
            (
                'Fast Learner',
                'Dat 80% tien do trong 7 ngay',
                'https://cdn.local/icons/fast-learner.png'
            ),
            (
                'Helpful Friend',
                'Dang 3 binh luan huu ich',
                'https://cdn.local/icons/helpful-friend.png'
            ),
            (
                '7-Day Streak',
                'Hoc lien tuc 7 ngay',
                'https://cdn.local/icons/streak-7.png'
            )
    ) AS v(title, description, icon_url)
WHERE NOT EXISTS (
        SELECT 1
        FROM achievements a
        WHERE a.title = v.title
    );
INSERT INTO user_achievements (user_id, achievement_id, earned_at)
VALUES (
        '30000000-0000-0000-0000-000000000001',
        (
            SELECT achievement_id
            FROM achievements
            WHERE title = 'First Lesson'
            ORDER BY achievement_id
            LIMIT 1
        ), CURRENT_TIMESTAMP - INTERVAL '9 days'
    ),
    (
        '30000000-0000-0000-0000-000000000001',
        (
            SELECT achievement_id
            FROM achievements
            WHERE title = 'Fast Learner'
            ORDER BY achievement_id
            LIMIT 1
        ), CURRENT_TIMESTAMP - INTERVAL '4 days'
    ),
    (
        '30000000-0000-0000-0000-000000000002',
        (
            SELECT achievement_id
            FROM achievements
            WHERE title = 'Helpful Friend'
            ORDER BY achievement_id
            LIMIT 1
        ), CURRENT_TIMESTAMP - INTERVAL '2 days'
    ),
    (
        '30000000-0000-0000-0000-000000000003',
        (
            SELECT achievement_id
            FROM achievements
            WHERE title = 'First Lesson'
            ORDER BY achievement_id
            LIMIT 1
        ), CURRENT_TIMESTAMP - INTERVAL '3 days'
    ) ON CONFLICT (user_id, achievement_id) DO
UPDATE
SET earned_at = EXCLUDED.earned_at;
INSERT INTO user_feedbacks (
        feedback_id,
        user_id,
        rating,
        feedback_text,
        context,
        created_at
    )
VALUES (
        '72000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000001',
        5,
        'Noi dung ro rang, de hieu.',
        'course:Nhap mon ngon ngu ky hieu',
        CURRENT_TIMESTAMP - INTERVAL '4 days'
    ),
    (
        '72000000-0000-0000-0000-000000000002',
        '30000000-0000-0000-0000-000000000002',
        4,
        'Can them video thuc hanh.',
        'course:Giao tiep trong lop hoc',
        CURRENT_TIMESTAMP - INTERVAL '2 days'
    ),
    (
        '72000000-0000-0000-0000-000000000003',
        '30000000-0000-0000-0000-000000000003',
        3,
        'Bai nang cao hoi nhanh.',
        'course:Luyen phan xa hoi thoai',
        CURRENT_TIMESTAMP - INTERVAL '1 day'
    ),
    (
        '72000000-0000-0000-0000-000000000004',
        '20000000-0000-0000-0000-000000000001',
        NULL,
        'Can cap nhat them tai lieu moi.',
        'teacher_note',
        CURRENT_TIMESTAMP - INTERVAL '12 hours'
    ) ON CONFLICT (feedback_id) DO
UPDATE
SET user_id = EXCLUDED.user_id,
    rating = EXCLUDED.rating,
    feedback_text = EXCLUDED.feedback_text,
    context = EXCLUDED.context,
    created_at = EXCLUDED.created_at;
INSERT INTO notification_users (
        notification_id,
        user_id,
        title,
        message,
        is_read,
        created_at
    )
VALUES (
        '73000000-0000-0000-0000-000000000001',
        '30000000-0000-0000-0000-000000000001',
        'Tien do hoc tap',
        'Ban da hoan thanh 100% khoa Nhap mon ngon ngu ky hieu.',
        TRUE,
        CURRENT_TIMESTAMP - INTERVAL '3 days'
    ),
    (
        '73000000-0000-0000-0000-000000000002',
        '30000000-0000-0000-0000-000000000002',
        'Nhac nho hoc tap',
        'Ban dang dat 35% o khoa Giao tiep trong lop hoc.',
        FALSE,
        CURRENT_TIMESTAMP - INTERVAL '1 day'
    ),
    (
        '73000000-0000-0000-0000-000000000003',
        '30000000-0000-0000-0000-000000000003',
        'Ban moi',
        'Ban duoc de xuat hoc tiep chu de nang cao.',
        FALSE,
        CURRENT_TIMESTAMP - INTERVAL '8 hours'
    ) ON CONFLICT (notification_id) DO
UPDATE
SET user_id = EXCLUDED.user_id,
    title = EXCLUDED.title,
    message = EXCLUDED.message,
    is_read = EXCLUDED.is_read,
    created_at = EXCLUDED.created_at;
COMMIT;
-- =========================================================
-- Query kiem tra nhanh sau khi seed:
-- SELECT 'roles' AS table_name, COUNT(*) FROM roles
-- UNION ALL SELECT 'users', COUNT(*) FROM users
-- UNION ALL SELECT 'user_profiles', COUNT(*) FROM user_profiles
-- UNION ALL SELECT 'students', COUNT(*) FROM students
-- UNION ALL SELECT 'teachers', COUNT(*) FROM teachers
-- UNION ALL SELECT 'authentication_sessions', COUNT(*) FROM authentication_sessions
-- UNION ALL SELECT 'dictionary_categories', COUNT(*) FROM dictionary_categories
-- UNION ALL SELECT 'dictionary_entries', COUNT(*) FROM dictionary_entries
-- UNION ALL SELECT 'dictionary_variations', COUNT(*) FROM dictionary_variations
-- UNION ALL SELECT 'general_course_categories', COUNT(*) FROM general_course_categories
-- UNION ALL SELECT 'general_courses', COUNT(*) FROM general_courses
-- UNION ALL SELECT 'general_course_modules', COUNT(*) FROM general_course_modules
-- UNION ALL SELECT 'general_course_lessons', COUNT(*) FROM general_course_lessons
-- UNION ALL SELECT 'learning_materials', COUNT(*) FROM learning_materials
-- UNION ALL SELECT 'course_enrollments', COUNT(*) FROM course_enrollments
-- UNION ALL SELECT 'comments', COUNT(*) FROM comments
-- UNION ALL SELECT 'microlearning_topics', COUNT(*) FROM microlearning_topics
-- UNION ALL SELECT 'microlearning_units', COUNT(*) FROM microlearning_units
-- UNION ALL SELECT 'microlearning_lessons', COUNT(*) FROM microlearning_lessons
-- UNION ALL SELECT 'microlearning_lesson_parts', COUNT(*) FROM microlearning_lesson_parts
-- UNION ALL SELECT 'microlearning_questions', COUNT(*) FROM microlearning_questions
-- UNION ALL SELECT 'student_streaks', COUNT(*) FROM student_streaks
-- UNION ALL SELECT 'achievements', COUNT(*) FROM achievements
-- UNION ALL SELECT 'user_achievements', COUNT(*) FROM user_achievements
-- UNION ALL SELECT 'user_feedbacks', COUNT(*) FROM user_feedbacks
-- UNION ALL SELECT 'notification_users', COUNT(*) FROM notification_users;