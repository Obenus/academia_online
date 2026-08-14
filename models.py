from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

post_likes = db.Table('post_likes',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('post_id', db.Integer, db.ForeignKey('post.id'), primary_key=True),
)

comment_likes = db.Table('comment_likes',
    db.Column('user_id',    db.Integer, db.ForeignKey('user.id'),    primary_key=True),
    db.Column('comment_id', db.Integer, db.ForeignKey('comment.id'), primary_key=True),
)


class User(UserMixin, db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    username     = db.Column(db.String(80),  unique=True, nullable=False)
    email        = db.Column(db.String(120), unique=True, nullable=False)
    password_hash= db.Column(db.String(256))
    role         = db.Column(db.String(20), default='student')   # 'student' | 'admin'
    bio          = db.Column(db.Text, default='')
    city         = db.Column(db.String(120), default='')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen    = db.Column(db.DateTime, nullable=True)
    avatar_data  = db.Column(db.LargeBinary, nullable=True)
    avatar_mime  = db.Column(db.String(50), default='image/jpeg')
    status       = db.Column(db.String(20), default='active')  # 'pending' | 'active' | 'rejected' | 'suspended'
    billing_type = db.Column(db.String(20), default='standard')  # 'standard' | 'free'
    subscription_plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plan.id'), nullable=True)
    stripe_customer_id     = db.Column(db.String(120), default='')
    stripe_subscription_id = db.Column(db.String(120), default='')
    subscription_status    = db.Column(db.String(30), default='none')  # none|active|past_due|canceled|unpaid
    subscription_period_end = db.Column(db.DateTime, nullable=True)
    subscription_last_paid_at = db.Column(db.DateTime, nullable=True)
    whatsapp_vip_pending    = db.Column(db.Boolean, default=False)

    posts        = db.relationship('Post',    backref='author', lazy=True)
    subscription_plan = db.relationship('SubscriptionPlan', backref='users', lazy=True)
    comments     = db.relationship('Comment', backref='author', lazy=True)
    enrollments  = db.relationship('Enrollment', backref='user', lazy=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    def is_enrolled(self, course_id):
        return Enrollment.query.filter_by(user_id=self.id, course_id=course_id).first() is not None

    @property
    def is_admin(self):
        return self.role == 'admin'

    @property
    def initials(self):
        return self.username[0].upper()

    @property
    def is_free_billing(self):
        return self.billing_type == 'free'

    @property
    def subscription_ok(self):
        if self.is_admin or self.is_free_billing:
            return True
        if self.status != 'active':
            return False
        st = self.subscription_status or 'none'
        if st not in ('active', 'trialing'):
            return False
        if self.subscription_period_end and self.subscription_period_end < datetime.utcnow():
            return False
        return True


class SubscriptionPlan(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120), nullable=False)
    description     = db.Column(db.Text, default='')
    price_monthly   = db.Column(db.Float, default=0.0)
    price_monthly_es = db.Column(db.Float, default=0.0)
    price_monthly_intl = db.Column(db.Float, default=0.0)
    price_yearly    = db.Column(db.Float, default=0.0)
    stripe_price_id = db.Column(db.String(120), default='')
    stripe_price_id_es = db.Column(db.String(120), default='')
    stripe_price_id_intl = db.Column(db.String(120), default='')
    stripe_price_id_yearly = db.Column(db.String(120), default='')
    trial_days      = db.Column(db.Integer, default=0)
    stripe_coupon_id = db.Column(db.String(120), default='')
    is_active       = db.Column(db.Boolean, default=True)
    sort_order      = db.Column(db.Integer, default=0)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    def price_for_region(self, region='es'):
        if region == 'intl':
            return self.price_monthly_intl or self.price_monthly or 0.0
        return self.price_monthly_es or self.price_monthly or 0.0

    def stripe_price_for_region(self, region='es'):
        if region == 'intl':
            return self.stripe_price_id_intl or self.stripe_price_id or ''
        return self.stripe_price_id_es or self.stripe_price_id or ''


class CheckoutIntent(db.Model):
    __tablename__ = 'checkout_intent'
    id                 = db.Column(db.Integer, primary_key=True)
    stripe_session_id  = db.Column(db.String(200), unique=True, nullable=True)
    plan_id            = db.Column(db.Integer, db.ForeignKey('subscription_plan.id'), nullable=False)
    billing_region     = db.Column(db.String(10), default='es')  # es | intl
    customer_email     = db.Column(db.String(120), default='')
    customer_name      = db.Column(db.String(200), default='')
    status             = db.Column(db.String(20), default='pending')  # pending|completed|failed
    user_id            = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    plain_password     = db.Column(db.String(80), default='')  # one-time for welcome email
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)
    plan               = db.relationship('SubscriptionPlan', backref='checkout_intents', lazy=True)
    user               = db.relationship('User', backref='checkout_intents', lazy=True)


class Category(db.Model):
    id    = db.Column(db.Integer, primary_key=True)
    name  = db.Column(db.String(50), unique=True, nullable=False)
    slug  = db.Column(db.String(50), unique=True, nullable=True)
    is_system = db.Column(db.Boolean, default=False)
    color = db.Column(db.String(20), default='#6366f1')
    emoji = db.Column(db.String(10), default='💬')
    required_plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plan.id'), nullable=True)
    required_plan = db.relationship('SubscriptionPlan', backref='categories', lazy=True)
    posts = db.relationship('Post', backref='category', lazy=True)


class Post(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    title       = db.Column(db.String(200), nullable=False)
    content     = db.Column(db.Text, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    pinned      = db.Column(db.Boolean, default=False)
    is_hidden   = db.Column(db.Boolean, default=False)
    hidden_reason = db.Column(db.String(300), default='')
    workflow_status = db.Column(db.String(30), nullable=True)  # pendiente|respondida|importante|idea_contenido

    likes    = db.relationship('User', secondary=post_likes,
                               backref=db.backref('liked_posts', lazy=True))
    comments = db.relationship('Comment', backref='post', lazy=True,
                               cascade='all, delete-orphan',
                               order_by='Comment.created_at')


class Comment(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    likes = db.relationship('User', secondary='comment_likes',
                            backref=db.backref('liked_comments', lazy=True))


class Course(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    title        = db.Column(db.String(200), nullable=False)
    subtitle     = db.Column(db.String(300), default='')
    description  = db.Column(db.Text, default='')
    image        = db.Column(db.String(500), default='')
    cover_data   = db.Column(db.LargeBinary, nullable=True)
    cover_mime   = db.Column(db.String(50), default='image/jpeg')
    price        = db.Column(db.Float, default=0.0)
    order        = db.Column(db.Integer, default=0)
    is_published = db.Column(db.Boolean, default=False)
    certificate_enabled = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    sections    = db.relationship('Section', backref='course', lazy=True,
                                  order_by='Section.order',
                                  cascade='all, delete-orphan')
    enrollments = db.relationship('Enrollment', backref='course', lazy=True)

    @property
    def is_free(self):
        return self.price == 0.0

    @property
    def lesson_count(self):
        return sum(len(s.lessons) for s in self.sections)

    @property
    def total_duration(self):
        return sum(l.duration_min for s in self.sections for l in s.lessons)


class Section(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    title     = db.Column(db.String(200), nullable=False)
    order     = db.Column(db.Integer, default=0)
    lessons   = db.relationship('Lesson', backref='section', lazy=True,
                                order_by='Lesson.order',
                                cascade='all, delete-orphan')
    quizzes     = db.relationship('Quiz', backref='section', lazy=True, cascade='all, delete-orphan')
    assignments = db.relationship('Assignment', backref='section', lazy=True, cascade='all, delete-orphan')


class Lesson(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    section_id   = db.Column(db.Integer, db.ForeignKey('section.id'), nullable=False)
    title        = db.Column(db.String(200), nullable=False)
    video_url    = db.Column(db.String(500), default='')
    description  = db.Column(db.Text, default='')
    order        = db.Column(db.Integer, default=0)
    duration_min = db.Column(db.Integer, default=0)
    group_label  = db.Column(db.String(200), nullable=True)
    drip_days    = db.Column(db.Integer, default=0)
    files        = db.relationship('LessonFile', backref='lesson', lazy=True,
                                   cascade='all, delete-orphan')


class LessonFile(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    lesson_id  = db.Column(db.Integer, db.ForeignKey('lesson.id'), nullable=False)
    name       = db.Column(db.String(200), nullable=False)
    mimetype   = db.Column(db.String(100), default='application/octet-stream')
    size       = db.Column(db.Integer, default=0)
    data       = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LessonImage(db.Model):
    """Inline images embedded in lesson descriptions via TinyMCE editor."""
    id         = db.Column(db.Integer, primary_key=True)
    lesson_id  = db.Column(db.Integer, db.ForeignKey('lesson.id'), nullable=False)
    mimetype   = db.Column(db.String(100), default='image/jpeg')
    data       = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Enrollment(db.Model):
    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_id         = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    enrolled_at       = db.Column(db.DateTime, default=datetime.utcnow)
    stripe_session_id = db.Column(db.String(200), default='')


class LessonProgress(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    lesson_id  = db.Column(db.Integer, db.ForeignKey('lesson.id'), nullable=False)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)


class SiteSettings(db.Model):
    id                    = db.Column(db.Integer, primary_key=True)
    academy_name          = db.Column(db.String(100), default='Marca Atractora')
    community_image       = db.Column(db.String(500), default='')
    community_image_data  = db.Column(db.LargeBinary, nullable=True)
    community_image_mime  = db.Column(db.String(50), default='image/jpeg')
    community_description = db.Column(db.Text, default='')
    link_url              = db.Column(db.String(500), default='')
    link_text             = db.Column(db.String(200), default='¡Empieza por aquí!')
    backup_enabled        = db.Column(db.Boolean, default=False)
    backup_interval_hours = db.Column(db.Integer, default=24)
    backup_retention_days = db.Column(db.Integer, default=14)
    backup_local_path     = db.Column(db.String(300), default='/app/backups')
    backup_s3_enabled     = db.Column(db.Boolean, default=False)
    backup_s3_bucket      = db.Column(db.String(200), default='')
    backup_s3_region      = db.Column(db.String(100), default='eu-west-1')
    backup_s3_prefix      = db.Column(db.String(200), default='miacademia')
    backup_s3_endpoint_url= db.Column(db.String(300), default='')
    backup_s3_access_key_enc = db.Column(db.Text, default='')
    backup_s3_secret_key_enc = db.Column(db.Text, default='')
    backup_last_run_at    = db.Column(db.DateTime, nullable=True)
    backup_last_status    = db.Column(db.String(40), default='')
    backup_last_error     = db.Column(db.Text, default='')
    payments_enabled      = db.Column(db.Boolean, default=False)
    stripe_public_key     = db.Column(db.String(200), default='')
    stripe_secret_key_enc = db.Column(db.Text, default='')
    stripe_webhook_secret_enc = db.Column(db.Text, default='')
    pay_auto_activate     = db.Column(db.Boolean, default=True)
    welcome_email_subject = db.Column(db.String(300), default='')
    welcome_email_body    = db.Column(db.Text, default='')
    admin_reg_email_subject = db.Column(db.String(300), default='')
    admin_reg_email_body    = db.Column(db.Text, default='')
    event_reminder_email_subject = db.Column(db.String(300), default='')
    event_reminder_email_body    = db.Column(db.Text, default='')
    event_reminder_24h_enabled   = db.Column(db.Boolean, default=True)
    event_reminder_1h_enabled    = db.Column(db.Boolean, default=True)
    billing_alert_email_subject  = db.Column(db.String(300), default='')
    billing_alert_email_body     = db.Column(db.Text, default='')
    landing_title                = db.Column(db.String(200), default='')
    landing_subtitle             = db.Column(db.Text, default='')
    landing_benefits             = db.Column(db.Text, default='')
    landing_hook                 = db.Column(db.Text, default='')
    landing_intro                = db.Column(db.Text, default='')
    landing_what_is              = db.Column(db.Text, default='')
    landing_how_helps            = db.Column(db.Text, default='')
    landing_explore_questions    = db.Column(db.Text, default='')
    landing_includes             = db.Column(db.Text, default='')
    landing_for_you              = db.Column(db.Text, default='')
    landing_closing              = db.Column(db.Text, default='')
    landing_cta_text             = db.Column(db.String(120), default='')
    landing_price_note           = db.Column(db.String(200), default='')
    landing_login_title          = db.Column(db.String(200), default='')
    landing_login_subtitle       = db.Column(db.String(300), default='')
    landing_video_url            = db.Column(db.String(500), default='')
    legal_community_md           = db.Column(db.Text, default='')
    legal_privacy_md             = db.Column(db.Text, default='')
    legal_cookies_md             = db.Column(db.Text, default='')
    legal_notice_md              = db.Column(db.Text, default='')
    commercial_landing_enabled   = db.Column(db.Boolean, default=False)
    commercial_landing_slug      = db.Column(db.String(80), default='oferta')
    commercial_landing_title     = db.Column(db.String(200), default='')
    commercial_landing_text      = db.Column(db.Text, default='')
    commercial_landing_whatsapp_url = db.Column(db.String(500), default='')
    commercial_landing_image_data = db.Column(db.LargeBinary, nullable=True)
    commercial_landing_image_mime = db.Column(db.String(50), default='image/jpeg')
    commercial_lead_notify_email = db.Column(db.String(200), default='')
    commercial_reply_subject     = db.Column(db.String(300), default='')
    commercial_reply_body        = db.Column(db.Text, default='')
    commercial_reply_whatsapp_url = db.Column(db.String(500), default='')
    library_catalog_order        = db.Column(db.Text, default='')  # claves de tarjetas, una por línea
    mail_server                  = db.Column(db.String(200), default='')
    mail_port                    = db.Column(db.Integer, default=587)
    mail_use_tls                 = db.Column(db.Boolean, default=True)
    mail_use_ssl                 = db.Column(db.Boolean, default=False)
    mail_username                = db.Column(db.String(200), default='')
    mail_password_enc            = db.Column(db.Text, default='')
    mail_sender                  = db.Column(db.String(200), default='')
    mail_local_relay             = db.Column(db.Boolean, default=False)
    welcome_video_url            = db.Column(db.String(500), default='')
    how_it_works_video_url       = db.Column(db.String(500), default='')
    start_page_intro             = db.Column(db.Text, default='')
    whatsapp_url                 = db.Column(db.String(500), default='')
    brand_logo_url               = db.Column(db.String(500), default='')
    color_primary                = db.Column(db.String(20), default='#7c3aed')
    color_secondary              = db.Column(db.String(20), default='#6d28d9')
    font_family                  = db.Column(db.String(120), default='')
    player_bar_bg                = db.Column(db.String(20), default='#141414')
    player_bar_accent            = db.Column(db.String(20), default='')
    player_bar_text              = db.Column(db.String(20), default='#bfbfbf')
    player_bar_btn               = db.Column(db.String(20), default='#2a2a2a')
    member_of_month_user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    member_of_month_note         = db.Column(db.String(300), default='')
    member_of_month_month        = db.Column(db.String(20), default='')  # YYYY-MM


class PointEvent(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    points     = db.Column(db.Integer, default=0)
    reason     = db.Column(db.String(50))   # 'lesson' | 'comment' | 'like'
    ref_id     = db.Column(db.Integer)      # id of lesson/comment/post
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type       = db.Column(db.String(30))  # 'new_class' | 'class_reminder' | 'comment'
    message    = db.Column(db.String(300))
    link       = db.Column(db.String(200), default='')
    is_read    = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class LiveClassCategory(db.Model):
    __tablename__ = 'live_class_category'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(80), unique=True, nullable=False)
    color      = db.Column(db.String(20), default='#7c3aed')
    emoji      = db.Column(db.String(10), default='📅')
    sort_order = db.Column(db.Integer, default=0)
    live_classes = db.relationship('LiveClass', backref='category', lazy=True)


class LiveClass(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    title        = db.Column(db.String(200), nullable=False)
    description  = db.Column(db.Text, default='')
    scheduled_at = db.Column(db.DateTime, nullable=False)
    duration_min = db.Column(db.Integer, default=60)
    meet_url     = db.Column(db.String(500), default='')
    instructor   = db.Column(db.String(100), default='')
    recurrence   = db.Column(db.String(10), default='none')  # 'none' | 'weekly' | 'monthly'
    parent_id    = db.Column(db.Integer, db.ForeignKey('live_class.id'), nullable=True)
    category_id  = db.Column(db.Integer, db.ForeignKey('live_class_category.id'), nullable=True)
    subtopic     = db.Column(db.String(200), default='')


class CalendarMonthTheme(db.Model):
    __tablename__ = 'calendar_month_theme'
    id          = db.Column(db.Integer, primary_key=True)
    year        = db.Column(db.Integer, nullable=False)
    month       = db.Column(db.Integer, nullable=False)
    theme_title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')


class LibraryItem(db.Model):
    __tablename__ = 'library_item'
    id            = db.Column(db.Integer, primary_key=True)
    title         = db.Column(db.String(200), nullable=False)
    description   = db.Column(db.Text, default='')
    video_url     = db.Column(db.String(500), default='')
    year          = db.Column(db.Integer, nullable=False)
    month         = db.Column(db.Integer, nullable=False)
    item_type     = db.Column(db.String(20), default='extra')  # encuentro|extra
    live_class_id = db.Column(db.Integer, db.ForeignKey('live_class.id'), nullable=True)
    sort_order    = db.Column(db.Integer, default=0)
    is_published  = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    live_class    = db.relationship('LiveClass', backref='library_items', lazy=True)


resource_tags = db.Table('resource_tags',
    db.Column('resource_id', db.Integer, db.ForeignKey('resource.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('resource_tag.id'), primary_key=True),
)


class ResourceTag(db.Model):
    __tablename__ = 'resource_tag'
    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)


class Resource(db.Model):
    __tablename__ = 'resource'
    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    media_type  = db.Column(db.String(20), default='pdf')  # pdf|audio|video|checklist
    file_url    = db.Column(db.String(500), default='')
    file_data   = db.Column(db.LargeBinary, nullable=True)
    file_mime   = db.Column(db.String(100), default='')
    file_name   = db.Column(db.String(200), default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    tags        = db.relationship('ResourceTag', secondary=resource_tags, backref='resources', lazy=True)


class Quiz(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    section_id   = db.Column(db.Integer, db.ForeignKey('section.id'), nullable=False)
    title        = db.Column(db.String(200), nullable=False)
    pass_percent = db.Column(db.Integer, default=70)
    is_required  = db.Column(db.Boolean, default=True)
    questions    = db.relationship('QuizQuestion', backref='quiz', lazy=True,
                                   cascade='all, delete-orphan', order_by='QuizQuestion.order')


class QuizQuestion(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    quiz_id    = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    text       = db.Column(db.Text, nullable=False)
    order      = db.Column(db.Integer, default=0)
    options    = db.relationship('QuizOption', backref='question', lazy=True,
                                 cascade='all, delete-orphan')


class QuizOption(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey('quiz_question.id'), nullable=False)
    text        = db.Column(db.String(500), nullable=False)
    is_correct  = db.Column(db.Boolean, default=False)


class QuizAttempt(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quiz_id    = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    score      = db.Column(db.Integer, default=0)
    passed     = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Assignment(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    section_id  = db.Column(db.Integer, db.ForeignKey('section.id'), nullable=False)
    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    submissions = db.relationship('AssignmentSubmission', backref='assignment', lazy=True,
                                  cascade='all, delete-orphan')


class AssignmentSubmission(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    assignment_id   = db.Column(db.Integer, db.ForeignKey('assignment.id'), nullable=False)
    user_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content         = db.Column(db.Text, nullable=False)
    mentor_feedback = db.Column(db.Text, default='')
    status          = db.Column(db.String(20), default='pending')  # pending|reviewed|returned
    submitted_at    = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at     = db.Column(db.DateTime, nullable=True)
    user            = db.relationship('User', backref='assignment_submissions', lazy=True)


class PostReport(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    post_id     = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason      = db.Column(db.String(500), default='')
    status      = db.Column(db.String(20), default='pending')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    post        = db.relationship('Post', backref='reports', lazy=True)
    reporter    = db.relationship('User', foreign_keys=[reporter_id], lazy=True)


class LiveClassReminderLog(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    live_class_id = db.Column(db.Integer, db.ForeignKey('live_class.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reminder_type = db.Column(db.String(10), nullable=False)  # 24h | 1h
    sent_at       = db.Column(db.DateTime, default=datetime.utcnow)


class EmailCampaign(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    admin_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    subject     = db.Column(db.String(300), nullable=False)
    target      = db.Column(db.String(30), default='students')
    total_sent  = db.Column(db.Integer, default=0)
    total_failed= db.Column(db.Integer, default=0)
    batch_size  = db.Column(db.Integer, default=50)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


class CourseCertificate(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_id        = db.Column(db.Integer, db.ForeignKey('course.id'), nullable=False)
    certificate_code = db.Column(db.String(32), unique=True, nullable=False)
    issued_at        = db.Column(db.DateTime, default=datetime.utcnow)
    user             = db.relationship('User', backref='certificates', lazy=True)
    course           = db.relationship('Course', backref='certificates', lazy=True)


class CommercialLead(db.Model):
    __tablename__ = 'commercial_lead'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(200), nullable=False)
    email      = db.Column(db.String(120), nullable=False)
    privacy_accepted = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_token'
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token      = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at    = db.Column(db.DateTime, nullable=True)
    user       = db.relationship('User', backref='password_reset_tokens', lazy=True)
