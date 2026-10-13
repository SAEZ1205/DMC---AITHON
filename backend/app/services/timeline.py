"""Carga en memoria todos los eventos de un curso. Los motores filtran por `now`, así se puede
recalcular el estado en cualquier instante (recálculo en línea y reproducción en evaluate.py)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from sqlmodel import Session, select

from ..models import (
    ChatMessage,
    Chapter,
    ClassSession,
    Course,
    Enrollment,
    Heartbeat,
    MicroAnswer,
    QuizAttempt,
    User,
    WatchRange,
    ZoomChat,
    ZoomInterval,
    ZoomVoice,
)


@dataclass
class SessionInfo:
    id: int
    number: int
    title: str
    starts_at: datetime
    ends_at: datetime
    key_ranges: list[tuple[float, float]] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return (self.ends_at - self.starts_at).total_seconds()

    def deadline(self, recovery_days: int) -> datetime:
        return datetime.combine(self.starts_at.date() + timedelta(days=recovery_days), time(23, 59, 59))


@dataclass
class MicroRecord:
    at: datetime
    question: str
    answered: bool
    topic: str | None
    sentiment: str | None
    summary: str | None


@dataclass
class StudentData:
    user_id: int
    name: str = ""
    email: str = ""
    grade: float | None = None
    project_status: str = "pendiente"
    intervals: dict[int, list[tuple[datetime, datetime]]] = field(default_factory=lambda: defaultdict(list))
    watch: dict[int, list[tuple[float, float, datetime]]] = field(default_factory=lambda: defaultdict(list))
    quiz: dict[int, list[tuple[int, datetime]]] = field(default_factory=lambda: defaultdict(list))
    zoom_chat: dict[int, list[datetime]] = field(default_factory=lambda: defaultdict(list))
    voice: dict[int, int] = field(default_factory=dict)
    heartbeats: list[tuple[datetime, int]] = field(default_factory=list)
    questions: list[tuple[datetime, str | None]] = field(default_factory=list)
    micro: list[MicroRecord] = field(default_factory=list)


@dataclass
class CourseData:
    course: Course
    sessions: list[SessionInfo]
    students: dict[int, StudentData]

    def dictated(self, now: datetime) -> list[SessionInfo]:
        return [s for s in self.sessions if s.ends_at <= now]


def load_course_data(db: Session, course_id: int, user_ids: list[int] | None = None) -> CourseData:
    course = db.get(Course, course_id)
    sessions_rows = db.exec(
        select(ClassSession).where(ClassSession.course_id == course_id).order_by(ClassSession.number)
    ).all()
    session_ids = [s.id for s in sessions_rows]
    key_ranges: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for ch in db.exec(select(Chapter).where(Chapter.session_id.in_(session_ids), Chapter.is_key == True)).all():  # noqa: E712
        key_ranges[ch.session_id].append((ch.start, ch.end))
    sessions = [
        SessionInfo(s.id, s.number, s.title, s.starts_at, s.ends_at, sorted(key_ranges.get(s.id, [])))
        for s in sessions_rows
    ]

    enroll_q = select(Enrollment, User).join(User, User.id == Enrollment.user_id).where(
        Enrollment.course_id == course_id, User.role == "alumno"
    )
    if user_ids is not None:
        enroll_q = enroll_q.where(Enrollment.user_id.in_(user_ids))
    students: dict[int, StudentData] = {}
    for enr, user in db.exec(enroll_q).all():
        students[user.id] = StudentData(user.id, user.name, user.email, enr.grade, enr.project_status)
    ids = list(students)
    if not ids:
        return CourseData(course, sessions, students)

    def in_scope(model):
        return model.user_id.in_(ids)

    for row in db.exec(select(ZoomInterval).where(in_scope(ZoomInterval), ZoomInterval.session_id.in_(session_ids))):
        students[row.user_id].intervals[row.session_id].append((row.join_at, row.leave_at))
    for row in db.exec(select(WatchRange).where(in_scope(WatchRange), WatchRange.session_id.in_(session_ids))):
        students[row.user_id].watch[row.session_id].append((row.start_s, row.end_s, row.at))
    for row in db.exec(select(QuizAttempt).where(in_scope(QuizAttempt), QuizAttempt.session_id.in_(session_ids))):
        students[row.user_id].quiz[row.session_id].append((row.score, row.at))
    for row in db.exec(select(ZoomChat).where(in_scope(ZoomChat), ZoomChat.session_id.in_(session_ids))):
        students[row.user_id].zoom_chat[row.session_id].append(row.at)
    for row in db.exec(select(ZoomVoice).where(in_scope(ZoomVoice), ZoomVoice.session_id.in_(session_ids))):
        students[row.user_id].voice[row.session_id] = row.turns
    for row in db.exec(select(Heartbeat).where(in_scope(Heartbeat), Heartbeat.course_id == course_id)):
        students[row.user_id].heartbeats.append((row.at, row.active_seconds))
    for row in db.exec(
        select(ChatMessage).where(in_scope(ChatMessage), ChatMessage.session_id.in_(session_ids), ChatMessage.role == "user")
    ):
        students[row.user_id].questions.append((row.at, row.kind))
    for row in db.exec(select(MicroAnswer).where(in_scope(MicroAnswer), MicroAnswer.course_id == course_id)):
        students[row.user_id].micro.append(
            MicroRecord(row.at, row.question, row.answer is not None, row.topic, row.sentiment, row.summary)
        )

    for st in students.values():
        for lst in st.watch.values():
            lst.sort(key=lambda x: x[2])
        for lst in st.quiz.values():
            lst.sort(key=lambda x: x[1])
        st.heartbeats.sort()
        st.questions.sort(key=lambda x: x[0])
        st.micro.sort(key=lambda m: m.at)
    return CourseData(course, sessions, students)
