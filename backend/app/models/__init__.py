"""模型包：导入各域模型以便 Base.metadata 注册。"""
from app.models.user import User, EmailVerification  # noqa: F401
from app.models.group import Group, UserGroup  # noqa: F401
from app.models.question import QuestionBank, Question, QuestionTag  # noqa: F401
from app.models.exam import (  # noqa: F401
    PaperTemplate, ExamDefinition, ExamQuestion,
)
from app.models.record import (  # noqa: F401
    PracticeRecord, QuestionState, ExamSession, ExamResult, ShortAnswerReview,
)
from app.models.stats import StatsUserDaily, StatsGroupDaily, RefreshJob  # noqa: F401
from app.models.system import Setting, AuditLog, Draft  # noqa: F401

__all__ = [
    "User", "EmailVerification", "Group", "UserGroup",
    "QuestionBank", "Question", "QuestionTag",
    "PaperTemplate", "ExamDefinition", "ExamQuestion",
    "PracticeRecord", "QuestionState", "ExamSession", "ExamResult", "ShortAnswerReview",
    "StatsUserDaily", "StatsGroupDaily", "RefreshJob",
    "Setting", "AuditLog", "Draft",
]
