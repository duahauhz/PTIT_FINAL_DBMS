CREATE TABLE roles (
    role_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_name VARCHAR(50) NOT NULL UNIQUE
);
CREATE TABLE users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    email VARCHAR(100) UNIQUE,
    role_id INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT fk_users_role FOREIGN KEY (role_id) REFERENCES roles (role_id) ON DELETE RESTRICT
);
CREATE TABLE user_profiles (
    profile_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE,
    full_name VARCHAR(100) NOT NULL,
    avatar_url VARCHAR(255),
    phone_number VARCHAR(20),
    date_of_birth DATE,
    CONSTRAINT fk_user_profiles_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE TABLE students (
    user_id UUID PRIMARY KEY,
    grade_level VARCHAR(50),
    school_name VARCHAR(150),
    CONSTRAINT fk_students_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE TABLE teachers (
    user_id UUID PRIMARY KEY,
    bio TEXT,
    department VARCHAR(100),
    CONSTRAINT fk_teachers_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE TABLE authentication_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    session_key VARCHAR(255) NOT NULL UNIQUE,
    otp_code VARCHAR(10),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_auth_session_expiry CHECK (expires_at > created_at),
    CONSTRAINT fk_auth_sessions_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);


CREATE TABLE dictionary_categories (
    category_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT
);
CREATE TABLE dictionary_entries (
    entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id INTEGER NOT NULL,
    word VARCHAR(100) NOT NULL,
    meaning TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_dict_entry_per_category UNIQUE (category_id, word),
    CONSTRAINT fk_dict_entries_category FOREIGN KEY (category_id) REFERENCES dictionary_categories (category_id) ON DELETE RESTRICT
);
CREATE TABLE dictionary_variations (
    variation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id UUID NOT NULL,
    region VARCHAR(100),
    video_url VARCHAR(255) NOT NULL,
    description TEXT,
    CONSTRAINT uq_dict_variation_video UNIQUE (entry_id, video_url),
    CONSTRAINT fk_dict_variations_entry FOREIGN KEY (entry_id) REFERENCES dictionary_entries (entry_id) ON DELETE CASCADE
);

CREATE TABLE general_course_categories (
    category_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
);
CREATE TABLE general_courses (
course_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
teacher_id UUID NOT NULL,
category_id INTEGER NOT NULL,
title VARCHAR(255) NOT NULL,
description TEXT,
visibility_status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
CONSTRAINT ck_course_visibility CHECK (
    visibility_status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')
),
CONSTRAINT fk_courses_teacher FOREIGN KEY (teacher_id) REFERENCES teachers (user_id) ON DELETE RESTRICT,
CONSTRAINT fk_courses_category FOREIGN KEY (category_id) REFERENCES general_course_categories (category_id) ON DELETE RESTRICT
);
CREATE TABLE general_course_modules (
    module_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    order_index INTEGER NOT NULL CHECK (order_index > 0),
    CONSTRAINT uq_module_order_per_course UNIQUE (course_id, order_index),
    CONSTRAINT fk_modules_course FOREIGN KEY (course_id) REFERENCES general_courses (course_id) ON DELETE CASCADE
);
CREATE TABLE general_course_lessons (
    lesson_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    order_index INTEGER NOT NULL CHECK (order_index > 0),
    CONSTRAINT uq_lesson_order_per_module UNIQUE (module_id, order_index),
    CONSTRAINT fk_lessons_module FOREIGN KEY (module_id) REFERENCES general_course_modules (module_id) ON DELETE CASCADE
);
CREATE TABLE learning_materials (
    material_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    content_url VARCHAR(255) NOT NULL,
    material_transcript JSONB,
    CONSTRAINT fk_materials_lesson FOREIGN KEY (lesson_id) REFERENCES general_course_lessons (lesson_id) ON DELETE CASCADE
);
CREATE TABLE course_enrollments (
    enrollment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL,
    course_id UUID NOT NULL,
    progress NUMERIC(5, 2) NOT NULL DEFAULT 0.00,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_student_course_enrollment UNIQUE (student_id, course_id),
    CONSTRAINT ck_enrollment_progress CHECK (
        progress >= 0
        AND progress <= 100
    ),
    CONSTRAINT fk_enrollments_student FOREIGN KEY (student_id) REFERENCES students (user_id) ON DELETE CASCADE,
    CONSTRAINT fk_enrollments_course FOREIGN KEY (course_id) REFERENCES general_courses (course_id) ON DELETE CASCADE
);
CREATE TABLE comments (
    comment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id UUID NOT NULL,
    user_id UUID NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_comments_lesson FOREIGN KEY (lesson_id) REFERENCES general_course_lessons (lesson_id) ON DELETE CASCADE,
    CONSTRAINT fk_comments_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE TABLE microlearning_topics (
    topic_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title VARCHAR(150) NOT NULL,
    description TEXT
);
CREATE TABLE microlearning_units (
    unit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic_id INTEGER NOT NULL,
    title VARCHAR(255) NOT NULL,
    order_index INTEGER NOT NULL CHECK (order_index > 0),
    CONSTRAINT uq_ml_unit_order_per_topic UNIQUE (topic_id, order_index),
    CONSTRAINT fk_ml_units_topic FOREIGN KEY (topic_id) REFERENCES microlearning_topics (topic_id) ON DELETE CASCADE
);
CREATE TABLE microlearning_lessons (
    lesson_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    order_index INTEGER NOT NULL CHECK (order_index > 0),
    CONSTRAINT uq_ml_lesson_order_per_unit UNIQUE (unit_id, order_index),
    CONSTRAINT fk_ml_lessons_unit FOREIGN KEY (unit_id) REFERENCES microlearning_units (unit_id) ON DELETE CASCADE
);
CREATE TABLE microlearning_lesson_parts (
    part_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lesson_id UUID NOT NULL,
    title VARCHAR(255),
    part_type VARCHAR(50) NOT NULL,
    content TEXT,
    order_index INTEGER NOT NULL CHECK (order_index > 0),
    CONSTRAINT uq_ml_part_order_per_lesson UNIQUE (lesson_id, order_index),
    CONSTRAINT fk_ml_parts_lesson FOREIGN KEY (lesson_id) REFERENCES microlearning_lessons (lesson_id) ON DELETE CASCADE
);
CREATE TABLE microlearning_questions (
    question_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    part_id UUID NOT NULL,
    question_text TEXT NOT NULL,
    question_type VARCHAR(20) NOT NULL,
    options_json JSONB NOT NULL,
    correct_answer VARCHAR(255) NOT NULL,
    CONSTRAINT fk_ml_questions_part FOREIGN KEY (part_id) REFERENCES microlearning_lesson_parts (part_id) ON DELETE CASCADE
);

CREATE TABLE student_streaks (
    streak_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL UNIQUE,
    current_streak INTEGER NOT NULL DEFAULT 0,
    highest_streak INTEGER NOT NULL DEFAULT 0,
    last_activity_date DATE,
    CONSTRAINT ck_streak_non_negative CHECK (
        current_streak >= 0
        AND highest_streak >= 0
    ),
    CONSTRAINT ck_streak_highest_gte_current CHECK (highest_streak >= current_streak),
    CONSTRAINT fk_streaks_student FOREIGN KEY (student_id) REFERENCES students (user_id) ON DELETE CASCADE
);
CREATE TABLE achievements (
    achievement_id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title VARCHAR(100) NOT NULL,
    description TEXT NOT NULL,
    icon_url VARCHAR(255)
);
CREATE TABLE user_achievements (
    user_id UUID NOT NULL,
    achievement_id INTEGER NOT NULL,
    earned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, achievement_id),
    CONSTRAINT fk_ua_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
    CONSTRAINT fk_ua_achievement FOREIGN KEY (achievement_id) REFERENCES achievements (achievement_id) ON DELETE CASCADE
);
CREATE TABLE user_feedbacks (
    feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    rating INTEGER,
    feedback_text TEXT,
    context VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_feedback_rating CHECK (
        rating IS NULL
        OR (
            rating BETWEEN 1 AND 5
        )
    ),
    CONSTRAINT fk_feedbacks_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);
CREATE TABLE notification_users (
    notification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_users_role_id ON users (role_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON authentication_sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_dict_entries_category_id ON dictionary_entries (category_id);
CREATE INDEX IF NOT EXISTS idx_dict_variations_entry_id ON dictionary_variations (entry_id);
CREATE INDEX IF NOT EXISTS idx_courses_teacher_id ON general_courses (teacher_id);
CREATE INDEX IF NOT EXISTS idx_courses_category_id ON general_courses (category_id);
CREATE INDEX IF NOT EXISTS idx_modules_course_id ON general_course_modules (course_id);
CREATE INDEX IF NOT EXISTS idx_lessons_module_id ON general_course_lessons (module_id);
CREATE INDEX IF NOT EXISTS idx_materials_lesson_id ON learning_materials (lesson_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_student_id ON course_enrollments (student_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_course_id ON course_enrollments (course_id);
CREATE INDEX IF NOT EXISTS idx_comments_lesson_id ON comments (lesson_id);
CREATE INDEX IF NOT EXISTS idx_comments_user_id ON comments (user_id);
CREATE INDEX IF NOT EXISTS idx_ml_units_topic_id ON microlearning_units (topic_id);
CREATE INDEX IF NOT EXISTS idx_ml_lessons_unit_id ON microlearning_lessons (unit_id);
CREATE INDEX IF NOT EXISTS idx_ml_parts_lesson_id ON microlearning_lesson_parts (lesson_id);
CREATE INDEX IF NOT EXISTS idx_ml_questions_part_id ON microlearning_questions (part_id);
CREATE INDEX IF NOT EXISTS idx_user_feedbacks_user_id ON user_feedbacks (user_id);
CREATE INDEX IF NOT EXISTS idx_notification_users_user_id ON notification_users (user_id);
CREATE INDEX IF NOT EXISTS idx_users_is_deleted ON users (is_deleted);
CREATE INDEX IF NOT EXISTS idx_courses_is_deleted ON general_courses (is_deleted);
