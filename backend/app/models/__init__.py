"""模型包：导入各域模型以便 Base.metadata 注册。"""

from app.models.exam import (  # noqa: F401
    ExamDefinition,
    ExamQuestion,
    PaperTemplate,
)
from app.models.group import Group, UserGroup  # noqa: F401
from app.models.question import Question, QuestionBank, QuestionTag  # noqa: F401
from app.models.record import (  # noqa: F401
    ExamResult,
    ExamSession,
    PracticeRecord,
    QuestionState,
    ShortAnswerReview,
)
from app.models.stats import StatsUserDaily  # noqa: F401
from app.models.system import AuditLog, Draft, Setting  # noqa: F401
from app.models.user import EmailVerification, User  # noqa: F401

__all__ = [
    "User",
    "EmailVerification",
    "Group",
    "UserGroup",
    "QuestionBank",
    "Question",
    "QuestionTag",
    "PaperTemplate",
    "ExamDefinition",
    "ExamQuestion",
    "PracticeRecord",
    "QuestionState",
    "ExamSession",
    "ExamResult",
    "ShortAnswerReview",
    "StatsUserDaily",
    "Setting",
    "AuditLog",
    "Draft",
]
