"""Migraciones SQL adicionales (columnas y tablas nuevas)."""
from sqlalchemy import text


def run_migrations(conn):
    stmts = [
        "ALTER TABLE subscription_plan ADD COLUMN IF NOT EXISTS price_yearly DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE subscription_plan ADD COLUMN IF NOT EXISTS stripe_price_id_yearly VARCHAR(120) DEFAULT ''",
        "ALTER TABLE subscription_plan ADD COLUMN IF NOT EXISTS trial_days INTEGER DEFAULT 0",
        "ALTER TABLE subscription_plan ADD COLUMN IF NOT EXISTS stripe_coupon_id VARCHAR(120) DEFAULT ''",
        "ALTER TABLE category ADD COLUMN IF NOT EXISTS required_plan_id INTEGER REFERENCES subscription_plan(id)",
        "ALTER TABLE post ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN DEFAULT FALSE",
        "ALTER TABLE post ADD COLUMN IF NOT EXISTS hidden_reason VARCHAR(300) DEFAULT ''",
        "ALTER TABLE course ADD COLUMN IF NOT EXISTS certificate_enabled BOOLEAN DEFAULT TRUE",
        "ALTER TABLE lesson ADD COLUMN IF NOT EXISTS drip_days INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS quiz (
            id SERIAL PRIMARY KEY, section_id INTEGER NOT NULL REFERENCES section(id) ON DELETE CASCADE,
            title VARCHAR(200) NOT NULL, pass_percent INTEGER DEFAULT 70, is_required BOOLEAN DEFAULT TRUE)""",
        """CREATE TABLE IF NOT EXISTS quiz_question (
            id SERIAL PRIMARY KEY, quiz_id INTEGER NOT NULL REFERENCES quiz(id) ON DELETE CASCADE,
            text TEXT NOT NULL, "order" INTEGER DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS quiz_option (
            id SERIAL PRIMARY KEY, question_id INTEGER NOT NULL REFERENCES quiz_question(id) ON DELETE CASCADE,
            text VARCHAR(500) NOT NULL, is_correct BOOLEAN DEFAULT FALSE)""",
        """CREATE TABLE IF NOT EXISTS quiz_attempt (
            id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES "user"(id),
            quiz_id INTEGER NOT NULL REFERENCES quiz(id), score INTEGER DEFAULT 0,
            passed BOOLEAN DEFAULT FALSE, created_at TIMESTAMP DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS assignment (
            id SERIAL PRIMARY KEY, section_id INTEGER NOT NULL REFERENCES section(id) ON DELETE CASCADE,
            title VARCHAR(200) NOT NULL, description TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS assignment_submission (
            id SERIAL PRIMARY KEY, assignment_id INTEGER NOT NULL REFERENCES assignment(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES "user"(id), content TEXT NOT NULL,
            mentor_feedback TEXT DEFAULT '', status VARCHAR(20) DEFAULT 'pending',
            submitted_at TIMESTAMP DEFAULT NOW(), reviewed_at TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS post_report (
            id SERIAL PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES post(id),
            reporter_id INTEGER NOT NULL REFERENCES "user"(id), reason VARCHAR(500) DEFAULT '',
            status VARCHAR(20) DEFAULT 'pending', created_at TIMESTAMP DEFAULT NOW(),
            resolved_at TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS live_class_reminder_log (
            id SERIAL PRIMARY KEY, live_class_id INTEGER NOT NULL REFERENCES live_class(id),
            user_id INTEGER NOT NULL REFERENCES "user"(id), reminder_type VARCHAR(10) NOT NULL,
            sent_at TIMESTAMP DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS email_campaign (
            id SERIAL PRIMARY KEY, admin_id INTEGER REFERENCES "user"(id),
            subject VARCHAR(300) NOT NULL, target VARCHAR(30) DEFAULT 'students',
            total_sent INTEGER DEFAULT 0, total_failed INTEGER DEFAULT 0,
            batch_size INTEGER DEFAULT 50, created_at TIMESTAMP DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS course_certificate (
            id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES "user"(id),
            course_id INTEGER NOT NULL REFERENCES course(id),
            certificate_code VARCHAR(32) UNIQUE NOT NULL, issued_at TIMESTAMP DEFAULT NOW())""",
        """CREATE TABLE IF NOT EXISTS live_class_category (
            id SERIAL PRIMARY KEY,
            name VARCHAR(80) UNIQUE NOT NULL,
            color VARCHAR(20) DEFAULT '#7c3aed',
            emoji VARCHAR(10) DEFAULT '📅',
            sort_order INTEGER DEFAULT 0)""",
        "ALTER TABLE live_class ADD COLUMN IF NOT EXISTS category_id INTEGER REFERENCES live_class_category(id)",
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS city VARCHAR(120) DEFAULT \'\'',
        "ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS event_reminder_email_subject VARCHAR(300) DEFAULT ''",
        "ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS event_reminder_email_body TEXT DEFAULT ''",
        "ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS event_reminder_24h_enabled BOOLEAN DEFAULT TRUE",
        "ALTER TABLE site_settings ADD COLUMN IF NOT EXISTS event_reminder_1h_enabled BOOLEAN DEFAULT TRUE",
    ]
    for sql in stmts:
        try:
            conn.execute(text(sql))
        except Exception:
            pass
