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
        return self.subscription_status == 'active'


class SubscriptionPlan(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120), nullable=False)
    description     = db.Column(db.Text, default='')
    price_monthly   = db.Column(db.Float, default=0.0)
    price_yearly    = db.Column(db.Float, default=0.0)
    stripe_price_id = db.Column(db.String(120), default='')
    stripe_price_id_yearly = db.Column(db.String(120), default='')
    trial_days      = db.Column(db.Integer, default=0)
    stripe_coupon_id = db.Column(db.String(120), default='')
    is_active       = db.Column(db.Boolean, default=True)
    sort_order      = db.Column(db.Integer, default=0)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class Category(db.Model):
    id    = db.Column(db.Integer, primary_key=True)
    name  = db.Column(db.String(50), unique=True, nullable=False)
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
