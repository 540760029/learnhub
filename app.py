"""
LearnHub · 课程学习与 AI 出题平台 —— FastAPI 后端

本地运行：
    set PYTHONPATH=..\\_libs
    python -m uvicorn app:app --reload --port 8000
然后浏览器打开 http://127.0.0.1:8000

设计说明
--------
* 多教师多租户：课程归属 teacher_id，教师只能操作自己的课程；
* 学生只能看到自己选的课、自己的答题记录；
* AI Key 三层优先级：学生自带 > 课程教师 > 平台默认；
  学生自带 key 无限次，其余每日 3 套（可在环境变量调整）；
* 所有 key 加密落库，接口只回显后 4 位。
"""
from __future__ import annotations

import io
import json
import os
import random
import re
import string
import zipfile
from datetime import date, datetime, timedelta

from fastapi import (Depends, FastAPI, File, Header, HTTPException, Query,
                     UploadFile)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import llm
from db import (Announcement, Assignment, Attempt, AiUsage, Course, KnowledgePoint,
                Question, QuizSet, SessionLocal, Submission, User, WeakStat,
                enrollments, init_db, now)
from security import (create_token, decode_token, encrypt_secret, hash_password,
                      mask_key, verify_password)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DAILY_AI_LIMIT = int(os.environ.get("LEARNHUB_DAILY_AI_LIMIT", "3"))

app = FastAPI(title="LearnHub API", version="0.1.0",
              description="课程学习 + AI 出题平台（本地 Demo）")


# ===================================================================== 依赖
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(authorization: str | None = Header(default=None),
                 db: Session = Depends(get_db)) -> User:
    token = ""
    if authorization:
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") \
            else authorization.strip()
    payload = decode_token(token) if token else None
    if not payload:
        raise HTTPException(401, "未登录或登录已过期")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(401, "账号不存在或已停用")
    return user


def teacher_only(user: User = Depends(current_user)) -> User:
    if user.role != "teacher" and not user.is_admin:
        raise HTTPException(403, "该操作仅教师可用")
    return user


def admin_only(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "该操作仅平台管理员可用")
    return user


def _course_role(db: Session, course: Course, user: User) -> str:
    """返回 'admin' | 'owner' | 'teacher' | 'student' | 'none'"""
    if user.is_admin:
        return "admin"
    if course.teacher_id == user.id:
        return "owner"
    if any(t.id == user.id for t in course.teachers):
        return "teacher"
    if user.role == "student" and any(s.id == user.id for s in course.students):
        return "student"
    return "none"


def require_course_access(db: Session, course_id: int, user: User, *, manage: bool = False) -> tuple[Course, str]:
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(404, "课程不存在")
    role = _course_role(db, course, user)
    if manage:
        if role not in ("admin", "owner", "teacher"):
            raise HTTPException(403, "只有该课程的教师可以执行此操作")
    elif role == "none":
        raise HTTPException(403, "你不在该课程中，请先加入课程")
    return course, role


# ---------- 可见性（scope）过滤 ----------
# course  : 全班可见（教师发布）
# teacher : 仅教师可见（教师草稿 / 内部卷）
# private : 仅创建者本人可见（学生自建笔记 / 学生 AI 生成的题）
def _can_manage(role: str) -> bool:
    return role in ("admin", "owner", "teacher")


def visible_kps(course: Course, role: str, user: User) -> list[KnowledgePoint]:
    if _can_manage(role):
        return list(course.knowledge_points)
    return [k for k in course.knowledge_points
            if k.scope == "course" or (k.scope == "private" and k.created_by == user.id)]


def visible_quizzes(course: Course, role: str, user: User) -> list[QuizSet]:
    if _can_manage(role):
        return list(course.quiz_sets)
    return [q for q in course.quiz_sets
            if q.scope == "course" or (q.scope == "private" and q.created_by == user.id)]


def may_view_quiz(qs: QuizSet, role: str, user: User) -> bool:
    if _can_manage(role):
        return True
    if qs.scope == "teacher":           # 教师内部卷，学生一律不可见
        return False
    if qs.scope == "private":
        return qs.created_by == user.id
    return True                          # course


def may_view_kp(kp: KnowledgePoint, role: str, user: User) -> bool:
    if _can_manage(role):
        return True
    if kp.scope == "teacher":
        return False
    if kp.scope == "private":
        return kp.created_by == user.id
    return True


# ===================================================================== Schema
class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=160)
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=6, max_length=128)
    role: str = Field(pattern="^(teacher|student)$")
    school: str | None = None
    student_no: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class CourseIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str | None = None
    cover_emoji: str = "📘"


class CoursePatch(BaseModel):
    title: str | None = None
    description: str | None = None
    cover_emoji: str | None = None
    is_published: bool | None = None


class JoinIn(BaseModel):
    join_code: str = Field(min_length=4, max_length=12)


class KpIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = ""
    order_no: int = 0
    scope: str | None = Field(default=None, pattern="^(course|teacher|private)$")


class KpCommitIn(BaseModel):
    """确认把「文件解析出的知识点」写库。"""
    source_file: str | None = None
    scope: str = Field(default="course", pattern="^(course|teacher|private)$")
    points: list[dict]


class QuizScopeIn(BaseModel):
    scope: str = Field(pattern="^(course|teacher|private)$")


class AdminUserPatch(BaseModel):
    role: str | None = Field(default=None, pattern="^(teacher|student)$")
    is_admin: bool | None = None
    is_active: bool | None = None


class PlatformAiIn(BaseModel):
    """管理员配置平台默认 AI。api_key 留空表示不改动现有 Key。"""
    provider: str | None = Field(default=None, pattern="^(deepseek|openai|dashscope|zhipu|moonshot|custom)$")
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    enabled: bool | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=200)
    clear_key: bool = False


class AssignmentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = ""
    due_at: str | None = None            # ISO 字符串
    full_score: float = 100.0


class SubmitIn(BaseModel):
    content: str = ""


class GradeIn(BaseModel):
    score: float
    feedback: str | None = None


class QuizIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    questions: list[dict]


class GenIn(BaseModel):
    course_id: int
    kp_ids: list[int] = []
    count: int = Field(default=5, ge=1, le=20)
    qtype: str = Field(default="single", pattern="^(single|multi|judge|mixed)$")
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard|mixed)$")
    scope: str = Field(default="auto", pattern="^(auto|course|teacher|private)$")
    extra: str = ""


class AttemptIn(BaseModel):
    answers: dict[str, str] = {}
    duration_sec: int = 0


class AiKeyIn(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = ""
    model: str = ""


# ===================================================================== 工具
def rand_code(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def today_str() -> str:
    return date.today().isoformat()


def get_usage(db: Session, user_id: int) -> AiUsage:
    day = today_str()
    row = db.scalar(select(AiUsage).where(AiUsage.user_id == user_id, AiUsage.day == day))
    if not row:
        row = AiUsage(user_id=user_id, day=day, used=0, used_own_key=0)
        db.add(row)
        db.flush()
    return row


def daily_limit(db: Session) -> int:
    """每日出题套数上限：管理员可在后台调整，缺省取默认值。"""
    from db import SK_DAILY_LIMIT, get_setting
    try:
        v = int(get_setting(db, SK_DAILY_LIMIT, str(DAILY_AI_LIMIT)) or DAILY_AI_LIMIT)
        return max(1, min(v, 200))
    except (TypeError, ValueError):
        return DAILY_AI_LIMIT


def ai_quota_info(db: Session, user: User) -> dict:
    """
    额度规则：
      * 使用自己的 Key                        → 无限
      * 管理员（用管理员配置的平台默认 Key）    → 无限
      * 教师 / 学生（用教师或平台默认 Key）     → 每日 N 套（N 可由管理员配置）
    """
    row = get_usage(db, user.id)
    has_own = bool(user.ai_api_key_enc)
    limit = daily_limit(db)
    unlimited = has_own or bool(user.is_admin)
    reason = "own_key" if has_own else ("admin" if user.is_admin else None)
    return {
        "has_own_key": has_own,
        "is_admin": bool(user.is_admin),
        "limit": None if unlimited else limit,
        "used": row.used,
        "remaining": None if unlimited else max(0, limit - row.used),
        "unlimited": unlimited,
        "reason": reason,
    }


def bump_weak_stats(db: Session, student_id: int, detail: list[dict]) -> None:
    """把每次答题结果累加到薄弱知识点统计（AI 出题与教师看板的数据来源）。

    注意：同一份答卷里可能有多道题属于同一个知识点，所以累计时要先 flush，
    否则下一次 select 查不到刚 add 的行，会重复插入触发唯一约束冲突。
    """
    for item in detail:
        kp_id = item.get("kp_id")
        if not kp_id:
            continue
        row = db.scalar(select(WeakStat).where(WeakStat.student_id == student_id,
                                               WeakStat.kp_id == kp_id))
        if not row:
            row = WeakStat(student_id=student_id, kp_id=kp_id, total=0, wrong=0)
            db.add(row)
            db.flush()
        row.total += 1
        if not item.get("correct"):
            row.wrong += 1
        row.updated_at = now()


def grade_answers(questions: list[Question], answers: dict[str, str]) -> tuple[float, list[dict]]:
    score, detail = 0.0, []
    for q in questions:
        given = (answers.get(str(q.id)) or "").strip().upper().replace(" ", "")
        expect = (q.answer or "").strip().upper().replace(" ", "")
        correct = bool(expect) and given == expect
        if correct:
            score += 1
        detail.append({"question_id": q.id, "kp_id": q.kp_id, "correct": correct,
                       "given": given, "answer": q.answer})
    return score, detail


def _answered(answers: dict | None, qid: int) -> bool:
    """该题是否作答（多选题选了任意选项就算作答）。"""
    v = (answers or {}).get(str(qid))
    return bool(str(v).strip()) if v is not None else False


def review_summary(questions: list[Question], answers: dict | None,
                   detail: list[dict] | None = None) -> dict:
    """
    交卷后的答题小结 —— 供前端显示「共几题 / 答了几题 / 哪些没答 / 哪些答错」，
    并支持点击跳转到未作答的题。

    注意：「未作答」与「答错」分开统计 —— 学生需要知道自己是漏做了还是做错了。
    """
    wrong_ids = {d.get("question_id") for d in (detail or []) if not d.get("correct")}
    answered_ids, unanswered_ids = [], []
    for q in questions:
        (answered_ids if _answered(answers, q.id) else unanswered_ids).append(q.id)
    q_index = {q.id: i + 1 for i, q in enumerate(questions)}
    wrong_answered = [qid for qid in wrong_ids if qid in q_index and qid not in unanswered_ids]
    return {
        "total": len(questions),
        "answered_count": len(answered_ids),
        "unanswered_count": len(unanswered_ids),
        "wrong_count": len(wrong_answered),
        "correct_count": len(answered_ids) - len(wrong_answered),
        "answered_ids": answered_ids,
        "unanswered_ids": unanswered_ids,
        # 供前端直接渲染「第 N 题」并点击滚动定位
        "unanswered": [{"id": qid, "no": q_index[qid]} for qid in unanswered_ids],
        "wrong": [{"id": qid, "no": q_index[qid]} for qid in wrong_answered],
    }


def review_payload(questions: list[Question], answers: dict | None,
                   detail: list[dict] | None) -> list[dict]:
    """逐题下发：学生作答、正确答案、详细解析（correct 标记与选项一致）。"""
    dmap = {d.get("question_id"): d.get("correct") for d in (detail or [])}
    return [{
        "id": q.id, "stem": q.stem, "options": q.options, "qtype": q.qtype,
        "answer": q.answer or "",
        "analysis": (q.analysis or "").strip() or "（本题暂无解析）",
        "difficulty": q.difficulty if isinstance(q.difficulty, int) else 3,
        "kp_id": q.kp_id,
        "given": str((answers or {}).get(str(q.id)) or ""),
        "answered": _answered(answers, q.id),
        "correct": bool(dmap.get(q.id)),
    } for q in questions]


def quiz_public(qs: QuizSet, with_answer: bool) -> dict:
    return {
        "id": qs.id, "title": qs.title, "source": qs.source,
        "course_id": qs.course_id, "created_at": qs.created_at.isoformat(),
        "question_count": len(qs.questions),
        "questions": [{
            "id": q.id, "qtype": q.qtype, "stem": q.stem, "options": q.options,
            "difficulty": q.difficulty, "kp_id": q.kp_id, "order_no": q.order_no,
            **({"answer": q.answer, "analysis": q.analysis} if with_answer else {}),
        } for q in sorted(qs.questions, key=lambda x: x.order_no)],
    }


# ===================================================================== 认证
@app.post("/api/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(400, "该邮箱已注册")
    user = User(email=email, name=body.name.strip(), role=body.role,
                password_hash=hash_password(body.password),
                school=body.school, student_no=body.student_no)
    db.add(user)
    db.commit()
    return {"token": create_token(user.id, user.role, user.name),
            "user": {"id": user.id, "name": user.name, "role": user.role, "email": user.email}}


@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(400, "邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已停用")
    return {"token": create_token(user.id, user.role, user.name),
            "user": {"id": user.id, "name": user.name, "role": user.role,
                     "email": user.email, "school": user.school}}


@app.get("/api/me")
def me(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {
        "id": user.id, "name": user.name, "role": user.role, "email": user.email,
        "is_admin": bool(user.is_admin),
        "school": user.school, "student_no": user.student_no,
        "ai": {"provider": user.ai_provider or llm.DEFAULT_PROVIDER,
               "base_url": user.ai_base_url or "", "model": user.ai_model or "",
               "key_masked": mask_key(_peek_key(user)), "has_key": bool(user.ai_api_key_enc)},
        "quota": ai_quota_info(db, user),
    }


def _peek_key(user: User) -> str:
    if not user.ai_api_key_enc:
        return ""
    try:
        from security import decrypt_secret
        return decrypt_secret(user.ai_api_key_enc)
    except Exception:
        return ""


@app.post("/api/me/ai-key")
def save_ai_key(body: AiKeyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """保存用户自己的 API Key（加密入库）。留空 api_key 表示清空，回到平台/教师额度。"""
    if body.provider not in llm.PROVIDERS:
        raise HTTPException(400, "不支持的 AI 服务商")
    user.ai_provider = body.provider
    user.ai_base_url = (body.base_url or "").strip() or None
    user.ai_model = (body.model or "").strip() or None
    user.ai_api_key_enc = encrypt_secret(body.api_key.strip()) if body.api_key.strip() else None
    db.commit()
    return {"ok": True, **ai_quota_info(db, user)}


@app.get("/api/ai/providers")
def ai_providers(db: Session = Depends(get_db)):
    plat = llm.platform_config(db)
    return {"default": llm.DEFAULT_PROVIDER,
            "platform_key_configured": plat["configured"] and plat["enabled"],
            "daily_limit": daily_limit(db),
            "scopes": [
                {"id": "course", "label": "全班可见（学生都能看到并作答）"},
                {"id": "teacher", "label": "仅教师可见（学生看不到）"},
                {"id": "private", "label": "仅自己可见"},
            ],
            "providers": [{"id": k, "label": v["label"], "base_url": v["base_url"],
                           "model": v["model"]} for k, v in llm.PROVIDERS.items()]}


# ===================================================================== 课程
@app.get("/api/courses")
def list_courses(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role == "teacher" or user.is_admin:
        owned = db.scalars(select(Course).where(Course.teacher_id == user.id)
                           .order_by(Course.created_at.desc())).all()
        assisting = [c for c in user.courses_assisting if c.teacher_id != user.id]
        return {"teaching": [_course_card(db, c, "owner") for c in owned],
                "assisting": [_course_card(db, c, "teacher") for c in assisting],
                "joined": []}
    return {"teaching": [], "assisting": [],
            "joined": [_course_card(db, c, "student") for c in
                       sorted(user.courses_joined, key=lambda x: x.created_at, reverse=True)]}


def _course_card(db: Session, c: Course, role: str) -> dict:
    return {"id": c.id, "title": c.title, "description": c.description,
            "cover_emoji": c.cover_emoji, "join_code": c.join_code if role != "student" else None,
            "is_published": c.is_published, "teacher": c.owner.name, "role": role,
            "student_count": len(c.students),
            "kp_count": len(c.knowledge_points),
            "assignment_count": len(c.assignments),
            "quiz_count": len(c.quiz_sets)}


@app.post("/api/courses")
def create_course(body: CourseIn, db: Session = Depends(get_db), user: User = Depends(teacher_only)):
    code = rand_code()
    while db.scalar(select(Course).where(Course.join_code == code)):
        code = rand_code()
    c = Course(title=body.title.strip(), description=body.description,
               cover_emoji=body.cover_emoji or "📘", join_code=code, teacher_id=user.id)
    db.add(c)
    db.commit()
    return _course_card(db, c, "owner")


@app.get("/api/courses/{cid}")
def course_detail(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    course, role = require_course_access(db, cid, user)
    kps = visible_kps(course, role, user)
    quizzes = visible_quizzes(course, role, user)
    # 一次查出本人在这门课的答题记录，便于前端显示"已作答/最高分"
    my_attempts: dict[int, list] = {}
    for a in db.scalars(select(Attempt).where(Attempt.student_id == user.id)
                        .order_by(Attempt.submitted_at)).all():
        my_attempts.setdefault(a.quiz_set_id, []).append(a)
    return {"course": _course_card(db, course, role),
            "knowledge_points": [{"id": k.id, "title": k.title, "content": k.content,
                                  "order_no": k.order_no, "scope": k.scope,
                                  "created_by": k.created_by,
                                  "source_file": k.source_file,
                                  "mine": k.created_by == user.id} for k in
                                 sorted(kps, key=lambda x: (x.order_no, x.id))],
            "assignments": [{"id": a.id, "title": a.title, "due_at": a.due_at.isoformat() if a.due_at else None,
                             "full_score": a.full_score, "content": a.content,
                             "submission_count": len(a.submissions)} for a in
                            sorted(course.assignments, key=lambda x: x.created_at, reverse=True)],
            "quiz_sets": [{"id": q.id, "title": q.title, "source": q.source, "scope": q.scope,
                           "question_count": len(q.questions),
                           "created_by": q.created_by, "mine": q.created_by == user.id,
                           "attempt_count": len(my_attempts.get(q.id, [])),
                           "best_score": (max((x.score for x in my_attempts[q.id]), default=None)
                                          if my_attempts.get(q.id) else None),
                           "created_at": q.created_at.isoformat()} for q in
                          sorted(quizzes, key=lambda x: x.created_at, reverse=True)],
            "announcements": [{"id": a.id, "content": a.content,
                               "created_at": a.created_at.isoformat()} for a in
                              sorted(course.announcements, key=lambda x: x.created_at, reverse=True)],
            "can_manage": _can_manage(role),
            "my_role": role}


@app.patch("/api/courses/{cid}")
def patch_course(cid: int, body: CoursePatch, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    course, _ = require_course_access(db, cid, user, manage=True)
    for field in ("title", "description", "cover_emoji", "is_published"):
        val = getattr(body, field)
        if val is not None:
            setattr(course, field, val)
    db.commit()
    return {"ok": True}


@app.post("/api/courses/join")
def join_course(body: JoinIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role != "student":
        raise HTTPException(400, "只有学生可以通过邀请码加入课程")
    code = body.join_code.strip().upper()
    course = db.scalar(select(Course).where(Course.join_code == code))
    if not course:
        raise HTTPException(404, "邀请码无效")
    if any(s.id == user.id for s in course.students):
        return {"ok": True, "course_id": course.id, "message": "你已在该课程中"}
    course.students.append(user)
    db.commit()
    return {"ok": True, "course_id": course.id, "message": f"已加入《{course.title}》"}


@app.get("/api/courses/{cid}/students")
def course_students(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    course, _ = require_course_access(db, cid, user, manage=True)
    out = []
    for s in sorted(course.students, key=lambda x: x.name):
        out.append({"id": s.id, "name": s.name, "email": s.email, "student_no": s.student_no,
                    "attempts": db.scalar(select(func.count()).select_from(Attempt)
                                          .where(Attempt.student_id == s.id)) or 0})
    return {"students": out}


@app.post("/api/courses/{cid}/announcements")
def add_announcement(cid: int, body: dict, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    course, _ = require_course_access(db, cid, user, manage=True)
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "内容不能为空")
    a = Announcement(course_id=course.id, content=content)
    db.add(a)
    db.commit()
    return {"ok": True, "id": a.id}


# ===================================================================== 知识点
MAX_UPLOAD_BYTES = 8 * 1024 * 1024          # 单文件上限 8MB


def extract_file_text(filename: str, raw: bytes) -> str:
    """从上传文件中抽取纯文本。支持 txt/md/csv/json + docx（docx 本质是 zip+xml）。"""
    name = (filename or "").lower()
    if name.endswith(".docx"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
        except Exception:
            raise HTTPException(400, "无法解析该 .docx 文件，请确认未损坏")
        xml = re.sub(r"</w:p\s*>", "\n", xml)              # 段落 → 换行
        xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)                  # 去掉所有标签
        text = (text.replace("&amp;", "&").replace("&lt;", "<")
                    .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))
    elif name.endswith((".txt", ".md", ".markdown", ".csv", ".json", ".py", ".html")):
        for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HTTPException(400, "无法识别文件编码，请另存为 UTF-8 后重试")
    elif name.endswith(".pdf"):
        raise HTTPException(400, "暂不支持 PDF 直接解析，请先转成 Word 或 txt 再上传")
    else:
        raise HTTPException(400, "支持的文件类型：.docx / .txt / .md / .csv / .json")

    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 20:
        raise HTTPException(400, "文件里没有解析出有效文字内容")
    return text


@app.post("/api/courses/{cid}/knowledge/extract")
async def extract_knowledge(cid: int, file: UploadFile = File(...),
                            db: Session = Depends(get_db), user: User = Depends(current_user)):
    """上传资料 → AI 整理出知识点列表（只返回预览，不入库，等老师确认）。"""
    course, role = require_course_access(db, cid, user, manage=True)
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "文件太大（上限 8MB）")
    text = extract_file_text(file.filename or "", raw)

    cfg = llm.resolve_ai_config(db, user, course)
    try:
        points, used = llm.extract_knowledge_points(
            provider=cfg["provider"], api_key=cfg["api_key"], base_url=cfg["base_url"],
            model=cfg["model"], text=text, course_title=course.title)
    except llm.LlmError as e:
        raise HTTPException(400, f"AI 整理失败：{e}")

    return {"filename": file.filename, "chars": len(text), "provider": used,
            "notice": "未配置 API Key，已按标题/段落做启发式切分" if used == "mock" else None,
            "points": points}


@app.post("/api/courses/{cid}/knowledge/commit")
def commit_knowledge(cid: int, body: KpCommitIn, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """确认入库：把（可能被老师编辑过的）知识点批量保存。"""
    course, role = require_course_access(db, cid, user, manage=True)
    scope = body.scope if body.scope in ("course", "teacher", "private") else "course"
    base = max([k.order_no for k in course.knowledge_points], default=-1) + 1
    created = []
    for i, p in enumerate(body.points):
        title = str(p.get("title") or "").strip()
        if not title:
            continue
        k = KnowledgePoint(course_id=course.id, title=title[:200],
                           content=str(p.get("content") or ""),
                           order_no=base + i, scope=scope,
                           created_by=user.id, source_file=body.source_file)
        db.add(k)
        created.append(title)
    if not created:
        raise HTTPException(400, "没有可保存的知识点")
    db.commit()
    return {"ok": True, "created": len(created), "titles": created, "scope": scope}


@app.post("/api/courses/{cid}/knowledge")
def add_kp(cid: int, body: KpIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """教师发布知识点 / 学生添加自己的课程笔记（学生只能建 private）。"""
    if user.role == "student":
        course, role = require_course_access(db, cid, user)      # 学生只需在课程中
        scope = "private"                                        # 强制仅本人可见
    else:
        course, role = require_course_access(db, cid, user, manage=True)
        scope = body.scope or "course"
    k = KnowledgePoint(course_id=course.id, title=body.title.strip(),
                       content=body.content, order_no=body.order_no,
                       scope=scope, created_by=user.id)
    db.add(k)
    db.commit()
    return {"ok": True, "id": k.id, "scope": k.scope}


@app.patch("/api/knowledge/{kid}")
def edit_kp(kid: int, body: KpIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    k = db.get(KnowledgePoint, kid)
    if not k:
        raise HTTPException(404, "知识点不存在")
    course, role = require_course_access(db, k.course_id, user)
    # 教师可改课程内任意知识点；学生只能改自己创建的私有笔记
    if not _can_manage(role) and not (k.scope == "private" and k.created_by == user.id):
        raise HTTPException(403, "你只能修改自己添加的知识点")
    k.title, k.content, k.order_no = body.title.strip(), body.content, body.order_no
    if body.scope and _can_manage(role):
        k.scope = body.scope
    db.commit()
    return {"ok": True}


@app.delete("/api/knowledge/{kid}")
def delete_kp(kid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    k = db.get(KnowledgePoint, kid)
    if not k:
        raise HTTPException(404, "知识点不存在")
    course, role = require_course_access(db, k.course_id, user)
    if not _can_manage(role) and not (k.scope == "private" and k.created_by == user.id):
        raise HTTPException(403, "你只能删除自己添加的知识点")
    db.delete(k)
    db.commit()
    return {"ok": True}


# ===================================================================== 作业
@app.post("/api/courses/{cid}/assignments")
def add_assignment(cid: int, body: AssignmentIn, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    course, _ = require_course_access(db, cid, user, manage=True)
    due = None
    if body.due_at:
        try:
            from datetime import datetime
            due = datetime.fromisoformat(body.due_at.replace("Z", ""))
        except ValueError:
            raise HTTPException(400, "截止时间格式不正确，应为 ISO 格式")
    a = Assignment(course_id=course.id, title=body.title.strip(), content=body.content,
                   due_at=due, full_score=body.full_score)
    db.add(a)
    db.commit()
    return {"ok": True, "id": a.id}


@app.get("/api/assignments/{aid}")
def assignment_detail(aid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = db.get(Assignment, aid)
    if not a:
        raise HTTPException(404, "作业不存在")
    course, role = require_course_access(db, a.course_id, user)
    mine = None
    if role == "student":
        sub = db.scalar(select(Submission).where(Submission.assignment_id == aid,
                                                 Submission.student_id == user.id))
        if sub:
            mine = {"content": sub.content, "score": sub.score, "feedback": sub.feedback,
                    "submitted_at": sub.submitted_at.isoformat()}
    return {"id": a.id, "title": a.title, "content": a.content, "full_score": a.full_score,
            "due_at": a.due_at.isoformat() if a.due_at else None,
            "course_id": a.course_id, "course_title": a.course.title,
            "my_submission": mine,
            "submissions": [{"id": s.id, "student": s.student.name, "student_id": s.student_id,
                             "content": s.content, "score": s.score, "feedback": s.feedback,
                             "submitted_at": s.submitted_at.isoformat()} for s in a.submissions]
            if role in ("owner", "teacher") else []}


@app.post("/api/assignments/{aid}/submit")
def submit_assignment(aid: int, body: SubmitIn, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    a = db.get(Assignment, aid)
    if not a:
        raise HTTPException(404, "作业不存在")
    course, role = require_course_access(db, a.course_id, user)
    if role != "student":
        raise HTTPException(403, "只有学生可以提交作业")
    sub = db.scalar(select(Submission).where(Submission.assignment_id == aid,
                                             Submission.student_id == user.id))
    if sub:
        sub.content, sub.submitted_at = body.content, now()
    else:
        sub = Submission(assignment_id=aid, student_id=user.id, content=body.content)
        db.add(sub)
    db.commit()
    return {"ok": True, "id": sub.id}


@app.post("/api/submissions/{sid}/grade")
def grade_submission(sid: int, body: GradeIn, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    sub = db.get(Submission, sid)
    if not sub:
        raise HTTPException(404, "提交记录不存在")
    require_course_access(db, sub.assignment.course_id, user, manage=True)
    if body.score < 0 or body.score > sub.assignment.full_score:
        raise HTTPException(400, f"分数应在 0 ~ {sub.assignment.full_score} 之间")
    sub.score, sub.feedback, sub.graded_at = body.score, body.feedback, now()
    db.commit()
    return {"ok": True}


# ===================================================================== 试题
@app.post("/api/courses/{cid}/quizzes")
def create_quiz(cid: int, body: QuizIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """教师手工组卷。"""
    course, _ = require_course_access(db, cid, user, manage=True)
    if not body.questions:
        raise HTTPException(400, "至少要有一道题")
    qs = QuizSet(course_id=course.id, title=body.title.strip(), source="manual",
                 scope="course", created_by=user.id)
    db.add(qs)
    db.flush()
    missing = []
    for i, q in enumerate(body.questions):
        qtype = q.get("qtype", "single")
        if qtype not in ("single", "multi", "judge", "short"):
            qtype = "single"
        options = q.get("options")
        if not isinstance(options, list):
            options = None
        # 统一答案格式，保证学生端"正确选项标绿"一定匹配得上
        answer = llm.canonical_answer(q.get("answer"), options, qtype)
        analysis = str(q.get("analysis") or "").strip()
        if not analysis:
            missing.append(i + 1)
        db.add(Question(quiz_set_id=qs.id, qtype=qtype,
                        stem=str(q.get("stem") or "").strip(), options=options,
                        answer=answer, analysis=analysis,
                        difficulty=llm.normalize_difficulty(q.get("difficulty")),
                        kp_id=q.get("kp_id"), order_no=i))
    if missing:
        db.rollback()
        raise HTTPException(400, "第 %s 题缺少解析，请补充后再创建（解析是学生自学的关键）"
                            % "、".join(str(m) for m in missing))
    db.commit()
    return {"ok": True, "id": qs.id}


@app.patch("/api/quizzes/{qid}/scope")
def set_quiz_scope(qid: int, body: QuizScopeIn, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """教师把自己的试卷设为 全班可见 / 仅教师可见。"""
    qs = db.get(QuizSet, qid)
    if not qs:
        raise HTTPException(404, "试题不存在")
    course, role = require_course_access(db, qs.course_id, user, manage=True)
    if body.scope == "private" and not user.is_admin:
        raise HTTPException(400, "教师试卷不支持设为「仅自己可见」，请用「仅教师可见」")
    qs.scope = body.scope
    db.commit()
    return {"ok": True, "scope": qs.scope}


@app.get("/api/quizzes/{qid}")
def quiz_detail(qid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    qs = db.get(QuizSet, qid)
    if not qs:
        raise HTTPException(404, "试题不存在")
    course, role = require_course_access(db, qs.course_id, user)
    if not may_view_quiz(qs, role, user):
        raise HTTPException(403, "无权查看该试题")
    data = quiz_public(qs, with_answer=_can_manage(role))
    data["scope"] = qs.scope
    data["mine"] = qs.created_by == user.id
    data["can_manage"] = _can_manage(role)
    return data


@app.get("/api/quizzes/{qid}/overview")
def quiz_overview(qid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """教师视角：某套题的答案解析 + 全班作答情况。"""
    qs = db.get(QuizSet, qid)
    if not qs:
        raise HTTPException(404, "试题不存在")
    course, role = require_course_access(db, qs.course_id, user, manage=True)

    attempts = db.scalars(select(Attempt).where(Attempt.quiz_set_id == qid)
                          .order_by(Attempt.submitted_at.desc())).all()
    quiz = quiz_public(qs, with_answer=True)

    # 每题的正确率，帮助老师定位"哪道题全班都错"
    per_q: dict[int, dict] = {}
    for q in qs.questions:
        per_q[q.id] = {"question_id": q.id, "stem": q.stem, "total": 0, "correct": 0}
    for a in attempts:
        for d in (a.detail or []):
            qid_ = d.get("question_id")
            if qid_ in per_q:
                per_q[qid_]["total"] += 1
                if d.get("correct"):
                    per_q[qid_]["correct"] += 1
    questions_stat = []
    for q in sorted(qs.questions, key=lambda x: x.order_no):
        st = per_q[q.id]
        questions_stat.append({**st,
                               "accuracy": round(st["correct"] / st["total"] * 100, 1)
                               if st["total"] else None})

    scores = [a.score / a.total * 100 for a in attempts if a.total]
    return {
        "quiz": quiz,
        "scope": qs.scope,
        "stats": {
            "attempt_count": len(attempts),
            "student_count": len({a.student_id for a in attempts}),
            "avg_accuracy": round(sum(scores) / len(scores), 1) if scores else None,
            "highest": round(max(scores), 1) if scores else None,
            "lowest": round(min(scores), 1) if scores else None,
        },
        "questions_stat": questions_stat,
        "attempts": [{
            "id": a.id, "student_id": a.student_id, "student": a.student.name,
            "student_no": a.student.student_no,
            "score": a.score, "total": a.total,
            "accuracy": round(a.score / a.total * 100, 1) if a.total else 0,
            "submitted_at": a.submitted_at.isoformat(),
            "wrong": [d.get("question_id") for d in (a.detail or []) if not d.get("correct")],
        } for a in attempts],
    }


@app.delete("/api/quizzes/{qid}")
def delete_quiz(qid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    qs = db.get(QuizSet, qid)
    if not qs:
        raise HTTPException(404, "试题不存在")
    require_course_access(db, qs.course_id, user, manage=True)
    db.delete(qs)
    db.commit()
    return {"ok": True}


@app.post("/api/quizzes/{qid}/submit")
def submit_quiz(qid: int, body: AttemptIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    qs = db.get(QuizSet, qid)
    if not qs:
        raise HTTPException(404, "试题不存在")
    course, role = require_course_access(db, qs.course_id, user)
    if not may_view_quiz(qs, role, user):
        raise HTTPException(403, "无权作答该试题")
    if role != "student":
        raise HTTPException(403, "只有学生可以作答")
    questions = list(qs.questions)
    if not questions:
        raise HTTPException(400, "这套题还没有题目")
    score, detail = grade_answers(questions, body.answers)
    att = Attempt(quiz_set_id=qid, student_id=user.id, answers=body.answers,
                  score=score, total=float(len(questions)), detail=detail,
                  duration_sec=body.duration_sec)
    db.add(att)
    bump_weak_stats(db, user.id, detail)
    db.commit()
    ordered = sorted(questions, key=lambda x: x.order_no)
    return {"attempt_id": att.id, "score": score, "total": len(questions),
            "accuracy": round(score / len(questions) * 100, 1),
            "summary": review_summary(ordered, body.answers, detail),
            "review": review_payload(ordered, body.answers, detail)}


@app.get("/api/my/attempts")
def my_attempts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Attempt).where(Attempt.student_id == user.id)
                      .order_by(Attempt.submitted_at.desc()).limit(50)).all()
    return {"attempts": [{"id": a.id, "quiz_id": a.quiz_set_id, "quiz_title": a.quiz_set.title,
                          "course": a.quiz_set.course.title, "score": a.score, "total": a.total,
                          "accuracy": round(a.score / a.total * 100, 1) if a.total else 0,
                          "submitted_at": a.submitted_at.isoformat()} for a in rows]}


@app.get("/api/attempts/{aid}")
def attempt_detail(aid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = db.get(Attempt, aid)
    if not a:
        raise HTTPException(404, "记录不存在")
    if a.student_id != user.id and user.role != "teacher" and not user.is_admin:
        raise HTTPException(403, "无权查看")
    qs = a.quiz_set
    ordered = sorted(qs.questions, key=lambda x: x.order_no)
    return {"id": a.id, "score": a.score, "total": a.total,
            "accuracy": round(a.score / a.total * 100, 1) if a.total else 0,
            "submitted_at": a.submitted_at.isoformat(), "quiz_title": qs.title,
            "quiz_id": qs.id, "course_id": qs.course_id,
            "summary": review_summary(ordered, a.answers or {}, a.detail or []),
            "review": review_payload(ordered, a.answers or {}, a.detail or [])}


# ===================================================================== AI 出题
@app.get("/api/courses/{cid}/ai-quota")
def quota(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    course, _ = require_course_access(db, cid, user)
    info = ai_quota_info(db, user)
    cfg = llm.resolve_ai_config(db, user, course)
    info.update({"key_source": cfg["source"], "provider": cfg["provider"],
                 "will_fallback_mock": not bool(cfg["api_key"])})
    return info


@app.get("/api/courses/{cid}/weak-points")
def weak_points(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """学生自己的薄弱知识点排行 —— AI 出题的输入。"""
    course, _ = require_course_access(db, cid, user)
    kp_map = {k.id: k.title for k in course.knowledge_points}
    rows = db.scalars(select(WeakStat).where(WeakStat.student_id == user.id)).all()
    out = []
    for r in rows:
        if r.kp_id not in kp_map:
            continue
        out.append({"kp_id": r.kp_id, "title": kp_map[r.kp_id], "total": r.total,
                    "wrong": r.wrong,
                    "wrong_rate": round(r.wrong / r.total * 100, 1) if r.total else 0.0})
    out.sort(key=lambda x: (-x["wrong_rate"], -x["wrong"]))
    return {"weak_points": out}


@app.post("/api/ai/generate")
def ai_generate(body: GenIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """AI 出题：额度校验 → 调 LLM → 落库成一套可作答的试题。"""
    course, role = require_course_access(db, body.course_id, user)
    if user.role not in ("student", "teacher"):
        raise HTTPException(403, "无权操作")

    kps = [k for k in course.knowledge_points if (not body.kp_ids or k.id in body.kp_ids)]
    if not kps:
        raise HTTPException(400, "该课程还没有知识点，请先让老师发布知识点")

    cfg = llm.resolve_ai_config(db, user, course)
    quota = ai_quota_info(db, user)
    if not quota["unlimited"] and quota["used"] >= quota["limit"]:
        raise HTTPException(
            429, f"今日 AI 出题额度已用完（{quota['limit']} 套/天）。"
                 f"你可以在「设置」里填入自己的 API Key 解除限制。")
    try:
        questions, used_provider = llm.generate_questions(
            provider=cfg["provider"], api_key=cfg["api_key"], base_url=cfg["base_url"],
            model=cfg["model"], kp_titles=[k.title for k in kps],
            kp_contents=[k.content or "" for k in kps], count=body.count,
            qtype=body.qtype, difficulty=body.difficulty, extra=body.extra)
    except llm.LlmError as e:
        # 失败不消耗额度
        raise HTTPException(400, f"AI 出题失败：{e}")

    kp_by_title = {k.title: k.id for k in kps}
    title = f"AI 智能练习 · {'/'.join(k.title for k in kps[:2])}" + ("…" if len(kps) > 2 else "")
    # 可见性：学生在 AI 出题页面生成的题默认只有本人可见；教师/管理员生成默认全班可见
    if body.scope == "auto":
        scope = "course" if _can_manage(role) else "private"
    else:
        scope = body.scope
    if scope != "private" and not _can_manage(role):
        raise HTTPException(403, "学生生成的试题只能自己可见")
    if scope == "course" and not _can_manage(role):
        raise HTTPException(403, "只有教师可以把试题发布给全班")
    qs = QuizSet(course_id=course.id, title=title, source="ai", scope=scope,
                 kp_ids=[k.id for k in kps], created_by=user.id)
    db.add(qs)
    db.flush()
    for i, q in enumerate(questions):
        db.add(Question(quiz_set_id=qs.id, qtype=q["qtype"], stem=q["stem"],
                        options=q["options"], answer=q["answer"], analysis=q["analysis"],
                        difficulty=q["difficulty"],
                        kp_id=kp_by_title.get(kps[i % len(kps)].title), order_no=i))

    row = get_usage(db, user.id)
    if cfg["source"] == "own":
        row.used_own_key += 1
    elif cfg["source"] == "admin":
        row.used_own_key += 1          # 管理员用平台 Key：不计数
    else:
        row.used += 1
    row.last_at = now()
    db.commit()

    return {"quiz_id": qs.id, "title": title, "count": len(questions),
            "scope": scope, "provider": used_provider, "key_source": cfg["source"],
            "quota": ai_quota_info(db, user),
            "notice": "当前为离线模拟题（未配置 API Key）" if used_provider == "mock" else None}


# ===================================================================== 教师看板
@app.get("/api/courses/{cid}/analytics")
def analytics(cid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    course, role = require_course_access(db, cid, user, manage=True)
    students = sorted(course.students, key=lambda x: x.name)
    kp_map = {k.id: k.title for k in course.knowledge_points}
    quiz_ids = [q.id for q in course.quiz_sets]
    asg_ids = [a.id for a in course.assignments]

    rows = []
    for s in students:
        attempts = db.scalars(select(Attempt).where(Attempt.student_id == s.id,
                                                    Attempt.quiz_set_id.in_(quiz_ids))).all() \
            if quiz_ids else []
        total_q = sum(a.total for a in attempts)
        got = sum(a.score for a in attempts)
        subs = db.scalars(select(Submission).where(Submission.student_id == s.id,
                                                   Submission.assignment_id.in_(asg_ids))).all() \
            if asg_ids else []
        graded = [x.score for x in subs if x.score is not None]
        rows.append({
            "student_id": s.id, "name": s.name, "student_no": s.student_no, "email": s.email,
            "quiz_attempts": len(attempts),
            "quiz_accuracy": round(got / total_q * 100, 1) if total_q else None,
            "submitted": len(subs), "graded": len(graded),
            "avg_score": round(sum(graded) / len(graded), 1) if graded else None,
        })

    # 班级薄弱知识点排行
    weak = db.execute(
        select(WeakStat.kp_id, func.sum(WeakStat.total), func.sum(WeakStat.wrong))
        .join(User, User.id == WeakStat.student_id)
        .where(enrollments.c.course_id == cid, enrollments.c.student_id == WeakStat.student_id)
        .group_by(WeakStat.kp_id)).all()
    weak_list = []
    for kp_id, tot, wr in weak:
        if kp_id in kp_map and tot:
            weak_list.append({"kp_id": kp_id, "title": kp_map[kp_id], "total": int(tot),
                              "wrong": int(wr or 0),
                              "wrong_rate": round((wr or 0) / tot * 100, 1)})
    weak_list.sort(key=lambda x: -x["wrong_rate"])

    # 全班每次答题的趋势
    trend = []
    if quiz_ids:
        trend = [{"date": a.submitted_at.date().isoformat(),
                  "accuracy": round(a.score / a.total * 100, 1) if a.total else 0}
                 for a in db.scalars(select(Attempt).where(Attempt.quiz_set_id.in_(quiz_ids))
                                     .order_by(Attempt.submitted_at)).all()]

    return {
        "course": {"id": course.id, "title": course.title, "join_code": course.join_code,
                   "student_count": len(students),
                   "kp_count": len(course.knowledge_points),
                   "assignment_count": len(course.assignments),
                   "quiz_count": len(course.quiz_sets)},
        "students": rows,
        "weak_knowledge_points": weak_list[:10],
        "trend": trend[-60:],
    }


# ===================================================================== 管理员
@app.get("/api/admin/platform-ai")
def admin_get_platform_ai(db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    """读取平台默认 AI 配置（Key 只回显后 4 位，绝不下发明文）。"""
    from db import (SK_DAILY_LIMIT, SK_PLATFORM_BASE_URL, SK_PLATFORM_ENABLED,
                    SK_PLATFORM_KEY_ENC, SK_PLATFORM_MODEL, SK_PLATFORM_PROVIDER,
                    get_setting)
    cfg = llm.platform_config(db)
    return {
        "configured": cfg["configured"],
        "enabled": cfg["enabled"],
        "provider": cfg["provider"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "key_masked": mask_key(cfg["api_key"]) if cfg["configured"] else "",
        "source": "db" if get_setting(db, SK_PLATFORM_KEY_ENC) else
                  ("env" if llm.ENV_FALLBACK_KEY else "none"),
        "daily_limit": daily_limit(db),
        "env_fallback_available": bool(llm.ENV_FALLBACK_KEY),
        "usage_today": db.scalar(select(func.sum(AiUsage.used + AiUsage.used_own_key))
                                 .where(AiUsage.day == today_str())) or 0,
    }


@app.post("/api/admin/platform-ai")
def admin_set_platform_ai(body: PlatformAiIn, db: Session = Depends(get_db),
                          admin: User = Depends(admin_only)):
    """
    管理员上传 / 更新**平台默认 API Key**（加密存库，供所有用户使用）。
      * api_key 留空 = 不改动现有 Key
      * clear_key=true = 清除平台 Key（所有用户回到离线模拟题）
    """
    from db import (SK_DAILY_LIMIT, SK_PLATFORM_BASE_URL, SK_PLATFORM_ENABLED,
                    SK_PLATFORM_KEY_ENC, SK_PLATFORM_MODEL, SK_PLATFORM_PROVIDER,
                    set_setting)

    if body.provider and body.provider not in llm.PROVIDERS:
        raise HTTPException(400, "不支持的 AI 服务商")

    if body.provider is not None:
        set_setting(db, SK_PLATFORM_PROVIDER, body.provider)
    if body.base_url is not None:
        set_setting(db, SK_PLATFORM_BASE_URL, body.base_url.strip())
    if body.model is not None:
        set_setting(db, SK_PLATFORM_MODEL, body.model.strip())
    if body.enabled is not None:
        set_setting(db, SK_PLATFORM_ENABLED, "1" if body.enabled else "0")
    if body.daily_limit is not None:
        set_setting(db, SK_DAILY_LIMIT, str(max(1, min(int(body.daily_limit), 200))))

    if body.clear_key:
        set_setting(db, SK_PLATFORM_KEY_ENC, None)
    elif body.api_key and body.api_key.strip():
        set_setting(db, SK_PLATFORM_KEY_ENC, encrypt_secret(body.api_key.strip()))

    db.commit()
    cfg = llm.platform_config(db)
    return {"ok": True, "configured": cfg["configured"], "enabled": cfg["enabled"],
            "key_masked": mask_key(cfg["api_key"]) if cfg["configured"] else "",
            "daily_limit": daily_limit(db)}


@app.post("/api/admin/platform-ai/test")
def admin_test_platform_ai(db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    """用当前平台配置真发一次最小请求，验证 Key 是否可用。"""
    cfg = llm.platform_config(db)
    if not cfg["configured"]:
        raise HTTPException(400, "还没有配置平台 API Key")
    if not cfg["enabled"]:
        raise HTTPException(400, "平台 AI 当前处于停用状态")
    try:
        raw = llm._call_openai_compatible(
            (cfg["base_url"] or llm.PROVIDERS.get(cfg["provider"], {}).get("base_url", "")),
            cfg["api_key"],
            (cfg["model"] or llm.PROVIDERS.get(cfg["provider"], {}).get("model", "")),
            "你是一个测试助手，只回复两个字。", "回复：可用", timeout=30.0)
    except llm.LlmError as e:
        raise HTTPException(400, f"测试失败：{e}")
    return {"ok": True, "provider": cfg["provider"],
            "base_url": cfg["base_url"] or llm.PROVIDERS.get(cfg["provider"], {}).get("base_url", ""),
            "model": cfg["model"] or llm.PROVIDERS.get(cfg["provider"], {}).get("model", ""),
            "reply": (raw or "").strip()[:60]}


@app.get("/api/admin/stats")
def admin_stats(db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    def count(model):
        return db.scalar(select(func.count()).select_from(model)) or 0
    return {
        "users": count(User),
        "teachers": db.scalar(select(func.count()).select_from(User).where(User.role == "teacher")) or 0,
        "students": db.scalar(select(func.count()).select_from(User).where(User.role == "student")) or 0,
        "admins": db.scalar(select(func.count()).select_from(User).where(User.is_admin.is_(True))) or 0,
        "courses": count(Course),
        "knowledge_points": count(KnowledgePoint),
        "assignments": count(Assignment),
        "submissions": count(Submission),
        "quiz_sets": count(QuizSet),
        "questions": count(Question),
        "attempts": count(Attempt),
        "ai_generations_today": db.scalar(select(func.sum(AiUsage.used + AiUsage.used_own_key))
                                          .where(AiUsage.day == today_str())) or 0,
        "platform_key_configured": llm.platform_config(db)["configured"],
        "daily_ai_limit": daily_limit(db),
    }


@app.get("/api/admin/users")
def admin_users(q: str = Query(default=""), db: Session = Depends(get_db),
                admin: User = Depends(admin_only)):
    stmt = select(User).order_by(User.id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(User.name.like(like) | User.email.like(like)
                          | User.student_no.like(like))
    users = db.scalars(stmt.limit(500)).all()
    out = []
    for u in users:
        out.append({
            "id": u.id, "name": u.name, "email": u.email, "role": u.role,
            "is_admin": u.is_admin, "is_active": u.is_active,
            "school": u.school, "student_no": u.student_no,
            "has_own_key": bool(u.ai_api_key_enc),
            "created_at": u.created_at.isoformat(),
            "course_count": (db.scalar(select(func.count()).select_from(Course)
                                       .where(Course.teacher_id == u.id)) or 0),
            "attempt_count": (db.scalar(select(func.count()).select_from(Attempt)
                                        .where(Attempt.student_id == u.id)) or 0),
        })
    return {"users": out}


@app.patch("/api/admin/users/{uid}")
def admin_patch_user(uid: int, body: AdminUserPatch, db: Session = Depends(get_db),
                     admin: User = Depends(admin_only)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    if u.id == admin.id and body.is_admin is False:
        raise HTTPException(400, "不能取消自己的管理员权限")
    if body.role is not None:
        u.role = body.role
    if body.is_admin is not None:
        u.is_admin = body.is_admin
    if body.is_active is not None:
        if u.id == admin.id and body.is_active is False:
            raise HTTPException(400, "不能停用自己的账号")
        u.is_active = body.is_active
    db.commit()
    return {"ok": True}


@app.get("/api/admin/courses")
def admin_courses(db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    courses = db.scalars(select(Course).order_by(Course.created_at.desc()).limit(500)).all()
    return {"courses": [{
        "id": c.id, "title": c.title, "teacher": c.owner.name, "teacher_id": c.teacher_id,
        "join_code": c.join_code, "is_published": c.is_published,
        "created_at": c.created_at.isoformat(),
        "student_count": len(c.students),
        "kp_count": len(c.knowledge_points),
        "assignment_count": len(c.assignments),
        "quiz_count": len(c.quiz_sets),
        "submission_count": db.scalar(
            select(func.count()).select_from(Submission)
            .join(Assignment, Assignment.id == Submission.assignment_id)
            .where(Assignment.course_id == c.id)) or 0,
    } for c in courses]}


@app.delete("/api/admin/courses/{cid}")
def admin_delete_course(cid: int, db: Session = Depends(get_db),
                        admin: User = Depends(admin_only)):
    c = db.get(Course, cid)
    if not c:
        raise HTTPException(404, "课程不存在")
    title = c.title
    db.delete(c)          # 级联删除知识点 / 作业 / 试题 / 公告
    db.commit()
    return {"ok": True, "deleted": title}


@app.delete("/api/admin/quiz-sets/{qid}")
def admin_delete_quiz(qid: int, db: Session = Depends(get_db),
                      admin: User = Depends(admin_only)):
    qs = db.get(QuizSet, qid)
    if not qs:
        raise HTTPException(404, "试题不存在")
    db.delete(qs)
    db.commit()
    return {"ok": True}


@app.delete("/api/admin/users/{uid}")
def admin_delete_user(uid: int, db: Session = Depends(get_db),
                      admin: User = Depends(admin_only)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    if u.id == admin.id:
        raise HTTPException(400, "不能删除自己的账号")
    name = u.name
    db.delete(u)          # 级联删除其课程、答题记录等
    db.commit()
    return {"ok": True, "deleted": name}


# ===================================================================== 静态页面
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.on_event("startup")
def _startup():
    init_db()
    if os.environ.get("LEARNHUB_SEED", "1") == "1":
        try:
            from seed import seed_if_empty
            seed_if_empty()
        except Exception as e:  # 种子失败不应阻塞启动
            print(f"[seed] skipped: {e}")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
