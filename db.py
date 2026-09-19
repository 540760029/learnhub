"""
数据层：SQLAlchemy 2.0 模型定义 + 会话管理

设计要点（为后续迁移到 Postgres / Supabase 做准备）：
  * 全部使用通用类型（String/Text/Integer/Float/DateTime/JSON），不用 SQLite 方言；
  * 主键用自增整型，迁移到 PG 时改成 Identity 即可；
  * 所有时间统一存 UTC naive datetime；
  * 多教师隔离靠 courses.teacher_id，多学生靠 enrollments，不依赖任何"单例"假设。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import config  # noqa: F401  —— 必须先加载 .env，再读下面的环境变量

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey,
                        Integer, String, Table, Text, UniqueConstraint,
                        create_engine)
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column, relationship,
                            sessionmaker)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "learnhub.db")
# 注意：.env 里写了 LEARNHUB_DB_URL= 但留空时，os.environ.get 会拿到空串，
# 直接丢给 create_engine 会报 "Could not parse SQLAlchemy URL"。
# 所以这里把「空值」等同于「没配置」，统一回落到本地 SQLite。
DB_URL = (os.environ.get("LEARNHUB_DB_URL") or "").strip() or f"sqlite:///{DB_PATH}"

engine = create_engine(
    DB_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def now() -> datetime:
    """统一 UTC naive 时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------- 关联表
enrollments = Table(
    "enrollments", Base.metadata,
    Column("course_id", ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
    Column("student_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime, default=now),
)

course_teachers = Table(
    "course_teachers", Base.metadata,
    Column("course_id", ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True),
    Column("teacher_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


# --------------------------------------------------------------------- 用户
class User(Base):
    """教师与学生共用一张表，用 role 区分（上千人规模下便于统一登录与统计）。"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16), default="student", index=True)  # teacher | student
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)   # 平台管理员
    password_hash: Mapped[str] = mapped_column(String(255))
    school: Mapped[str | None] = mapped_column(String(120))
    student_no: Mapped[str | None] = mapped_column(String(40), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    # 默认 AI 配置（学生可覆盖成自己的 key → 解锁无限次）
    ai_provider: Mapped[str | None] = mapped_column(String(32))
    ai_api_key_enc: Mapped[str | None] = mapped_column(Text)      # 加密存储，绝不明文
    ai_base_url: Mapped[str | None] = mapped_column(String(200))
    ai_model: Mapped[str | None] = mapped_column(String(80))

    courses_owned: Mapped[list["Course"]] = relationship(
        back_populates="owner", foreign_keys="Course.teacher_id")
    courses_joined: Mapped[list["Course"]] = relationship(
        secondary=enrollments, back_populates="students")
    courses_assisting: Mapped[list["Course"]] = relationship(
        secondary=course_teachers, back_populates="teachers")


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    cover_emoji: Mapped[str] = mapped_column(String(8), default="📘")
    join_code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    owner: Mapped[User] = relationship(back_populates="courses_owned", foreign_keys=[teacher_id])
    teachers: Mapped[list[User]] = relationship(
        secondary=course_teachers, back_populates="courses_assisting")
    students: Mapped[list[User]] = relationship(
        secondary=enrollments, back_populates="courses_joined")

    knowledge_points: Mapped[list["KnowledgePoint"]] = relationship(
        back_populates="course", cascade="all, delete-orphan", order_by="KnowledgePoint.order_no")
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="course", cascade="all, delete-orphan")
    quiz_sets: Mapped[list["QuizSet"]] = relationship(
        back_populates="course", cascade="all, delete-orphan")
    announcements: Mapped[list["Announcement"]] = relationship(
        back_populates="course", cascade="all, delete-orphan")


class KnowledgePoint(Base):
    """知识点。scope 决定可见性：
       course  = 全班可见（教师发布）
       teacher = 仅教师可见（教师草稿/内部资料）
       private = 仅创建者本人可见（学生自己整理的笔记）
    """
    __tablename__ = "knowledge_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")      # Markdown
    order_no: Mapped[int] = mapped_column(Integer, default=0)
    scope: Mapped[str] = mapped_column(String(16), default="course", index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source_file: Mapped[str | None] = mapped_column(String(255))   # 由哪个文件解析而来
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    course: Mapped[Course] = relationship(back_populates="knowledge_points")


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    full_score: Mapped[float] = mapped_column(Float, default=100.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    course: Mapped[Course] = relationship(back_populates="assignments")
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("assignment_id", "student_id", name="uq_submission"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    attachment_url: Mapped[str | None] = mapped_column(String(500))
    score: Mapped[float | None] = mapped_column(Float)
    feedback: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime)

    assignment: Mapped[Assignment] = relationship(back_populates="submissions")
    student: Mapped[User] = relationship()


class QuizSet(Base):
    """一套试题。

    source : manual 手工组卷 / ai 生成 / kp 按知识点生成
    scope  : course  全班可见（教师发布的试卷 → 所有学生和教师都能看到）
             teacher 仅教师可见（教师内部卷）
             private 仅创建者本人可见（学生自己 AI 生成的题）
    """
    __tablename__ = "quiz_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16), default="manual")   # manual | ai | kp
    scope: Mapped[str] = mapped_column(String(16), default="course", index=True)
    kp_ids: Mapped[list | None] = mapped_column(JSON)                   # AI 卷关联的知识点
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    course: Mapped[Course] = relationship(back_populates="quiz_sets")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="quiz_set", cascade="all, delete-orphan", order_by="Question.order_no")
    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="quiz_set", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_set_id: Mapped[int] = mapped_column(ForeignKey("quiz_sets.id", ondelete="CASCADE"), index=True)
    qtype: Mapped[str] = mapped_column(String(16), default="single")   # single | multi | judge | short
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list | None] = mapped_column(JSON)                 # ["A. ...", ...]
    answer: Mapped[str] = mapped_column(Text, default="")              # "A" / "AB" / "对" / 参考答案
    analysis: Mapped[str] = mapped_column(Text, default="")            # 解析
    difficulty: Mapped[int] = mapped_column(Integer, default=3)        # 1-5
    kp_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_points.id", ondelete="SET NULL"))
    order_no: Mapped[int] = mapped_column(Integer, default=0)

    quiz_set: Mapped[QuizSet] = relationship(back_populates="questions")


class Attempt(Base):
    """一次答题记录（学生做完一套题）。"""
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_set_id: Mapped[int] = mapped_column(ForeignKey("quiz_sets.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    answers: Mapped[dict | None] = mapped_column(JSON)                 # {"question_id": "A"}
    score: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[list | None] = mapped_column(JSON)                  # 每题对错，供薄弱点统计
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)

    quiz_set: Mapped[QuizSet] = relationship(back_populates="attempts")
    student: Mapped[User] = relationship()


class AiUsage(Base):
    """AI 出题额度计数：默认 key 每天 3 套；自带 key 不计数（used_own_key=True）。"""
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)           # YYYY-MM-DD (UTC)
    used: Mapped[int] = mapped_column(Integer, default=0)
    used_own_key: Mapped[int] = mapped_column(Integer, default=0)
    last_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WeakStat(Base):
    """学生薄弱知识点统计——AI 出题的依据，也是教师看板的数据来源。"""
    __tablename__ = "weak_stats"
    __table_args__ = (UniqueConstraint("student_id", "kp_id", name="uq_weak"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kp_id: Mapped[int] = mapped_column(ForeignKey("knowledge_points.id", ondelete="CASCADE"), index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    wrong: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    course: Mapped[Course] = relationship(back_populates="announcements")


class Setting(Base):
    """平台级配置（键值对）。

    主要用来存**管理员在后台配置的平台默认 AI API Key**——
    这份 Key 对所有用户生效：教师/学生用它时每日限次，管理员不受限。
    """
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# 平台 AI 配置在 settings 表里的键名
SK_PLATFORM_PROVIDER = "platform_ai_provider"
SK_PLATFORM_KEY_ENC = "platform_ai_api_key_enc"      # 加密后存储
SK_PLATFORM_BASE_URL = "platform_ai_base_url"
SK_PLATFORM_MODEL = "platform_ai_model"
SK_PLATFORM_ENABLED = "platform_ai_enabled"
SK_DAILY_LIMIT = "daily_ai_limit"


def get_setting(db, key: str, default: str | None = None) -> str | None:
    row = db.get(Setting, key)
    return row.value if row and row.value is not None else default


def set_setting(db, key: str, value: str | None) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
        row.updated_at = now()
    else:
        db.add(Setting(key=key, value=value))


# --------------------------------------------------------------------- 工具
def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()


# 增量迁移：给已存在的库补上后加的列，避免老数据库启动即报错。
# 生产环境建议换成 Alembic，这里用最小实现保证 Demo 平滑升级。
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "is_admin", "BOOLEAN DEFAULT 0"),
    ("knowledge_points", "scope", "VARCHAR(16) DEFAULT 'course'"),
    ("knowledge_points", "created_by", "INTEGER"),
    ("knowledge_points", "source_file", "VARCHAR(255)"),
    ("quiz_sets", "scope", "VARCHAR(16) DEFAULT 'course'"),
]


def _migrate() -> None:
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in existing_tables:
                continue
            cols = {c["name"] for c in insp.get_columns(table)}
            if column in cols:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            print(f"[migrate] {table}.{column} 已补齐")
        # 历史数据默认归到"全班可见"
        if "knowledge_points" in existing_tables:
            conn.execute(text("UPDATE knowledge_points SET scope='course' WHERE scope IS NULL"))
        if "quiz_sets" in existing_tables:
            conn.execute(text("UPDATE quiz_sets SET scope='course' WHERE scope IS NULL"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
