"""考试业务对外入口（门面）。

实现按职责拆分在 `app.services.exam` 包内：
- `common`    时间窗口/超时判定、可见性（含指派分组子树展开）、考试摘要
- `sessions`  可用考试列表、开考与卷面固化、断点续答
- `scoring`   逐题乐观锁落库、交卷判分、成绩读取、卡死会话回收
- `mock`      模拟考试（题库收敛 / 题型配额 / 预览 / 自助开考）
- `templates` 试卷模板
- `admin`     管理端正式考试（CRUD / 范围校验 / 归档发布 / 作废重固化）

本模块只做重导出，保持对 api 层与测试的原有导入路径不变。
"""

from __future__ import annotations

from app.services.exam.admin import (
    _EXAM_SOURCE_FIELDS,
    _check_exam_scope,
    _deleted_rows,
    _exam_admin_brief,
    _recompute_published_pass,
    _reset_exam_attempts,
    _source_fields_changed,
    _validate_exam_group_ids,
    _validate_exam_question_scope,
    archive_exam,
    create_exam,
    delete_exam,
    list_exams,
    list_results,
    publish_exam,
    unarchive_exam,
    update_exam,
)
from app.services.exam.common import (
    _count_attempts,
    _exam_brief,
    _exam_question_count,
    _expand_groups,
    _is_overtime,
    _now,
    _parse_time,
    _user_can_access_exam,
    _within_time_window,
    exam_in_scope,
)
from app.services.exam.mock import (
    MOCK_ALLOCATIONS,
    MOCK_DEFAULT_SIZE,
    MOCK_MAX_QUESTIONS,
    MOCK_SIZE_PRESETS,
    _cleanup_stale_mock_defs,
    _mock_avail_by_type,
    _mock_rules_key,
    _normalize_bank_ids,
    build_mock_spec,
    mock_banks,
    preview_mock_paper,
    resolve_mock_scope,
    start_mock_exam,
)
from app.services.exam.scoring import (
    _SCORING_TIMEOUT_SEC,
    _recover_stuck_scoring,
    get_result,
    submit_answer,
    submit_exam,
)
from app.services.exam.sessions import (
    _ensure_exam_questions,
    _persist_exam_questions,
    _session_payload,
    list_available,
    session_detail,
    start_exam,
)
from app.services.exam.templates import (
    create_template,
    delete_template,
    list_templates,
    preview_paper,
)

__all__ = [
    "_count_attempts",
    "_exam_brief",
    "_exam_question_count",
    "_expand_groups",
    "_is_overtime",
    "_now",
    "_parse_time",
    "_user_can_access_exam",
    "_within_time_window",
    "exam_in_scope",
    "_SCORING_TIMEOUT_SEC",
    "_recover_stuck_scoring",
    "get_result",
    "submit_answer",
    "submit_exam",
    "_ensure_exam_questions",
    "_persist_exam_questions",
    "_session_payload",
    "list_available",
    "session_detail",
    "start_exam",
    "MOCK_ALLOCATIONS",
    "MOCK_DEFAULT_SIZE",
    "MOCK_MAX_QUESTIONS",
    "MOCK_SIZE_PRESETS",
    "_cleanup_stale_mock_defs",
    "_mock_avail_by_type",
    "_mock_rules_key",
    "_normalize_bank_ids",
    "build_mock_spec",
    "mock_banks",
    "preview_mock_paper",
    "resolve_mock_scope",
    "start_mock_exam",
    "create_template",
    "delete_template",
    "list_templates",
    "preview_paper",
    "_EXAM_SOURCE_FIELDS",
    "_check_exam_scope",
    "_deleted_rows",
    "_exam_admin_brief",
    "_recompute_published_pass",
    "_reset_exam_attempts",
    "_source_fields_changed",
    "_validate_exam_group_ids",
    "_validate_exam_question_scope",
    "archive_exam",
    "create_exam",
    "delete_exam",
    "list_exams",
    "list_results",
    "publish_exam",
    "unarchive_exam",
    "update_exam",
]
