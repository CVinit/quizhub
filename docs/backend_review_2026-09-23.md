# 培训考试平台 后端代码审查报告

> 审查日期：2026-09-23
> 审查范围：`backend/app/**`（70 个文件、约 10.8k 行）+ `backend/tests`（覆盖映射）
> 基线提交：`main` @ `c9b59f5`（审查前工作区干净）
> 审查方式：core/api/schema 人工逐行 + services/utils 分 8 片并行深审 + 测试覆盖映射；
> 关键结论用仓库自带 `.venv` + 隔离临时库实弹复现（脚本为临时文件，未入库）
> 整改状态：**23 项已落地**（第二节）+ 3 条产品决策已执行（第三节），全部改动均补回归测试并通过门禁

---

## 一、审查概览

- 分片代理原始结论：**59** 条（P0 12 / P1 31 / P2 16）；另有 schemas+api 补充 14 条（第六节）、测试覆盖 15 条（第五节）。
- 本轮人工复核：**25** 条（实弹复现或逐行源码确认，见各条「复核」标注）。
- 本轮已落地：**23 项修复**（第二节）+ **3 条产品决策**（第三节）+ 2 个数据迁移脚本；新增回归测试 157 例（守卫矩阵 72、导入/审计 8、认证与并发 22、统计锁行为 3、分层/上限/日志 12、复查补齐 3、本轮其余修复 37）；随排行榜下线移除 11 例。
- 待处理：**0 项**（3.1/3.2 均已处理；第五节的「仍待补」为可选后续项）。


门禁现状：`ruff check app tests scripts`、`ruff format --check`、`mypy app`、`pytest -q` 全绿；
前端 `vue-tsc --noEmit`、`eslint src`、`vitest run`（70 passed）、`vite build` 全绿。
本轮发现绝大多数是「同一条既有约定在某条路径上漏了」，而不是风格或方向性问题。
第四~六节保留分片代理的原始结论（含未逐条复核者），复核状态逐条标注。

---

## 二、已修复项（23 项，含复现证据）

> 改动集中在三类：①校验口径与消费口径不一致；②错误降级约定漏路径；③产品决策的落地与清理。
> 每项都补了回归测试。

### 1. Update schema 显式 `null` 写入 NOT NULL 列 → 500（exam / question / group）

- 改动：新增 `backend/app/schemas/_patch.py:reject_explicit_null`，三个 Update schema 复用。
- 复现（整改前）：`PUT /api/admin/exams/{id}` 传 `{"name": null}`、`{"show_score_immediately": null}`、`{"need_review": null}` → **500**；`PUT /api/admin/questions/{qid}` 传 `{"question": null}`、`{"score": null}`、`{"difficulty": null}` → **500**；`PUT /api/admin/groups/{gid}` 传 `{"name": null}`、`{"sort": null}` → **500**；对照 `{"duration_min": null}` → 422。
- 复现（整改后）：以上全部 422；可空列（exam `start_at`、question `tags`、group `parent_id`）显式 null 仍 200。
- 测试：`backend/tests/test_review_2026_09_23_fixes.py`（参数化 11 例 + 3 例对照）。

### 2. `PUT /admin/users/{id}` 传不存在的 `dept_group_id` → 外键 IntegrityError 500

- 改动：`backend/app/services/user_service.py` 写入前 `db.get(Group, dept_group_id)` 校验（与 `assign_groups` 同口径）→ 400。
- 复现（整改前）：super_admin 传 `{"dept_group_id": 999999}` → **500**（`PRAGMA foreign_keys=ON` 下 `FOREIGN KEY constraint failed`）。
- 复现（整改后）：400「分组不存在」；传合法分组 id 仍 200。
- 测试：同上文件 `test_update_user_unknown_dept_group_is_400` + 对照。

### 3. `GET /admin/question-type-stats` 超长数字参数 → `int()` ValueError 500

- 改动：`backend/app/api/questions.py` 抽 `_split_ids`（只接受 1~10 位纯数字，超长跳过），顺带消掉两处 `lambda` 赋值（E731）。
- 复现（整改前）：`bank_ids=<5000 位数字>` → **500**（CPython ≥3.11 `int()` 4300 位上限）；正常参数 200。
- 复现（整改后）：5000 位参数 200，返回六类题型统计。
- 测试：同上文件 `test_question_type_stats_ignores_overlong_digits`。

### 4. 用户导入「分组ID」填 `inf` / `1e400` → OverflowError 使整份导入 500

- 改动：`backend/app/utils/user_excel.py:_parse_group_ids` 捕获 `OverflowError`。
- 复现（整改前）：`_parse_group_ids("inf")` / `("1e400")` 抛 `OverflowError: cannot convert float infinity to integer` → 整个预览/导入 500。
- 复现（整改后）：`_parse_group_ids("inf,1e400,3") == [3]`。
- 测试：同上文件 `test_parse_group_ids_skips_non_finite`。

### 5. 整数型系统设置用 `float()` 校验、消费方 `int()` 解析 → 模拟开考 500

- 改动：`backend/app/services/system_service.py:_validate_value` 把 `default_exam_duration_min` / `max_questions_per_exam` 拆出为严格正整数校验；`upload_max_size_mb` 保持 float（消费方确实用 float）。
- 复现（整改前）：保存 `default_exam_duration_min="90.5"` 返回 200，之后 `exam/mock.py` 的 `int("90.5")` 抛 ValueError → 用户开考 500。
- 复现（整改后）：保存 "90.5"/"0" → 400；保存 "90" 正常；`upload_max_size_mb="10.5"` 仍被接受。
- 测试：同上文件 `test_integer_settings_reject_decimal` + 对照。

### 6. 邮件模板格式错误逃出 `_render` 兜底 → 整封邮件不发

- 改动：`backend/app/services/mail_service.py:_render` 捕获范围扩为 `(KeyError, IndexError, ValueError, AttributeError)`。
- 复现（整改前）：`_render("{code!x}", code="123")` → ValueError；`_render("{code.missing}", ...)` → AttributeError，均逃出兜底，被 `send_safely` 记为 ERROR，邮件不再发送（接口仍提示「已发送」）。
- 复现（整改后）：上述模板返回空串 → 调用方回退默认文案；正常模板渲染不变。
- 测试：同上文件 `test_render_falls_back_on_bad_template`（参数化）+ 正常模板对照。

### 7. `refresh_user_daily` 缺 `BEGIN IMMEDIATE` 写锁 → 统计丢失更新

- 改动：`backend/app/services/stats/aggregate.py` 抽 `_acquire_write_lock(db)`（已在事务中时 no-op），`refresh_daily` 与 `refresh_user_daily` 读快照前统一调用。
- 依据：`refresh_daily` 已有锁 + 注释论证；`refresh_user_daily` 同为「读快照 → DELETE → 重建」，且调用方 `review_service`、`exam/scoring` 都是 commit 之后调用（新事务首条 SQL 是 SELECT）。
- 测试：`backend/tests/test_stats_refresh_locking.py` 新增 `test_refresh_user_daily_takes_immediate_write_lock`（与既有 refresh_daily 用例同构）。

### 8. 复核 / 公布后的统计重算未降级 → 假 500，且公布路径会跳过通知邮件

- 改动：`backend/app/services/review_service.py` 新增 `_refresh_stats_quietly()`，`review()` 与 `publish_results()` 两处调用点改用它（失败记 WARNING + rollback，不向上抛），与 `exam/scoring`、`exam/admin`、`question_service` 的既有降级口径一致。
- 影响面（整改前）：刷新失败时接口 500 但复核已落库（重试得「该题已复核」）；公布路径更是成绩已公布却**永远跳过发信**，重试返回 `published: 0`。
- 测试：`test_review_2026_09_23_fixes.py::test_review_survives_stats_refresh_failure`、`::test_publish_results_still_queues_mail_when_refresh_fails`。

### 9. 模拟考试定义收敛：清理逻辑真正生效 + 保留数可配置（决策 2）

- 决策：保留最近 N 个，N 可在系统设置中配置。
- 改动：
  - `services/exam/mock.py`：`_cleanup_stale_mock_defs` 重写为「按 id 倒序保留最近 N 个（强制含本次复用的定义）」，清理更早的**废弃**定义（其全部会话仍是 `in_progress`），连同未完成会话与固化题目一并删除；**任何含已交卷/已判分会话的定义一律保留**（删除会经外键级联抹掉该考生的模拟成绩）。
  - 新增 `MOCK_KEEP_DEFAULT = 5`、`MOCK_KEEP_MAX = 100` 与 `_mock_keep_limit()`（脏配置回退默认）。
  - 系统设置新增 `mock_keep_definitions`（分类 exam，默认 5，正整数 1~100；`api/system.py` 的标签与字段类型同步）。
- 复现（整改前）：同一用户 8 次不同题量开考 → 8 个定义 / 8 个会话 / 68 行固化题目；原清理条件「无任何 ExamSession」永不成立，一条都没删。
- 复现（整改后）：`keep=2` + 5 次开考 → 2 个定义 / 2 个会话；已交卷的定义与其 `ExamResult` 保留。
- 测试：`backend/tests/test_mock_cleanup_and_rank_removal.py`（3 例，含「已交卷不被连带删除」）。

### 10. 排行榜功能整体下线（决策 3、4）

- 决策：取消排行功能。
- 后端改动：删除 `services/stats/rank.py`、`GET /rank` 端点（`api/panel.py` 仅保留面板与概览）、`stats_service` 的 rank 重导出（含此前已无引用的 `_TZ`/`logger` 重导出）、`rank_visible` 设置项（`DEFAULT_SETTINGS` / `BOOL_SETTING_KEYS` / `site_info` / 管理端标签与字段类型）。
- 前端改动：删除 `views/user/Rank.vue`、路由与守卫、导航入口（`UserMenu.vue`）、`stores/site.ts` 的 `rank_visible` 字段与赋值。
- 测试清理：删除 `tests/test_stats_rank.py` 与 4 个文件中的 rank 用例/断言；`test_api_user_routes.py` 改为断言 `/api/rank` → 404（防止误加回）。
- 验证：后端 `ruff`/`mypy`/`pytest` 全绿；前端 `vue-tsc --noEmit`、`eslint src`、`vitest run`（70 passed）、`vite build` 全绿。
- 遗留提示：见第三节 3.2（`stats_user_daily` 的考试类列已无读取方）。

### 11. 迁移脚本：清理 `settings` 表中的 `rank_visible` 遗留行

- 新增 `backend/scripts/migrate_2026_09_23.py`（`--dry-run` 支持、幂等、随启动脚本自动执行）：删除存量 `key='rank_visible'` 行。
- 为什么必须清：`get_settings` 是「读全表」，孤儿行会让管理端设置页出现一个既无标签、也无法保存的字段。
- 测试：`test_mock_cleanup_and_rank_removal.py::test_migration_removes_legacy_rank_setting`。

### 12. 考试数据范围口径统一：概览计数与「可见 / 可改」一致（报告 3.1）

- 改动：`app/services/exam/common.py` 新增共享判定 `exam_in_scope(group_ids, scope)`（子集口径），经 `exam_service` 门面导出；`stats/panel.admin_overview`、`exam/admin.list_exams`、`exam/admin._check_exam_scope`、`review_service.publish_results` 四处统一调用。
- 问题（整改前）：概览用「有交集」判定，而考试列表与操作前校验用「子集」判定 —— 共同指派给 A、B 两个部门的考试会被算进 A 部门管理员的「正式考试数」，但该管理员在列表里看不到它、编辑/归档/公布成绩都会 403。
- 复现（整改前）：A 部门管理员的概览 `total_exams == 2`（跨部门 + 本部门），考试列表却只有 1 场。
- 复现（整改后）：`total_exams == 1`，且与 `list_exams` 的可见集合完全一致（回归断言两处相等）。
- 测试：`backend/tests/test_review_2026_09_23_p1_fixes.py::test_exam_overview_uses_subset_scope_like_exam_list`、`::test_exam_in_scope_helper_semantics`。

### 13. 题库导入：题目分组继承题库分组（修复 NULL 分组）

- 改动：`services/import_service.do_import` 以题库的 `group_id` 作为题目分组（`question_group_id = bank.group_id`）；题库在预览与确认之间被删除时显式报 400，而不是访问属性触发 500。
- 问题（整改前）：super_admin 指定已有题库但不传 `group_id` 时题目落 `group_id = NULL`，破坏 `question_service.update_question` 维护的「题目分组 = 题库分组」不变量；部门管理员看不到这些题（列表按 `group_id IN (scope)` 过滤）也改不了（`_validate_group` 对 None 直接 403）。
- 复现（整改前）：`bank.group_id = 1`，导入 6 题后 `{q.group_id} == {None}`。
- 复现（整改后）：`{q.group_id} == {bank.group_id}`，部门管理员在题目列表里能看到它们。
- 测试：同上文件 `::test_imported_questions_inherit_bank_group`。

### 14. 用户导入：自动归属导入者的部门

- 改动：`user_service.import_users` 新增 `dept_group_id` 形参并写入新用户；`api/users.py` 导入端点按 `scope is not None` 传入调用者部门（与手动新增 `create_user` 完全同口径）。
- 问题（整改前）：手动新增会自动归属部门，导入路径不设 `dept_group_id`，而模板「分组ID」列允许留空 —— 部门管理员导入出的用户不在其数据范围内：列表看不到，审批/禁用/重置密码/分配分组全部 403，再次导入同一邮箱只会得到「该邮箱已存在」。
- 测试：同上文件 `::test_import_users_assigns_actor_department`。

### 15. 分组更新：显式 `parent_id: null` 不再绕过部门范围守卫

- 改动：`api/groups.py` 用 `model_fields_set` 区分「未传」与「显式 null」，显式 null（移到根）同样受范围约束：部门管理员 403，super_admin 放行。
- 问题（整改前）：守卫条件含 `payload.parent_id is not None`，显式传 `{"parent_id": null}` 时校验被跳过，`update_group` 把分组提升为根 —— 部门管理员可把自己子树内的分组（含 `dept_group_id` 指向的那个）摘出本部门树，与注释声明的「防扩张数据范围」意图相反。
- 测试：同上文件 `::test_dept_admin_cannot_move_group_to_root`（含「未传 parent_id 仍可改名」的对照）。

### 16. 认证与限流失败留痕（不落 PII）

- 改动：`services/auth_service.login` 的 5 个失败分支与 `change_password` 失败分支补 WARNING（只记原因 + user_id + 来源 IP，不记邮箱）；改密成功记 INFO（token_version 变更会让其它会话失效）；`core/rate_limit._deny` 补 WARNING，键经 `_mask_key()` 处理（保留 `login:account` 维度前缀，标识替换为 sha256 前 8 位）。
- 问题（整改前）：认证路径零日志 —— 撞库/账号枚举在服务端不留痕，用户「莫名被踢下线」也无从排查；限流拒绝同样只返回 429。
- 测试：同上文件 `::test_login_failure_is_logged_without_email`、`::test_rate_limit_denial_is_logged_with_masked_key`（断言日志含原因/维度，且**不含邮箱**）。

### 17. P1 加固批次：入参上限、上传读取共用实现、草稿预检与限流、utils 分层

- `core/limits.py` 新增 `MAX_ID_LIST_LEN = 200`，应用到所有 list 型入参（`RegisterIn.group_ids`、
  `ExamCreateIn` / `ExamUpdateIn`、`PaperTemplateIn`、`PaperPreviewIn`、`MockPaperIn.bank_ids`、
  `UserGroupAssign`、`UserCreateIn`、题目 `tags`）：这些列表会原样拼进 SQL 的 `IN (...)`，
  无上限时单个请求即可撞上 SQLite 绑定参数上限（`too many SQL variables` → 500），
  且 `RegisterIn.group_ids` 走公开注册接口；现在超限由 Pydantic 拦成 422。
- 新增 `core/uploads.py:read_limited()`：题库导入与用户导入原先各抄一份「Content-Length 预检 +
  1MB 分块累计」（已分叉），现共用同一实现（Content-Length 与累计读取双重校验）。
- `PUT /drafts/{form_key}`：先按 Content-Length 预检超大 body（原来只在请求体解析完成后校验体积，
  挡不住解析期内存峰值），并补用户维度限流（120 次/分钟）。
- `utils/user_excel.py` 不再 `raise fastapi.HTTPException`，统一改抛 `DomainError`
  （与 `import_service.do_import` 同口径，响应体形状不变），utils 层不再抛传输层异常。

### 18. P2 清理批次：示例行不再入库、截断/空表可见、死代码与筛选校验

- **模板示例行不再被当真题导入**：`utils/excel.py` 新增 `EXAMPLE_PREFIX = "【示例】"`，
  `build_template()` 的 6 行示例题带该前缀，`parse_workbook` 解析时跳过 —— 此前
  `parse_workbook(build_template())` 会返回 6 道示例题（「HTTP 默认端口？」等），
  用户下载模板后原地填写不删示例行，示例题就会随真实题目一起入库。
- **无匹配 Sheet / 超行数上限不再静默**：题库模板一个 Sheet 都没命中时补一条整体错误；
  用户导入预览新增 `truncated` 标记 + 一条「超过单次导入上限 N 行」的错误；缺少「用户」
  Sheet 时也给出可见错误（原先都是静默返回 0 行/0 题）。
- **题目列表筛选参数校验**：`difficulty` 加 `Query(ge=1, le=3)`，`type` 用 `QUESTION_TYPE`
  白名单校验（422），服务层 `if difficulty:` 改为 `is not None` —— 原先 `?difficulty=0`
  被当作「不过滤」返回全量、`?difficulty=99` 返回空集，而同一字段在创建/更新时是 400，
  同一语义三种表现。
- **死代码清理**：删除 `core/deps.users_in_scope`、`group_subtree_ids` 兼容别名（零引用）
  与 `stats_service` 中零引用的 `_utcnow` 重导出（`_date_str` / `_day_bounds_utc` 仍被测试使用，保留）。

### 19. 3.2 遗留处理：下线 stats_user_daily 的考试类聚合

- 证据：`exam_count` / `exam_score_sum` / `exam_pass_count` 在 `app/**` 与 `frontend/src`
  中**零读取方**（排行榜下线后仅剩写入），`answer_count` / `correct_count` / `wrong_count`
  同样无读取方；该表当前唯一消费方是管理端概览的「今日活跃」，只按 `(user_id, date)` 计数。
- 改动：
  - `models/stats.py` 移除三个考试类列；
  - `services/stats/aggregate.py` 的考试侧查询由「按用户聚合分数/次数」改为「是否存在当日
    已公布正式考试成绩」（活跃判定），口径不变（仍排除未公布与模拟考），SQL 少一次聚合与 JOIN；
  - 新增 `scripts/migrate_2026_09_23_stats_exam_columns.py`：按**当前模型 DDL** 重建
    `stats_user_daily`（rename → create → copy → drop → 重建索引），保留数据、幂等、支持 `--dry-run`。
    不迁移的话存量库会因列不存在而让概览刷新直接 `OperationalError`。
  - 受影响的 4 处测试断言改写为「行存在 = 当日活跃」的语义（更贴近真实契约）。
- 保留取舍：练习类三列当前也无读取方，但保留用于活动标记与后续报表（见模型 docstring）；
  若确认长期不用，可连同聚合逻辑一起下线。

### 20. P2 类型与上限批次：JSON 体积上限、TypedDict、列表响应模型、无用参数

- **JSON 字段体积上限**：`core/limits.py` 新增 `MAX_JSON_BYTES(64KB)` / `MAX_SETTING_VALUE_CHARS(4096)` /
  `MAX_SETTING_KEYS(50)` 与 `validate_json_size()`；应用到考试 `rules`、模板 `config`、
  题目 `options` / `left_items` / `right_items` / `answer`、系统设置 `updates`
  （此前作答路径有 16KB 上限，但这些创建/配置路径无任何限制，可写入任意大 JSON blob）。
- **题目列表声明 `response_model`**：新增 `QuestionListOut`，`GET /api/admin/questions`
  不再直接返回 ORM 实例（字段集此前随模型演进自动变化，且与同文件 create/update 的做法不一致）。
- **类型标注**：`RowError` / `UserRowError` TypedDict 统一 Excel 解析错误项结构
  （`RowError` 放在 schemas 层供 utils 复用，避免 utils→schemas→utils 成环），
  `open_workbook()` 补返回类型，`UploadPreview.errors` 由 `list[dict]` 收紧为 `list[RowError]`。
- **删除无用参数**：`user_excel.preview(db, ...)` 的 `db` 从未使用（调用方却在事务中等待它
  数分钟），签名收紧为 `preview(content, user_id)`，8 处调用点同步更新。

### 21. 分层、性能与可观测性批次

- **服务层/工具层彻底不依赖 FastAPI**（`core/errors.py` 声明的分层约定）：
  - 新增 `core/status.py`（领域层状态码常量，纯 int、不 import 框架），14 个 service/utils 模块
    由 `fastapi.status` 迁移过去；`core/background.py` 定义 `BackgroundTaskQueue` Protocol，
    `auth_service` 不再 import FastAPI 的 `BackgroundTasks`（`review_service`/`exam/admin`
    的 `bg` 参数也顺带补上类型）；
  - 新增**结构性守卫测试**：`app/services/**` 与 `app/utils/**` 出现 `import fastapi` 即失败。
- **密码 72 字节规则收敛**：`core/security.validate_password_bytes()` 成为唯一实现
  （原先在 schemas/auth、api/users、user_service、user_excel、security 共 6 处各写一份）；
  `ResetPasswordIn` / `UserCreateIn` 从路由模块移入 `schemas/user.py`。
- **角色/状态常量**：`models/user.py` 新增 `ROLE_*` / `STATUS_*`，授权分支里 41 处字面量改为常量
  （拼错的字面量会让安全守卫静默失效，拼错的常量名会直接 NameError）。
- **性能**：
  - 组卷来源的标签筛选改用 SQLite JSON1 `json_each` 在 SQL 侧做元素级匹配
    （原实现把整表 `(id,type,tags)` 拉进 Python；超管不传 bank/group 时即全表载入）；
  - 用户端可用考试列表加上限（200，候选按 4 倍取，与管理端 `list_exams` 同口径）。
- **可观测性**：5xx 补结构化日志（方法/路径/客户端 IP，不含 PII），异常仍照常上抛由
  Starlette 转 500 —— 此前只有 uvicorn 的裸堆栈。
- **正确性**：
  - 审计时间边界按「入参是否带偏移」区分语义：不带偏移 = 业务本地时间（按 `BUSINESS_TZ` 解释），
    带偏移 = 绝对时刻。修掉两个 bug：显式给出的 UTC 零点被静默扩成当天末尾；纯日期下界被当成
    UTC 零点（Asia/Shanghai 部署下丢掉本地 00:00–08:00 的记录）。
  - 简答复核不再污染 `objective_score`（该列语义是「客观题得分」，复核得分不属于客观题；
    原实现只给 score 封顶却同时改两列，超满分时必然分叉）。
  - 上传时的 `bank_name` 加 100 字符上限（与 `QuestionBankCreate.name` 同口径）。

### 22. 收尾小项：死常量、不可达分支、验证码失败计数、加密设置错误码

- 删除 `utils/excel.TYPE_TO_SHEET` 死常量（零引用，且映射本身是同名恒等）；
- `_parse_options` 的「`|` 分隔」分支原先**不可达**（判断放在「结果非空」之后）：`甲|乙`
  会被当成一个含竖线的选项，与 docstring 承诺的行为不符；现按单行 `|` 拆分；
- 验证码失败计数记在「被提交的那条」上：原先只看最新一条，用户先收到 A、又请求到 B、
  却输入 A 时，A 永远不可用，被消耗的却是 B 的 5 次尝试机会；现在按 `(email, code)`
  定位待用记录，仅在没有匹配记录时才把失败记到最新一条（限制穷举但不消费它）；
- 未配置 `TRAINING_ENC_KEY` 时保存敏感设置返回 **503 + 可操作提示**：原先 `encrypt_value`
  抛出的 RuntimeError 未被路由捕获，管理员只会看到 500 与堆栈；
- 用户导入的分组 id 预取改**分块查询**：单元格上限 1 万字符（约 2500 个 id/行）× 单次 5000 行
  理论上能构造出数十万个候选 id，一条 `IN (...)` 会撞上 SQLite 变量上限（整批 500）；
- Logo 上传的写盘/清理历史文件/更新设置移入 `run_in_threadpool`：async 路由内做同步 I/O
  会阻塞事件循环（与题库/用户导入的既有做法同口径）；
- **服务层授权自证**：`create_user` / `update_user` 新增 `actor_role` 并自证「管理员角色只允许
  超级管理员设置/创建」（`actor_role` 未传按非超管处理）—— 路由的 403 仍是第一道，服务层补上
  第二道，避免未来的脚本或新路由直接调用时绕过校验提权。

### 23. 审查清单复查：补齐 5 项此前未修改的项

对第四节全部结论（59 条分片 + schemas/api 14 条 + 测试 15 条）逐条回查源码，方法：先用
`git diff --name-only` 取出本次改动的 76 个文件，与每条结论的 `file` 比对筛出「文件从未被改动」
的候选，再逐条读源码确认。结果：**仅以下 5 项未落地**，其余均已修复或属明确保留取舍。

- `services/audit_service.py`：草稿只限单条体积、**未限每用户条数** —— form_key 由客户端指定
  （仅限长度），认证用户可不断换 key 让 `drafts` 表无界增长；现加 `MAX_DRAFTS_PER_USER = 50`
  （覆盖已有草稿不受上限影响，只有新增 key 才计数）；
- `services/audit_service.py`：actor 为 NULL 的日志标签是「系统」，而 `users` 外键是
  `ondelete=SET NULL` —— **已删除用户的历史操作会被误读为系统行为**；标签改为
  「系统/已删除用户」并注明两种来源无法区分；
- `api/exams.py`：`POST /exams/mock/start` **无路由级限流**（清理逻辑已在第 9 项修好）；
  现加 20 次/小时/用户（`MOCK_START_LIMIT` / `MOCK_START_WINDOW_SEC`，便于测试覆盖）；
- `tests/test_rate_limit.py`：两处 `time.sleep(1.1)` 造成**时间依赖**（负载高时假失败）；
  改为注入假时钟推进 `time.monotonic`；
- `tests/test_review_suggestions_batch.py`：串行用例的 docstring 声称验证并发竞争（The Liar 的
  变体）；改为如实说明「本用例为串行验证」，并指向真实并发用例
  `test_concurrent_cross_disable_of_super_admins_keeps_one_active`（双线程互禁超管）。

---

## 三、产品决策与处理结果（2026-09-23）

| # | 事项 | 决策 | 处理结果 |
| --- | --- | --- | --- |
| 1 | 简答复核是否禁止复核本人成绩 | **允许管理员兼考生**（不禁止自评） | 已在 `review_service.review()` docstring 显式声明该取舍，并写明日后改为强制双人复核的具体做法（第二节 #8 同文件） |
| 2 | 模拟考试定义无界增长 | **保留最近 N 个，N 可设置** | 已实现并补测试（第二节 #9） |
| 3 | 排行「连胜」被 range 窗口截断 | **排行榜功能取消** | 已整体下线（第二节 #10），该语义问题随之消失 |
| 4 | 排行「考试均分」跨卷面不可比 | 同上（排行取消） | 同上；`stats_user_daily` 的考试类列仍在写入但已无读取方（见 3.2） |
| 5 | `admin_overview` 考试数用「交集」口径，与全项目「子集」口径冲突 | **需要修复**（与排行无关） | ✅ 已修复：抽 `exam_in_scope` 统一四处口径（第二节 #12） |

### 3.1 关于第 5 项：需要改，且与「排行取消」无关

**状态：✅ 已修复（见第二节 #12）** —— 以下保留当时的问题分析与改法，便于回溯判断依据。

**位置**：`backend/app/services/stats/panel.py:154-160`

```python
exam_rows = db.execute(select(ExamDefinition.group_ids).where(ExamDefinition.type == "formal")).all()
total_exams = sum(1 for (gids,) in exam_rows if gids and set(gids) & scope)   # 交集
```

**规范口径**是子集，且被显式论证过两次：`exam/admin.py:79-89`（`_check_exam_scope`，注释原文「口径是**子集**而非『有交集』」）、`exam/admin.py:66-70`（`list_exams`）、`review_service.publish_results`。

**为什么与排行无关**：`admin_overview` 是 `GET /admin/panel/overview`（`api/panel.py:26-27`）的返回，与 `rank()` 之间没有任何调用关系；删除 `stats/rank.py` 只带走了「排行四维度」的计算，概览这条路径原样保留。

**为什么必须改**（即使排行已下线）：

1. **概览数字与可操作范围自相矛盾**：一场同时指派给 A、B 两个部门的考试，会被算进 A 部门管理员的「正式考试数」；但该管理员在考试列表里看不到它（`list_exams` 用子集过滤），编辑/归档/公布成绩也会 403（`_check_exam_scope`）。管理员看到「我这有 3 场考试」，点进去只有 2 场。
2. **同一规则在 4 处各写一份**：`panel.py:159`（交集）、`exam/admin.py:68`（子集）、`exam/admin.py:88`（子集）、`review_service.py`（子集）。任何一处漂移都会让「可见性」与「可操作性」分叉 —— 这正是本报告里反复出现的缺陷模式。
3. **附带性能问题**：该查询把全部 formal 考试行（含 draft/archived）无 limit 载入 Python 再过滤。

**改法（2 处小改 + 1 条回归）**：

1. 抽共享判定（建议放 `app/services/exam/common.py`，panel 与 review 都已依赖 exam 包）：

```python
def exam_in_scope(group_ids: list[int] | None, scope: set[int] | None) -> bool:
    """考试是否落在调用者数据范围内。scope 为 None（super_admin）表示全量。

    口径是**子集**而非「有交集」：只要有一个指派分组在调用者范围之外，
    就说明该考试跨出了其管辖范围（与 _check_exam_scope 一致）。
    """
    if scope is None:
        return True
    return bool(group_ids) and set(group_ids).issubset(scope)
```

2. `panel.admin_overview` 改为 `sum(1 for (gids,) in exam_rows if exam_in_scope(gids, scope))`；`exam/admin.py` 的 `list_exams` 过滤与 `_check_exam_scope`、`review_service.publish_results` 三处改为调用同一函数。
3. 回归测试：共同指派 A/B 的考试 —— A 部门管理员的概览计数**不含**它，且其考试列表也看不到它（断言两处一致）。

> 这条我建议与「部门管理员数据范围」相关的其它测试放在同一轮做，改完跑 `pytest` 即可验证无行为回退。

### 3.2 排行下线后的遗留物（✅ 已处理：见第二节 #19）

- `stats_user_daily` 的 `exam_count` / `exam_score_sum` / `exam_pass_count` 三列现在**没有任何读取方**（概览只用 `user_id` / `date` 统计「今日活跃」），但每次作答/交卷/复核仍在写入并触发聚合重算。
- `services/stats/aggregate.py` 的 `_countable_exam_conditions()`（只统计已公布正式考试）同样已无消费方，属「无读取方的口径约束」。
- 处置（2026-09-23）：已**下线考试类聚合**（三列 + 相关口径 SQL），考试只参与「当日是否活跃」
  判定；练习类三列暂时保留（无读取方，保留用于活动标记/后续报表）。实现与迁移见第二节 #19。

---

## 四、完整发现清单（分片代理原始结论 + 复核状态）

> 说明：本节为 8 个分片代理的原始输出（去重前），「复核」列标注了本轮实际验证过的条目；
> 未标注者属代理报告，采纳前建议按 `file:line` 自行确认。同源重复条目保留，便于对照。
> 其中涉及 `stats/rank.py` 的条目已随排行榜下线自然消失（代码已删除），保留原文以记录当时的判断依据。


> 本节共收录 59 条分片原始结论（另有 14 条 schemas/api 补充结论见第六节、15 条测试结论见第五节）。

### 4.1 P0 级（必修）

#### 1. update_group 盲目 setattr，显式 null 触发 NOT NULL 约束失败 → 500

- 位置：`backend/app/services/group_service.py` line 97-98
- 复核：已实测复现 ｜ ✅ 已修复（schemas/group.py 拦成 422）
- 说明：GroupUpdate 的 name/sort 类型为 str|None / int|None，客户端提交 {"name": null} 或 {"sort": null} 时 model_dump(exclude_unset=True) 会带上该键，`for k, v in data.items(): setattr(g, k, v)` 把非空列写成 NULL，commit 抛 IntegrityError: NOT NULL constraint failed: groups.name（或 groups.sort）→ 500。已用 TestClient 实测复现（PUT /api/admin/groups/{id}，super_admin 与范围内的 dept_admin 均可触发）。注意 type:null 因白名单校验返回 400，parent_id:null 是合法语义（移到根），因此不能简单跳过所有 None。
- 建议：显式处理 parent_id，其余键拒绝 null：
```python
if "parent_id" in data:
    g.parent_id = data.pop("parent_id")  # None 表示移到根，合法
for k, v in data.items():
    if v is None:
        raise DomainError(status.HTTP_400_BAD_REQUEST, f"{k} 不能为 null")
    setattr(g, k, v)
```
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 2. update_user 不校验 dept_group_id 是否存在，外键失败 → 500

- 位置：`backend/app/services/user_service.py` line 318-326
- 复核：已实测复现 ｜ ✅ 已修复（补分组存在性校验 → 400）
- 说明：users.dept_group_id 是 REFERENCES groups(id)（models/user.py:28-30，迁移 scripts/migrate_2026_09_16.py:181 同样带该外键），连接开启 PRAGMA foreign_keys=ON。super_admin 调用时 scope 为 None，跳过范围校验，直接 u.dept_group_id = dept_group_id 并 commit，若该分组不存在则抛 IntegrityError: FOREIGN KEY constraint failed → 500（非 400）。已用 TestClient 实测复现：PUT /api/admin/users/{id} body {"dept_group_id": 999999}。同文件的 assign_groups:350-353 对分组 id 做了存在性校验，此处缺失属实现不一致。
- 建议：在写入前补存在性校验（Group 已在本文件顶部导入）：
```python
elif dept_group_id is not None:
    if scope is not None and dept_group_id not in scope:
        raise DomainError(status.HTTP_403_FORBIDDEN, "无权将用户迁移到该部门")
    if not db.get(Group, dept_group_id):
        raise DomainError(status.HTTP_400_BAD_REQUEST, "分组不存在")
    u.dept_group_id = dept_group_id
```
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 3. refresh_user_daily 缺少 BEGIN IMMEDIATE 写锁，仍存在与 refresh_daily 同类的丢失更新窗口

- 位置：`backend/app/services/stats/aggregate.py` line 137-198
- 复核：源码复核（并发窗口 + 调用点） ｜ ✅ 已修复（抽 _acquire_write_lock 并调用）
- 说明：refresh_daily 的 docstring（第 41-49 行）明确说明：本函数是「读快照 → 全量 DELETE → 重建」，pysqlite 只在 DML 前 BEGIN、SELECT 走自动提交，所以必须在读快照前先取写锁，否则并发的 refresh_user_daily 提交的新行会被旧快照覆盖（丢失更新）；第 48-49 行的实现与 tests/test_stats_refresh_locking.py 的回归测试都按此约定。但同一个模式在 refresh_user_daily 里（第 144-172 行：先 SELECT 聚合，再 DELETE，再 INSERT）完全没有取锁。

该窗口在本文件之外是真实可达的：review_service.py:161 先 `db.commit()`，随后第 165 行调用 refresh_user_for_timestamps → refresh_user_daily；exam/scoring.py:253 同样是 commit 之后调用。这两条路径下本函数的第一条 SQL 是 SELECT（autocommit 读快照），此时并发的「考生本人作答」事务（practice_service.py:257-263，其 INSERT 已持有写锁）若在 SELECT 之后、DELETE 之前提交，其当日新行会被 DELETE 抹掉并用旧快照重建。结果是 stats_user_daily 长期低于 practice_records 的真实值，排行榜/「今日活跃」静默偏小，直到下次 startup_refresh/手动 refresh_daily 才自愈。

注意 practice_service 内部调用时因为前面已 flush 过 INSERT（已持有写锁），是安全的；问题恰恰出在「commit 之后再刷新」的两处调用方。
- 建议：把取锁逻辑抽成共用 helper，refresh_daily 与 refresh_user_daily 都在读快照前调用（已是 in_transaction 时保持 no-op，不破坏 practice_service「同一事务提交」的契约）：

```python
def _acquire_write_lock(db: Session) -> None:
    """读快照前取 SQLite 写锁，避免「读快照→DELETE→重建」丢失并发写入。"""
    if not db.in_transaction():
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
```

并在 refresh_user_daily 的第 144 行 `_day_bounds_utc` 之前调用 `_acquire_write_lock(db)`；同时补一条与 test_stats_refresh_locking.py 同风格的回归断言。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 4. 指定已有题库但未传 group_id 时，题目落 NULL 分组，破坏「题目分组必须与题库分组一致」不变量

- 位置：`backend/app/services/import_service.py` line 110-149
- 复核：已实测复现
- 说明：do_import 直接用 `group_id = entry.group_id`（第 110 行，即上传表单里的分组），第 148 行 `Question(group_id=group_id)` 原样落库，从不根据所选题库的 `bank.group_id` 推导或补齐。_validate_scope 第 173 行只在「两者都非空」时比较，bank.group_id=X、group_id=None 会被放行。前端 frontend/src/views/admin/Upload.vue 允许「题库来源」选已有题库而「所属分组」留空（placeholder 明写“留空则题目不归属分组”），因此超管按 UI 正常操作即可造出 `question_banks.group_id=X` 而 `questions.group_id IS NULL` 的数据。后果可确认：(1) question_service.list_questions 第 215-216 行按 `Question.group_id.in_(scope)` 过滤，X 部门的 dept_admin 完全看不到这些题，而 list_banks 第 123-128 行按 bank_id 计数，题库里却显示有 N 题；(2) 前端编辑表单固定回传 `group_id: form.group_id`（frontend/src/views/admin/Questions.vue:331,371），question_service.update_question 第 293-295 行会因 `bank.group_id is not None and bank.group_id != effective_group` 直接抛 400「题目分组必须与题库分组一致」，即这些题在“不改分组”的情况下无法保存，直到管理员手动补选分组。create_question/update_question 都强制该不变量，导入是唯一绕过点。
- 建议：在 do_import 中把题库分组作为权威来源：选定 bank_id 后，若 `bank.group_id is not None`，则用它覆盖/校验 `group_id`（不一致时抛 DomainError 400，与 question_service 同口径）；若 bank.group_id 为 None 且 group_id 为 None，按现状允许。另一种等价做法是把这段一致性校验下沉为 `_validate_scope` 的强制分支（第 167-174 行），并在创建 Question 前统一赋值：`group_id = bank.group_id if bank and bank.group_id is not None else group_id`。建议同时补一条测试：preview(bank_id=<属于分组X的题库>, group_id=None) → do_import → 断言 Question.group_id == X。

#### 5. 整数型设置用 float() 校验，消费方 int() 解析同一字符串会 ValueError 导致 500

- 位置：`backend/app/services/system_service.py` line 191-197
- 复核：源码复核 ｜ ✅ 已修复（整数键改严格 int 校验）
- 说明：`default_exam_duration_min` / `max_questions_per_exam` / `upload_max_size_mb` 这一支用 `float(value)` 做合法性判断，因此 "90.5"、"90.0"、"1e2" 都能通过并原样入库（已实测：`float('90.0')` 合法、`int('90.0')` 抛 ValueError）。但消费方 backend/app/services/exam/mock.py:323 是 `duration_min=int(settings.get("default_exam_duration_min", "90"))`，对入库的 "90.5"/"90.0" 会抛未捕获的 ValueError。管理端设置页对 value_type=='number' 的项用 el-input-number 且未设 precision（frontend/src/views/admin/Settings.vue:51-56），用户输入 90.5 后以 `String($event)` 提交，于是任何用户「开始模拟考试」都会 500。同类不一致还有下界缺失：`upload_max_size_mb=0` 会让 api/questions.py 的 max_bytes=0，所有题库上传永久 413（管理员自锁，虽可通过同一页面改回）。
- 建议：把这三项按整数语义校验：`if not value.strip().lstrip('+').isdigit(): raise ValueError(f"{key} 必须是整数")`，并补下界（如 duration ≥ 1、upload_max_size_mb ≥ 1、max_questions_per_exam ≥ 1）。若确需兼容历史浮点脏数据，则在写入时归一为整数串（`str(int(number))`），保证消费方 `int()` 恒成立；同时给 mock.py:323 加兜底解析避免脏数据再次引发 500。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 6. _render 只捕获 KeyError/IndexError，模板格式错误会逃出兜底并静默丢失整封邮件

- 位置：`backend/app/services/mail_service.py` line 86-93
- 复核：已实测复现（异常类型） ｜ ✅ 已修复（扩围 ValueError/AttributeError）
- 说明：`_render` 的设计意图（注释第 90-91 行）是「模板占位符写错时退回通用文案」，但它只捕获 KeyError/IndexError。模板里出现未配对的 `{`、或使用了非法格式说明符时，`str.format` 抛的是 ValueError/TypeError，不在捕获范围内：实测 `'您的验证码：{code'.format(code='123456')` → ValueError，`'{score:d}'.format(score='abc')` → ValueError。system_service._validate_value 对 mail_tpl_* 三项完全没有校验（只在 CRLF 分支里管 smtp_* 与 site_name），所以管理员保存一个带笔误的模板后：send_register_code 第 98 行 `_render` 直接抛错，兜底文案不会生效，注册验证码邮件永远发不出去——而 auth_service.send_code 已经 commit 了验证码并返回成功，用户看到「已发送」，正是模块 docstring 第 3-6 行声明要消除的静默失败。放大效应：send_exam_publish_many（第 122-128 行）与 send_review_done_many（第 149-155 行）只 `except MailError`，一个收件人抛出的非 MailError 会直接中断整批循环，剩余考生/收件人全部收不到通知，且只留下 send_safely 的一行 error 日志。
- 建议：1) `_render` 扩大捕获：`except (KeyError, IndexError, ValueError, TypeError)`，保证任何模板错误都走兜底文案而不是丢信。2) 批量发送改为逐收件人兜底：`except Exception`（保留 MailError 计数语义）或把 `_render` 的异常统一包装成 MailError，避免一人失败中断整批。3) 在 system_service._validate_value 中为 mail_tpl_* 增加写入期校验：用允许的占位符集合试渲染（如 `tpl.format(**{k: '' for k in ALLOWED})`），非法模板直接 400，把问题挡在保存阶段。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 7. 工具/服务层直接 raise fastapi.HTTPException，违背全仓 DomainError 分层约定

- 位置：`backend/app/utils/user_excel.py` line 252-278
- 复核：代理报告
- 说明：`peek_preview` 与 `consume_preview` 在函数内 `from fastapi import HTTPException` 并直接抛 HTTPException。本模块不是路由：它接收 `Session`、实现角色/状态/口令策略等业务规则，是 `app/services/import_service.py` 的同构实现，而后者已统一改抛 `app.core.errors.DomainError`（`app/services/` 下已无任何 HTTPException 引用，本次全仓 grep 确认）。后果：传输层与业务逻辑耦合，测试必须导入 Web 框架；两套错误口径并存（`DomainError` 由 `app/main.py:57` 的处理器统一映射，HTTPException 走 FastAPI 内建），后续若统一在服务层包装错误会漏掉这两条路径。
- 建议：改为 `from app.core.errors import DomainError`，`raise DomainError(http_status.HTTP_400_BAD_REQUEST, "预览已过期，请重新上传")` / `DomainError(403, "无权导入他人预览数据")`。状态码与 detail 保持不变，路由 `app/api/users.py:246,251` 无需改动（DomainError 不是 ValueError，不会被该路由的 `except ValueError` 误捕，会由全局处理器返回同样的 `{"detail": ...}`）。

#### 8. _parse_group_ids 未捕获 OverflowError：分组ID 填 inf/1e400 导致整份导入 500

- 位置：`backend/app/utils/user_excel.py` line 117-122
- 复核：已实测复现 ｜ ✅ 已修复（捕获 OverflowError）
- 说明：`int(float(p))` 对 `float("inf")`（含 "inf"、"Infinity"、"1e400" 等 Excel 里作为文本的写法）抛的是 OverflowError，而 `except (ValueError, TypeError)` 捕获不到。已实跑验证：`_parse_group_ids('inf')` → `OverflowError: cannot convert float infinity to integer`；构造含 "inf" 分组ID 单元格的 xlsx 调 `user_excel.preview(None, content, 1)` 同样抛 OverflowError。路由 `app/api/users.py:233-235` 只把 ValueError 转 400，因此该异常冒泡成 500（"NaN" 恰好被 ValueError 兜住，只有 inf 系列漏网）。同一类问题在 `app/utils/excel.py:428-433` 的 `_to_int` 中已被显式修复并写了注释，此处是漏改的孪生路径。
- 建议：与 `_to_int` 同口径：`except (ValueError, TypeError, OverflowError): continue`；更稳妥的是先判有限性，如 `num = float(p)` / `if not math.isfinite(num): continue` / `gid = int(num)`。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 9. ExamUpdateIn 只对 3 个 NOT NULL 字段做了显式 null 拦截，name / show_score_immediately / show_analysis / need_review 传 null 直接 500

- 位置：`backend/app/schemas/exam.py` line 90-105
- 复核：已实测复现 ｜ ✅ 已修复（4 个 NOT NULL 字段纳入拦截）
- 说明：第 107-113 行的 `_reject_explicit_null` 只覆盖 duration_min / pass_score / max_attempts，注释也写明理由是「这些字段对应 NOT NULL 列，拒绝显式 null，避免落库时 500」。但同一 schema 里的 name（第 90 行）、show_score_immediately / show_analysis / need_review（第 98-100 行）在 exam_definitions 中同样是 NOT NULL 列（models/exam.py:44,50-52），且 `exam/admin.py:401-417` 的 `setattr` 循环会把显式 null 原样写入。实测（FastAPI TestClient，临时探针已删除）：`PUT /api/admin/exams/{id}` 传 `{"name": null}`、`{"show_score_immediately": null}`、`{"show_analysis": null}`、`{"need_review": null}` 均返回 500 Internal Server Error；同一探针中 `{"manual_questions": null}` / `{"group_ids": null}` / `{"start_at": null}` 返回 200（可空列，符合预期）。即拦截规则与列可空性不一致，漏了 4 个字段。
- 建议：把 NOT NULL 列的字段名补进同一个校验器（PATCH 语义不受影响：不提交字段时校验器不会运行）：
```python
@field_validator(
    "duration_min", "pass_score", "max_attempts",
    "name", "show_score_immediately", "show_analysis", "need_review",
    mode="before",
)
@classmethod
def _reject_explicit_null(cls, v: Any, info: ValidationInfo) -> Any:
    ...
```
或改为 model_validator 遍历「显式 set 且值为 None」的字段做白名单判断，避免以后再漏。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 10. QuestionUpdate 显式 null 会写入 NOT NULL 列（question/analysis/difficulty/score），PUT /admin/questions/{id} 返回 500

- 位置：`backend/app/schemas/question.py` line 77-85
- 复核：已实测复现 ｜ ✅ 已修复（4 个 NOT NULL 字段纳入拦截）
- 说明：QuestionUpdate 把 question / analysis / difficulty / score 声明为可空（第 77、82、83、85 行），而 questions 表这四列均为 NOT NULL（models/question.py:45,51-53 及 score 列）。question_service.update_question 走白名单 setattr（question_service.py:303-306），对显式 null 不做任何拦截。实测（临时探针，已删除）：`PUT /api/admin/questions/{qid}` 分别传 `{"question": null}` / `{"analysis": null}` / `{"difficulty": null}` / `{"score": null}` 全部返回 500（IntegrityError: NOT NULL constraint failed）。注意 answer 是安全的（`_validate_answer_shape` 对 6 种题型都拒绝 None），type 也是安全的（`None not in QUESTION_TYPES` → 400），说明只有这 4 个字段漏了。
- 建议：与 schemas/exam.py 同口径，为这 4 个字段加 `mode="before"` 的显式 null 拒绝校验器（复用同一份实现，例如放到 app/schemas/_patch.py 或 core/validation.py），把 500 变成 422：
```python
@field_validator("question", "analysis", "difficulty", "score", mode="before")
@classmethod
def _reject_explicit_null(cls, v: Any, info: ValidationInfo) -> Any:
    if v is None:
        raise ValueError(f"{info.field_name} 不能为 null；如需保持不变请不要提交该字段")
    return v
```
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 11. GroupUpdate 显式 null 会写入 NOT NULL 列（name/sort），PUT /admin/groups/{id} 返回 500

- 位置：`backend/app/schemas/group.py` line 22-25
- 复核：已实测复现 ｜ ✅ 已修复（同上，同源问题）
- 说明：GroupUpdate 的 name（第 22 行）与 sort（第 25 行）允许显式 null；groups.name / groups.sort 都是 NOT NULL（models/group.py:19,24）。group_service.update_group 直接 `for k, v in data.items(): setattr(g, k, v)`（group_service.py:97-98），没有像 QuestionUpdate 那样的白名单/空值防线。实测（临时探针，已删除）：`PUT /api/admin/groups/{gid}` 传 `{"name": null}` 与 `{"sort": null}` 均 500（IntegrityError: NOT NULL constraint failed: groups.name / groups.sort）。同类问题在本仓库已有一处修好（exam 的 3 个字段），group 这一层被漏掉。
- 建议：为 name / sort 增加显式 null 拒绝校验（或让 update_group 只对 `v is not None` 的键做 setattr）。推荐前者，与 exam 的既有做法保持一致，语义仍是 PATCH：
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 12. PUT /admin/users/{id} 未校验 dept_group_id 是否存在，超管传不存在的分组 id 触发外键 IntegrityError → 500

- 位置：`backend/app/api/users.py` line 131-141
- 复核：已实测复现 ｜ ✅ 已修复（同上，同源问题）
- 说明：路由把客户端提交的 `payload.dept_group_id` 原样传给 user_service.update_user；服务端只在 `scope is not None`（部门管理员）时校验范围（user_service.py:321-326），super_admin 的 scope 为 None，因此不存在的分组 id 会被直接 `u.dept_group_id = 999999` 写入，而 users.dept_group_id 有指向 groups.id 的外键且运行期 `PRAGMA foreign_keys=ON`（database.py:23）。实测（TestClient，已删除探针）：super_admin 调 `PUT /api/admin/users/{id}` body `{"dept_group_id": 999999}` → IntegrityError: FOREIGN KEY constraint failed → 500（不是 400/404）。对比：题目/题库路径有 `_validate_group`/`_get_allowed_bank` 做存在性校验，`assign_groups` 也校验分组存在（user_service.py:349-353），只有这里漏了。
- 建议：在路由或 update_user 内先校验分组存在：
```python
if dept_group_id is not None and not db.get(Group, dept_group_id):
    raise DomainError(status.HTTP_400_BAD_REQUEST, "分组不存在")
```
建议放在 user_service.update_user（与 assign_groups 同层），这样脚本/其它调用方也受保护。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

---

### 4.2 P1 级（建议）

#### 1. import_users 分组 id 预取无上限，绑定参数爆炸导致整批导入 500

- 位置：`backend/app/services/user_service.py` line 479-493
- 复核：代理报告
- 说明：candidate_gids 由所有行的 group_ids 去重得到且无数量上限；单元格仅受 MAX_CELL_CHARS=10000 限制（约 2500 个 id/行，utils/excel.py:56），导入行上限 5000，因此构造 100 余行即可让 Group.id.in_(candidate_gids) 超过 SQLite 变量上限。实测（本机 SQLite 3.39.3）：100000 个参数可执行，300000 个报 OperationalError: too many SQL variables；服务层实测 120 行 × 2500 个伪造分组 id → OperationalError，整个导入 500 且一行都不落库，同时进程内先构造了 30 万个 int 的 set（内存放大）。
- 建议：在解析/校验阶段限制规模并分批预取：限制每行分组数与单次导入的分组 id 总数（超限按行报错），或把 `Group.id.in_(candidate_gids)` 改为每 500 个一批查询后合并结果。

#### 2. 导入用户不写 dept_group_id，部门管理员导入后无法管理自己创建的用户

- 位置：`backend/app/services/user_service.py` line 534-547
- 复核：代理报告
- 说明：create_user 路径由路由传 dept_group_id=user.dept_group_id（api/users.py:106），而 import_users 构造 User(...) 时完全不设该字段。实测：dept_admin（dept_group_id=dept）导入一个未填分组的用户后，user_service.list_users(db, scope={dept.id}) 只返回自己，导入的用户不在范围内，后续 approve/禁用/重置密码/分配分组全部 403。用户只能由 super_admin 管理，与「部门管理员批量建号」的预期不符。
- 建议：给 import_users 增加 dept_group_id: int | None = None 形参，写入时带上；路由在 scope is not None 时传 user.dept_group_id，与 create_user 同口径。

#### 3. create_user 邮箱唯一性 check-then-insert 未兜底唯一约束冲突

- 位置：`backend/app/services/user_service.py` line 411-432
- 复核：代理报告
- 说明：先 SELECT 判存在再 INSERT commit，users.email 有唯一索引但 commit 未捕获 IntegrityError：并发同邮箱建号时后到者 500 而非 400。同类问题在 auth_service.register:156-174 更严重——验证码已在 _consume_code 中 commit 消费，冲突后用户必须重新获取验证码才能重试。import_users:557-574 已用 SAVEPOINT + IntegrityError 处理，说明这是遗漏而非设计取舍。
- 建议：```python
try:
    db.commit()
except IntegrityError as exc:
    db.rollback()
    raise DomainError(status.HTTP_400_BAD_REQUEST, "该邮箱已存在") from exc
```
register 侧同样包一层（并把「验证码已消费」的影响写入提示）。

#### 4. 登录失败与自助改密无任何服务端记录

- 位置：`backend/app/services/auth_service.py` line 307-333
- 复核：代理报告
- 说明：login 的密码错误、账号 disabled、pending、未验证邮箱分支都不写日志；change_password 提升了 token_version（使其它会话失效）也不写审计。管理侧动作（user_service）普遍有 audit_log，认证侧则完全没有痕迹：暴力破解尝试无法发现，用户投诉「莫名被踢下线」也无从排查，与可观测性要求（关键决策点/错误路径记录操作名与结果）不符。
- 建议：login 失败分支记 WARNING（只记 user_id/原因，不记邮箱以符合 PII 约定），例如 `logger.warning("[auth] 登录失败 user=%s reason=%s", user.id, "bad_password")`；change_password 成功后 `audit_log(db, user.id, "user.change_password", "user", user.id)`。

#### 5. update_user/create_user 的角色权限依赖路由校验，服务层不自证

- 位置：`backend/app/services/user_service.py` line 270-331
- 复核：代理报告
- 说明：update_user 的注释明确写「角色变更需超级管理员权限（由调用方在路由层校验）」，服务内不做角色判定；create_user 同样把「仅超管可建管理员账号」留给 api/users.py:88。同文件的 delete_user(actor_role)、import_users(actor_role) 是在服务内自证的，签名与防线不一致。当前仅路由调用（已 grep 确认），不构成可利用漏洞，但服务层可被未来脚本/新路由误用而提权。
- 建议：create_user/update_user 增加 actor_role: str 参数，服务内判定「非 super_admin 不得设置/变更 admin 角色」，路由传 user.role；与 delete_user/import_users 保持同一约定。

#### 6. _strip_group_from_assignments 在删除事务内全表加载两张表

- 位置：`backend/app/services/group_service.py` line 133-146
- 复核：代理报告
- 说明：对 ExamDefinition 与 PaperTemplate 无过滤全量物化为 ORM 对象（仅 group_ids IS NOT NULL），在持有 SQLite 写锁的 delete_group 事务内做 Python 侧逐行比较与 JSON 重写。考试/模板数量增长后，删除分组会长时间占锁，阻塞其它写请求。
- 建议：用 SQLite json_each 在 SQL 侧判断包含关系（或用 LIKE '%"<id>"%' 预筛再精判），或分批迭代（yield_per）降低内存峰值与持锁时间。

#### 7. user_service.py 体积与职责超标（501 行有效代码 / 14 个函数）

- 位置：`backend/app/services/user_service.py` line 1-590
- 复核：代理报告
- 说明：有效代码 501 行已达 500 行红线，且混合五类职责：列表查询、权限校验、单条 CRUD、级联删除、批量导入。其中 import_users(449-590，141 行) 独自承担解析校验、预取、SAVEPOINT 写入、错误汇总与审计，属于需要用「和」描述的职责聚合。
- 建议：抽出 user_import_service.py 承载导入相关逻辑（含预取与分批辅助），user_service 保留 CRUD 与权限校验；权限校验函数（_check_scope/_check_manage_permission）可归入独立的 user_permissions 模块供其它服务复用。

#### 8. 提交后的派生统计刷新未按项目既有约定降级，publish_results 失败会永久丢失公布通知邮件

- 位置：`backend/app/services/review_service.py` line 162-165, 267-295
- 复核：源码复核 ｜ ✅ 已修复（_refresh_stats_quietly 降级）
- 说明：项目对「已提交业务结果 + 派生统计刷新」已有明确约定并写进注释：exam/scoring.py:249-257、exam/admin.py:432-438、question_service.py:344-358 都把它包在 try/except 里降级为 WARNING，理由（注释原文）是「派生统计失败不应把已提交的结果变成 500，否则管理员会误以为操作失败而重试」。review_service 两处都没有遵守：

1) review() 第 161 行 `db.commit()` 之后第 165 行直接调用 stats_service.refresh_user_for_timestamps。刷新一旦抛错（如 SQLite 忙/写锁冲突），接口返回 500，但复核与改分已经落库；管理员重试会得到 400「该题已复核」（第 145-146 行），无法判断实际状态。
2) publish_results() 第 265 行 `db.commit()` 后，第 269-271 行刷新统计，第 273-295 行才挂邮件后台任务。刷新抛错时既返回 500（公布其实已成功），又永远跳过 `bg.add_task(...)`——管理员重试时 rows 里已无 published=False 的行，直接 `return {"published": 0}`，这些考生再也收不到「成绩已公布」邮件。
- 建议：与既有三处同口径降级，并把通知任务的挂载与刷新解耦：

```python
try:
    stats_service.refresh_for_timestamps(db, [r[3] for r in claimed])
except Exception:  # noqa: BLE001  派生统计失败不阻断已提交的公布
    db.rollback()
    logger.warning("[review] 公布成绩后统计重算失败，已忽略；下次刷新会兜底重算")
```

review() 第 165 行同样处理（并把 logger 引入本模块，当前模块无任何日志）。publish_results 建议先挂邮件任务再刷新，或把刷新放进 try 内。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 9. admin_overview 的正式考试数用「有交集」口径，与全项目规范的「子集」口径冲突

- 位置：`backend/app/services/stats/panel.py` line 154-160
- 复核：源码复核
- 说明：本文件用交集计数：`total_exams = sum(1 for (gids,) in exam_rows if gids and set(gids) & scope)`。但考试数据范围的规范口径是子集，且被显式论证过两次：exam/admin.py:79-89（_check_exam_scope，注释「口径是**子集**而非『有交集』：只要有一个指派分组在调用者范围之外，就说明该考试跨出了其管辖范围」）与 exam/admin.py:52-53/66-70（list_exams 同口径）。review_service.py:192-195 也沿用子集。

后果：共同指派给 A、B 两个部门的考试会被算进 A 部门管理员的概览「正式考试数」，但该管理员在考试列表里看不到它、编辑会 403、publish_results 也会 403——概览数字与其可操作范围自相矛盾。附带问题：第 159 行把全部 formal 考试行（含 draft/archived）无 limit 载入 Python 再过滤，且同一子集规则在本仓库已重复三处（exam/admin.py:68、exam/admin.py:88、review_service.py:193）。
- 建议：提取共享判定（例如放到 app/services/exam/common.py 或 app/core/deps.py：`def exam_in_scope(e, scope) -> bool: return scope is None or bool(e.group_ids) and set(e.group_ids).issubset(scope)`），panel/exam.admin/review_service 三处统一调用；admin_overview 改为复用该函数计数，并只 SELECT 需要的列（现有 `select(ExamDefinition.group_ids)` 已是列级查询，可保留）。

#### 10. 连胜维度被 range 窗口截断：range=7d/30d 时连胜最多显示 7/30，且注释给出的理由不成立

- 位置：`backend/app/services/stats/rank.py` line 57-63, 246-271
- 复核：源码复核（相关代码已随排行下线删除）
- 说明：`since` 由 range 决定（7d→今天-6，30d→今天-29，all→_MIN_DATE），第 91 行把它透传给 _streaks，_streaks 第 265 行用 `StatsUserDaily.date >= since` 限定回看范围，_streak_from_dates 只能数到列表末端，因此结果被硬性截断在窗口长度内：连续作答 100 天的用户在「近 7 天」视图里显示 7，在「全部」视图里显示 100。_streaks 的 docstring（第 249-250 行）给出的理由是「连胜不可能早于查询窗口起点」——该论断不成立：连胜当然可以早于窗口起点，只是被截断了。

实际影响：前端 Rank.vue 的「连续天数」与「近 7 天」是两个正交控件，组合后满周用户全部并列 7，榜单失去区分度，且同一用户在不同 range 下得到互相矛盾的连胜值。
- 建议：先确认产品语义：若「连续天数」应表示当前真实连胜，则把回看范围与 range 解耦（例如固定回看上限 N 天，或对 streak 维度始终按足够长的窗口取数），并把窗口内被截断的情况在返回值上标注；若确实想表达「窗口内连续天数」，请修正 _streaks 的 docstring 理由并在前端标签上写明「近 7 天内连续天数」，避免注释继续误导后续维护者。

#### 11. 排行聚合把窗口内全部用户载入内存后在 Python 排序取 Top10，未做 SQL 侧收敛

- 位置：`backend/app/services/stats/rank.py` line 65-91
- 复核：代理报告
- 说明：第 65-84 行的聚合查询按 user_id 分组后全部 `.all()` 取回，第 93-128 行在 Python 里逐行算 value 再 sort 取前 10。range=all 时该结果集等于「历史上产生过任何聚合行的全部用户」（stats_user_daily 全表按用户去重），单进程部署下这是每次 /rank 请求的 O(用户数) 内存与 CPU 峰值。文件内已对 streak 候选集（第 87-90 行注释）与展示名 IN 列表（第 129 行）做了收敛，唯独主结果集没有。
- 建议：把每个维度的取值表达为 SQL 聚合表达式后 ORDER BY + LIMIT 10，例如 count 维度：`ORDER BY func.sum(StatsUserDaily.answer_count).desc(), StatsUserDaily.user_id.asc() LIMIT 10`；accuracy 用 `func.sum(ok)*100.0/func.sum(cnt)`（cnt=0 用 CASE 归零）；score 用 `func.sum(exam_score_sum)/func.sum(exam_count)`；streak 保持现有候选集 + Python 计算后再与其余维度统一收敛。并列名次的确定性可用 `user_id ASC` 作为第二排序键保持与当前稳定排序一致。

#### 12. 考试均分榜用原始分平均，不同卷面总分之间不可比（与 panel 的百分制归一化口径不一致）

- 位置：`backend/app/services/stats/rank.py` line 106
- 复核：源码复核（相关代码已随排行下线删除）
- 说明：`value = round(score / exam_cnt, 1)`，其中 score = Σ exam_score_sum（原始分累加）、exam_cnt = Σ exam_count；聚合口径只纳入 formal 考试（aggregate.py:26-31）。但正式考试的卷面总分由题目分值/题量决定（Question.score 可配、DEFAULT_QUESTION_SCORE=2，见 models/question.py:19/61 与 ExamQuestion.score），因此同一榜单会混入满分 40 分与满分 100 分的考试：38/40（95%）会被排到 60/100（60%）之后。panel.py:70-82 对模拟考平均分专门做了 `score*100.0/total_score` 的百分制归一化并注明「各场满分不同」，两处口径不一致。前端 Rank.vue 只显示裸数字（无单位），用户无法察觉量纲问题。
- 建议：若确认「考试均分」应为百分制平均分，需要在聚合层补充分母：给 stats_user_daily 增加 `exam_total_sum`（Float）列并在 refresh_daily/refresh_user_daily 里一并 `func.sum(ExamResult.total_score)`，rank 的 score 维度改为 `round(score*100.0/total_sum, 1)`；若不改口径，请在 docstring 与前端标签中明确「原始分平均，跨卷不可比」。

#### 13. 把简答得分累加进 objective_score，破坏「客观题得分」语义，且封顶逻辑使其与 score 仍会分叉

- 位置：`backend/app/services/review_service.py` line 148-160
- 复核：代理报告
- 说明：第 153-160 行把复核增量同时加到 score 与 objective_score。但 objective_score 在 scoring.py:183/204/207/221 的定义是「客观题得分（简答部分待复核后加）」：只累加客观题 `eq.score`，且初始 `score = objective_score`。把简答 delta 加进去后，该列不再等于客观题得分，任何按它统计「客观题正确率/客观题得分」的报告都会被简答分污染。注释（第 150-152 行）声称这样能保持与 score 一致，但实现只对 score 做了 `func.min(total_score, ...)` 封顶、objective_score 无封顶，一旦累计超满分（注释自己举的 108/100 例子），两者必然分叉，声称的一致性并未达成。全仓 grep 确认该列当前无任何读取方，属于潜在口径污染。
- 建议：二选一并写清语义：1) 保持列名语义——复核只更新 score，不更新 objective_score；2) 若确实需要「复核后总分」快照，改为显式列（如 reviewed_total_score）或把 objective_score 重命名为与含义一致的字段，并与 score 使用同一个封顶表达式 `func.min(ExamResult.total_score, ExamResult.objective_score + delta)`，避免两者分叉。

#### 14. 缺少「不得复核本人成绩」约束，管理员可给自己的简答复核判满分

- 位置：`backend/app/services/review_service.py` line 81-102
- 复核：源码复核 ｜ 🟡 产品决策：允许管理员兼考生，已在 docstring 声明取舍
- 说明：review() 只做了 verdict 校验、记录存在性校验与（部门管理员的）user_in_scope 归属校验。当复核人本人就是该条记录的考生时，scope 校验自然通过（自己的 dept_group_id 必在自身子树内），于是 dept_admin/super_admin 可以对自己的简答题提交 verdict=pass，第 118-119 行直接按卷面分值给自己加分；publish_results 同理可公布自己的成绩（只要考试分组在其范围内）。list_pending 也会把本人待复核项列给本人。项目其余处对归属与范围校验相当严谨，此处缺少的自我审批（segregation of duties）约束更像是遗漏。
- 建议：在 scope 校验之后加一条内控校验并记录日志：

```python
if r.user_id == reviewer.id:
    raise DomainError(status.HTTP_403_FORBIDDEN, "不得复核本人成绩")
```

若产品确实允许管理员兼考生，请在 docstring 中显式声明该取舍（当前无任何说明）。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 15. _preview_cache 只按条目数限界，「内存有界」的注释与实际上界差几个数量级

- 位置：`backend/app/services/import_service.py` line 39
- 复核：代理报告
- 说明：第 36-38 行注释声称「行数上限 × 容量上限共同给出内存上界」，但真实上界并不安全：单条 _PreviewEntry 最多含 PARSE_ROW_MAX=10000 行（app/utils/excel.py:52），每行的题干/选项/解析等单元格各自允许 MAX_CELL_CHARS=10000 字符（同文件 MAX_CELL_CHARS），最坏单条约数百 MB（Python str/list 开销另计）；maxsize=32 即 GB 级。且缓存不做字节计量，容量淘汰只看条目数。上传侧限流是「单管理员 20 次/小时」（backend/app/api/questions.py:171），30 分钟 TTL 内一名管理员即可把 32 个槽位填满超大条目，构成 OOM/内存压力面。
- 建议：给 BoundedTTLCache 增加按成本（字节或行数）的上限并在 put 时累计/淘汰，或在 preview 中按「行数 × 单元格上限」估算体积后拒绝超限条目；退一步也可把 maxsize 降到个位数并把 PARSE_ROW_MAX/单格字符上限与该上限的乘积写进注释，使注释里的「上界」与实际相符。

#### 16. 标签集合无任何上限：大 IN 绑定参数可超 SQLite 上限、并逐条 SAVEPOINT 插入

- 位置：`backend/app/services/import_service.py` line 125-129, 191-201
- 复核：代理报告
- 说明：all_tags 由最多 10000 行 × 每格 ≤10000 字符的标签单元格聚合而来（_parse_tags 不做条数/总长度限制），数量级可达数万。第 191 行把它整体塞进 `QuestionTag.name.in_(tags)`：SQLite 的变量上限在 3.32 之前是 999、之后是 32766，超过即抛 OperationalError（不是 ValueError/DomainError），导入直接 500；本仓库其它位置（如 core/deps.user_ids_subquery 的注释）已明确把「避免大 IN 绑定参数」作为约定，此处是漏网点。随后的循环对每个新标签各做一次 `begin_nested()` + INSERT，数万标签即数万条语句，也让写事务持锁时间显著拉长（SQLite 单写者，其它写请求可能撞 database is locked）。
- 建议：1) 在 preview/do_import 中对 distinct 标签数量（或标签总字节）设上限，超限报 400 明确文案。2) 把存在性检查改为分块查询（如每 500 个一批）或用 `INSERT OR IGNORE`/`on_conflict_do_nothing` 批量插入替代「逐条 SAVEPOINT」，与 core/limits 的「单点上限」思路一致。

#### 17. 草稿只限单条体积，未限每用户条数：认证用户可无界增长 drafts 表

- 位置：`backend/app/services/audit_service.py` line 129-144
- 复核：代理报告
- 说明：api/audit.py 为草稿设了 _MAX_DRAFT_KEY_LEN=64 与 _MAX_DRAFT_BYTES=64KB，但 form_key 取值无白名单、该路由也没有任何限流（同项目 smtp-test、upload-preview 都加了 core.rate_limit.check），而 save_draft 的 upsert 主键是 (user_id, form_key)。默认 register_open=true，任意注册用户可对 k1..kN 反复 PUT，每行最多 64KB，SQLite 文件持续膨胀且不会自动收缩——单条有界、总量无界。
- 建议：在 save_draft（或路由）增加每用户草稿条数上限（例如 ≤20，超出时删除最旧或拒绝），并给写草稿路由加 per-user 限流；若业务上 form_key 只来自固定几个长表单，更稳妥的是把它收敛为服务端枚举白名单。

#### 18. max_questions_per_exam 是死配置，但管理端仍展示为「单场最大题数」

- 位置：`backend/app/services/system_service.py` line 41
- 复核：代理报告
- 说明：全仓检索该 key 只有三处：DEFAULT_SETTINGS 定义（本行）、_validate_value 的分支、api/system.py:152/182 的展示标签与数值类型声明——没有任何读取点。真正生效的是试卷模板自身的 config.max_questions（backend/app/services/paper_service.py:182，默认 100）。管理员在系统设置里改「单场最大题数」不会有任何效果，属误导性配置（也违背 YAGNI：为不存在的需求保留了可写配置项与校验分支）。
- 建议：二选一：要么让组卷/开考路径真正读取该设置作为全局上限（paper_service 中与模板 max_questions 取 min），要么从 DEFAULT_SETTINGS、_validate_value 与 api/system.py 的标签/数值类型表中一并删除，避免管理端出现无效开关。

#### 19. encrypt_value 的 RuntimeError 未转换，未配置 TRAINING_ENC_KEY 时保存 SMTP 密码返回 500

- 位置：`backend/app/services/system_service.py` line 162-165
- 复核：代理报告
- 说明：`encrypt_value(sval)` 在未配置 TRAINING_ENC_KEY 时抛 RuntimeError（core/security.py:71-73，config.py 仅在启动时 WARN）。update_settings 不捕获，路由 api/system.py:211-215 只 `except ValueError`，于是管理员在设置页保存 SMTP 密码得到的是未处理 500（无明确文案），与 _decrypt_row_value 为「单个坏行不 500」所做的同类加固（第 129-132 行捕获 RuntimeError/ValueError/InvalidToken）口径不一致。fail-closed 的拒绝语义本身是对的，问题只在错误映射。
- 建议：在 update_settings 的加密分支把 RuntimeError 转成 ValueError（路由已映射为 400）：`try: sval = encrypt_value(sval) except RuntimeError as exc: raise ValueError("未配置 TRAINING_ENC_KEY，无法保存敏感设置") from exc`；或在启动时对缺失的 ENC_KEY 直接 fail-fast。

#### 20. 预览阶段 bcrypt 耗时仍随「不同口令数」线性增长（5000 个不同口令 ≈ 25 分钟 CPU/请求）

- 位置：`backend/app/utils/user_excel.py` line 217-223
- 复核：代理报告
- 说明：docs/fix_changelog.md S8 的修复是按口令去重后只哈希一次，但去重只覆盖「同一初始口令复用」的场景：攻击者（`require_admin` = dept_admin 或 super_admin，见 app/core/deps.py:52）可以构造 5000 行、每行口令都不同的合法文件（每行只需满足 6~72 字节，文件远小于 upload_max_size_mb 默认 10MB），此时 `hash_cache` 退化为 5000 次 bcrypt（本仓注释自述 ~300ms/次）≈ 25 分钟单请求 CPU，且该接口限流为 20 次/小时/用户，足以长期占满线程池。这是 S8 修复后的残余面（去重未解决全不同口令），不是重复报同一取舍。
- 建议：给单次预览的不同口令数设上限（例如 200 个，超出即行级/整体报错），或把哈希推迟到确认导入阶段（那里有 DB 事务节流与行级 SAVEPOINT，且可逐行限速）；也可在返回体里回传 `hashed_count` 以便观测。

#### 21. 判断题答案大小写/布尔单元格口径不一致：TRUE/False/布尔单元格被拒，true/false 通过

- 位置：`backend/app/utils/excel.py` line 332-339
- 复核：代理报告
- 说明：该分支用 `str(answer_raw).strip()` 直接与字面量集合比较，集合里是 `"true"/"false"` 小写，而 openpyxl 读 Excel 布尔单元格返回 Python bool。已实跑验证：答案单元格为 `True`/`False`（Excel 中输入 TRUE/FALSE 会自动转成布尔）或文本 "TRUE"/"False" 时，解析结果均为 `valid=False, error=判断题答案必须是 正确/错误`；只有小写 "true"/"false" 才通过。而单选题分支（第 316 行）是做了 `.upper()` 归一化的，两处口径不一致。
- 建议：先归一化再匹配：`ans = str(answer_raw or "").strip().upper()`，集合改为 `{"正确", "对", "A", "T", "TRUE", "是"}` 与 `{"错误", "错", "B", "F", "FALSE", "否"}`；或对 bool 单元格直接 `answer = "正确" if answer_raw else "错误"`。

#### 22. 填空题答案空位数与题干空位数未交叉校验，会存下「永远判错」的题

- 位置：`backend/app/utils/excel.py` line 341-351
- 复核：代理报告
- 说明：前端按题干里连续下划线数量渲染输入框（frontend/src/composables/useAnswerDraft.ts:36 `q?.question.match(/_{2,}/g)?.length || 1`），作答数组长度 = 题干空位数；判分要求 `len(correct_answer) == len(user_answer)`（app/services/grading.py:85-92）。而本解析用 `if b.strip()` 过滤掉空段后统计空位数：已实跑验证，题干 "甲____与乙____是什么？" + 答案 "甲答案|"（第 2 空漏写）被判定 `valid=True`、answer=`[["甲答案"]]`，该题永远判错且预览不报错。第 350 行的守卫只能拦住 "/" 这类全空段（"///"），拦不住「空位数比题干少」的情况。
- 建议：解析时用与前端同口径的正则统计题干空位数：`expected = len(re.findall(r"_{2,}", question_text)) or 1`，若 `len(answer) != expected` 则行级报错（如「答案空位数(1)与题干空位数(2)不一致」）。

#### 23. 拖拽题重复左项被静默覆盖：left_items/right_items 与 answer 映射数量不一致

- 位置：`backend/app/utils/excel.py` line 359-376
- 复核：代理报告
- 说明：`mapping[left] = right` 对重复左项是覆盖写，而 `left_items`/`right_items` 仍逐行追加。已实跑验证："HTTP:80\
HTTP:443" 解析为 left_items=['HTTP','HTTP']、right_items=['80','443']、answer={'HTTP': '443'}——第一对配对丢失，且题干列出的 2 个左项只对应 1 条答案映射。判分 `_grade_drag` 要求 `len(correct_answer) == len(user_answer)`（app/services/grading.py:109），前端拖拽映射是 `Record<string,string>`（同键只能有一个值），该题实际不可作答，但预览显示 valid=True。
- 建议：解析时检测重复左项并报行级错误（如「拖拽题左项重复：HTTP」），或去重并同步裁剪 left_items/right_items，保证三者长度与配对一致。

#### 24. 工作簿中没有任何匹配 Sheet 时静默返回 0 题、无任何错误提示

- 位置：`backend/app/utils/excel.py` line 203-206
- 复核：源码复核
- 说明：`for sheet_name in SHEET_ORDER: if sheet_name not in wb.sheetnames: continue` 直接跳过不存在的 Sheet；已实跑验证：一个只有 `SingleChoice` 工作表（或表名带尾随空格，如 "单选题 "）的文件，`parse_workbook` 返回 `total=0, errors=[], type_dist={}`，接口给前端一个「0 题、无错误」的预览，用户完全无法得知原因（表名写错/未用中文模板）。`app/utils/user_excel.py:190` 的 `if "用户" in wb.sheetnames:` 是同样问题。
- 建议：循环结束后若 `not rows and not errors`，追加一条整体错误，例如 `errors.append({"sheet": "", "row": 0, "error": "未找到任何模板工作表（应为：" + "、".join(SHEET_ORDER) + "），请下载最新模板"})`；user_excel 同理。

#### 25. 超过 _IMPORT_ROW_MAX 的行被静默截断，无标记、无错误项、无日志

- 位置：`backend/app/utils/user_excel.py` line 198-208
- 复核：代理报告
- 说明：`if len(rows) >= _IMPORT_ROW_MAX: break` 与 `if r_idx > _IMPORT_ROW_MAX + 1: break` 都会直接结束解析，既不追加 `errors` 项也不在返回体中给出任何信号（返回体只有 rows/total/valid_count/errors/confirm_token）。5001 行的文件与 5000 行的文件预览完全一致，用户会以为已全部导入。对照组：题库导入 `app/services/import_service.py:65` 明确回传 `truncated = preview_obj.total >= PARSE_ROW_MAX`，本路径缺该口径。
- 建议：预览返回体增加 `truncated: bool`（并在被截断时按 WARNING 记录 `logger.warning("user import preview truncated at %s rows (user_id=%s)", _IMPORT_ROW_MAX, user_id)`），或直接追加一条 `{"row": _IMPORT_ROW_MAX + 1, "error": "超过单次导入上限 5000 行，其余行未导入"}`。

#### 26. POST /exams/mock/start 无任何限流，且 _cleanup_stale_mock_defs 对「已有会话」的定义永不回收，普通用户可无界增长 exam_definitions / exam_sessions / exam_questions

- 位置：`backend/app/api/exams.py` line 58-70
- 复核：已实测复现（8 次开考 → 8 定义/8 会话/68 固化题） ｜ ✅ 已修复（保留数可配置 + 清理真正生效）
- 说明：mock 开考会新建或复用 ExamDefinition，并在 exam/mock.py:316-335 新建定义 + 固化题目 + 建会话；清理函数 `_cleanup_stale_mock_defs`（mock.py:228-263）明确只删「没有 ExamSession」的 ongoing 定义，而 start_mock_exam 每次都会经 start_exam 建出会话，因此实际一条都清不掉，其注释声称的「避免 exam_definitions 无界堆积」并未生效。`_mock_rules_key` 只要 bank_ids/size/type_quota/allocation/objective_only 任一不同就是新定义，普通用户很容易构造出大量不同组合。实测（临时探针，已删除；题库 40 题、同一用户连续 8 次 /api/exams/mock/start）：得到 7 个 exam_definitions、7 个 exam_sessions、160 行 exam_questions，全部保留。路由层没有任何 rate_limit（本文件也未 import app.core.rate_limit），与 /records/practice/answer 有 600/5min 兜底的做法不一致。
- 建议：1) 在 start_mock 路由加用户维度限流（如 `check(f"mock-start:user:{user.id}", 20, 3600, "模拟考试")`）；
2) 让清理逻辑真正生效：对同一用户限制 ongoing mock 定义数（例如保留最近 N 个，超出的连同其未提交会话一并清理），或改为按用户+规则键复用/软删，而不是只依赖「无会话」这一永不成立的条件。
> 实际落地方式与代理原始方案可能不同，以第二节为准。

#### 27. 部门管理员批量导入的用户不带 dept_group_id，模板「分组ID」留空时创建出自己都看不到、管不了的用户

- 位置：`backend/app/api/users.py` line 238-253
- 复核：代理报告
- 说明：手动新增路径显式把创建者的部门写入新用户（api/users.py:106 `dept_group_id=user.dept_group_id if scope is not None else None`，并在 changelog 里说明是刻意同事务写入），但导入路径只调用 `user_service.import_users(...)`，该函数构造 User 时不含 dept_group_id（user_service.py:537-544），分组关联仅来自 Excel 的「分组ID」列。而数据范围判定是 `dept_group_id ∈ scope OR user_groups.group_id ∈ scope`（core/deps.user_in_scope / user_ids_subquery），模板又明确允许「分组ID」留空（utils/user_excel.py:83）。因此部门管理员导入一行不填分组ID的用户后：该用户不在其 scope 内 → `GET /admin/users` 列表看不到、approve/disable/reset-password 均 403，只有超管可见；再次导入同一邮箱只会得到「该邮箱已存在」。创建者无法在界面上修复，属跨路径行为不一致导致的孤儿用户。
- 建议：让导入路径与手动新增同口径：import_users 增加 `dept_group_id` 入参（部门管理员调用时传 actor 的 dept_group_id），在构造 User 时写入；或至少对「scope 非 None 且该行无任何 group_ids」的行报错/自动归属，避免静默产出范围外用户。

#### 28. 「密码 UTF-8 ≤ 72 字节」这条业务规则在 5 处各写一份，且请求 schema 被塞进路由模块

- 位置：`backend/app/schemas/auth.py` line 10-13
- 复核：代理报告
- 说明：同一规则至少有 5 份实现：schemas/auth.py:10-13 `_validate_password_bytes`；api/users.py:27-32 `ResetPasswordIn.validate_password_bytes`；api/users.py:45-50 `UserCreateIn.validate_password_bytes`；services/user_service.py:523-524（内联）；utils/user_excel.py:160-163（内联）。此外 core/security.py:21-25 hash_password 里还有第 6 份兜底。任何一处漏改（例如以后放开到 128 字节）都会造成「预览通过、落库 500」这类跨路径不一致。附带问题：ResetPasswordIn / UserCreateIn 定义在 api/users.py 里，而项目其余请求模型都在 app/schemas/（user.py 只有 UserUpdate），分层与可发现性不一致。
- 建议：把规则收敛到一处，例如 core/security.py 增加 `MAX_PASSWORD_BYTES = 72` 与 `validate_password_bytes(value: str) -> str`，schema 校验器与 user_service/user_excel 的内联判断统一调用它；并把 ResetPasswordIn / UserCreateIn 移入 app/schemas/user.py。

#### 29. utils 层直接 raise fastapi.HTTPException（peek_preview/consume_preview），与项目「服务层只抛 DomainError」的分层约定冲突

- 位置：`backend/app/utils/user_excel.py` line 252-278
- 复核：代理报告
- 说明：peek_preview / consume_preview 在函数体内 `from fastapi import HTTPException` 并抛出 400/403，而它们是被 api/users.py:246,251 直接调用的业务校验函数（预览过期判定、confirm_token 归属校验）。全仓 app/services/**、app/utils/** 中只有这一个模块抛 HTTPException（其余仅 `from fastapi import status` 取常量），同一流程的题库版实现 import_service.do_import 走的是 `DomainError`（import_service.py:99-107）。结果是：这类错误绕过 main.py 的 DomainError 处理器语义、使 utils 无法脱离 FastAPI 复用，也让「服务层不依赖传输层」的约定出现例外，后续若有人照抄这段代码会继续扩散。
- 建议：把这两个函数改成 `raise DomainError(status.HTTP_400_BAD_REQUEST, ...)` / `DomainError(status.HTTP_403_FORBIDDEN, ...)`（app.core.errors），由 main.py 的统一处理器映射为同样的 {"detail": ...} 响应，API 契约不变；同时可去掉函数内的延迟 import。

#### 30. 登录失败与限流拒绝没有任何日志，暴力破解/撞库在服务端不留痕迹

- 位置：`backend/app/api/auth.py` line 113-120
- 复核：代理报告
- 说明：login 路由只有两个限流点（ip_limit 与 check），auth_service.login 对「邮箱或密码错误」「账号已禁用」「待审批」「未验证」全部直接抛 DomainError，全程无 logger 调用；core/rate_limit._deny 抛 429 时也不记录。相比之下 user_service._check_scope/_check_manage_permission 在越权拒绝时会写 WARNING（user_service.py:99,112-117），说明项目已有「安全边界要留痕」的约定，认证路径是唯一缺失的地方。运维无法从日志判断是否正在被撞库、哪个 IP/账号在被扫，只能看到 401/429 响应。
- 建议：在 login 的失败分支与限流拒绝处补 WARNING 日志，只记非 PII 标识（如 actor_ip、失败原因枚举、账号的 user_id 或邮箱的哈希前缀），例如：
```python
except DomainError as exc:
    logger.warning("[auth] 登录失败：ip=%s reason=%s", get_request_ip(), exc.status_code)
    raise
```
并在 rate_limit._deny 里按 scope 记一条 WARNING（限流键已含 IP/邮箱，注意不要直接落邮箱原文）。

#### 31. 显式传 parent_id=null 可绕过「部门管理员不得挂到自身子树外」的守卫，把分组从本部门树里摘出去

- 位置：`backend/app/api/groups.py` line 40-41
- 复核：源码复核
- 说明：守卫写成 `payload.parent_id is not None and payload.parent_id not in scope`，因此 `{"parent_id": null}` 走的是「未传」分支之外的路径：校验被跳过，group_service.update_group 执行 `setattr(g, "parent_id", None)`（group_service.py:90-98），分组被提升为根节点。对部门管理员而言，这等于把自己的子分组（甚至自己 dept_group_id 指向的分组）移出本部门子树——与第 39 行注释声明的「不可把分组挂到自身子树外的父分组（防扩张数据范围）」意图相反。影响是组织结构被改坏 + 该分组从调用者 scope 中消失（同部门其他人的数据范围也随之变化），不构成提权，但确实是一条未受守卫约束的写路径。
- 建议：用 `model_fields_set` 区分「未传」与「显式 null」，并让显式 null 也受同一约束（例如部门管理员不允许把分组提升为根，只有 super_admin 可以）：
```python
if scope is not None and "parent_id" in payload.model_fields_set:
    if payload.parent_id is None or payload.parent_id not in scope:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权把分组移出本部门")
```

---

### 4.3 P2 级（可选）

#### 1. 验证码失败计数记到最新一条而非用户实际提交的那条

- 位置：`backend/app/services/auth_service.py` line 179-216
- 复核：源码复核
- 说明：_latest_unused 按 id desc 取第一条，代码不匹配时 _record_code_failure(ev.id) 增加的是最新那条的 attempts。用户先收到验证码 A、又请求到 B、却输入 A 时，A 永远保持 unused 且不可用，被消耗的却是 B 的 5 次尝试机会；输入旧码达到 5 次会把最新码置为 used，用户必须再次请求验证码。
- 建议：按 (email, code) 定位待消费行再记失败，或在发送新码时把同邮箱旧的 unused 行置为失效，使「失败计数」与「被尝试的验证码」一致。

#### 2. 授权判定中硬编码角色/状态魔法字符串

- 位置：`backend/app/services/user_service.py` line 111,152,163,192,198,233,298,305
- 复核：代理报告
- 说明：文件顶部已定义 ADMIN_ROLES 并导入 USER_ROLE/USER_STATUS，但「最后一个超管」守卫、删除/降级/审批等授权分支仍硬编码 "super_admin"/"active"/"pending"（auth_service.py:164,320-323 同样）。这类字面量一旦拼错会让安全守卫静默失效而非报错，也与注释中「避免同一枚举在多处漂移」的意图相悖。
- 建议：在 models/user.py 增加 ROLE_SUPER_ADMIN = "super_admin"、STATUS_ACTIVE = "active" 等常量，授权分支统一引用。

#### 3. 门面模块中 5 个重导出已无任何引用方

- 位置：`backend/app/services/stats_service.py` line 34-55
- 复核：源码复核（其中 rank 重导出已随下线删除）
- 说明：第 57-58 行的注释声称「api 层与既有测试依赖这些导入路径」，但在 backend/ 全仓（app/、tests/、scripts/）grep 确认：`_rank_by_group`、`_streak_from_dates`、`_user_labels`、`stats_service.logger`、`stats_service._TZ` 均无引用（`_TZ` 的测试用的是 `app.services.stats.common._TZ`，见 tests/test_review_residual_fixes.py:663）；真正被外部使用的只有 `_date_str`、`_utcnow`、`_day_bounds_utc`、`_streaks`。这 5 个 `X as X` 重导出属于死代码，且注释与现状不符，会误导后续维护者继续为它们保留兼容性。
- 建议：删除 `logger`、`_TZ`、`_rank_by_group`、`_streak_from_dates`、`_user_labels` 的重导出，并把注释改为仅说明保留下来的 4 个私有符号；若担心外部脚本依赖，改用模块级 `__getattr__` 做一次性兼容并在其中发 DeprecationWarning。

#### 4. publish_results 的 bg 参数无类型标注，与项目既有写法不一致且 mypy 无法校验

- 位置：`backend/app/services/review_service.py` line 178
- 复核：代理报告
- 说明：`bg=None` 使参数退化为隐式 Any（第 289 行 `bg.add_task(...)` 因此完全绕过类型检查）；同类后台任务参数在 auth_service.py:76/126/275 都标注为 `bg: BackgroundTasks`。exam/admin.py:503 的 publish_exam 有同样问题，说明是复制扩散而非有意为之。
- 建议：改为 `bg: BackgroundTasks | None = None`（从 fastapi 导入 BackgroundTasks），并同步修正 exam/admin.py:503；如需保持服务层不依赖 FastAPI，可改为传入 `Callable[[Callable[..., object], object], None]` 形态的通知回调接口。

#### 5. 自动题库名使用服务器本地时间而非业务时区

- 位置：`backend/app/services/import_service.py` line 118
- 复核：代理报告
- 说明：`time.strftime('%Y%m%d%H%M%S')` 取的是进程本地时区，而全站时间口径已收敛为 UTC + `core/timeutil.business_tz()`（config.TRAINING_TZ，默认 Asia/Shanghai）。容器常以 UTC 运行，此时自动命名会与管理员看到的业务时间相差 8 小时，题库名里的时间戳具有误导性。
- 建议：改用 `datetime.now(business_tz()).strftime('%Y%m%d%H%M%S')`（from app.core.timeutil import business_tz），与业务展示口径保持一致。

#### 6. truncated 由 total >= PARSE_ROW_MAX 二次推导，与解析侧常量为两份来源

- 位置：`backend/app/services/import_service.py` line 65
- 复核：代理报告
- 说明：解析器本身在达到 PARSE_ROW_MAX 时 break（app/utils/excel.py:221-234），但「是否被截断」这个事实没有被解析器返回，改由 import_service 用 `total >= PARSE_ROW_MAX` 反推：恰好 10000 行的完整文件会被误报为「已截断」（前端会弹告警），且 PARSE_ROW_MAX 一旦在 excel 侧调整/两侧不同步，判定即失真（tests/test_review_residual_fixes.py:343 也只能 patch import_service 侧的常量，无法真正触发解析截断）。
- 建议：在 UploadPreview 上增加 `truncated: bool` 字段，由 parse_workbook 在 break 时置位，import_service 直接透传，去掉对 PARSE_ROW_MAX 的重复依赖。

#### 7. actor 为 NULL 的日志一律显示「系统」，被删用户的历史操作与真正的系统日志不可区分

- 位置：`backend/app/services/audit_service.py` line 111-115
- 复核：代理报告
- 说明：user_service.delete_user 会把该用户的历史审计行 `actor=None`（保留痕迹的刻意设计），而 list_logs 对 actor 为空的记录统一输出「用户#」缺失时的 "系统"。结果是「被删除管理员做过什么」在审计界面里变成了系统行为，且这些行对部门管理员不可见，审计可追溯性被削弱。
- 建议：保留可区分性：删除用户时不置空 actor（改为不设外键或加 actor_label 列），或在置空的同时把原 actor id 写入 detail（如 {"deleted_actor": id}），list_logs 对 actor_id 为空但 detail 含该字段的记录显示「已删除用户#N」。

#### 8. 批量发信失败日志缺少考试/批次标识

- 位置：`backend/app/services/mail_service.py` line 128, 155
- 复核：代理报告
- 说明：`[mail] 考试通知发送失败 %d/%d` 与 `[mail] 成绩公布通知发送失败 %d/%d` 只记录计数，没有考试 id/名称或批次标识。失败率异常时运维无法从日志定位是哪场考试、哪个批次，只能反查数据库（符合「错误路径应记录操作名/ID/结果」的缺口，不含 PII）。
- 建议：把 exam_name（或调用方传入的 exam_id）加入日志：`logger.warning("[mail] 考试通知发送失败 %d/%d（exam=%s）", failed, len(recipients), exam_name)`。

#### 9. 部分公共函数缺少 docstring（Args/Raises）

- 位置：`backend/app/services/mail_service.py` line 96, 102, 131
- 复核：代理报告
- 说明：同文件里 send_safely/_safe_header/send_smtp_test 都写了 Google 风格 Args/Returns/Raises，而对外暴露的 send_register_code（96）、send_exam_publish（102）、send_review_done（131）以及 audit_service 的 save_draft/load_draft/clear_draft 没有任何 docstring；mail_service 的这三个函数是会抛 MailError 的同步发送入口，调用方（后台任务/路由）需要知道异常语义。ruff 未启用 D 规则，故仅为一致性与可维护性问题。
- 建议：为上述公共函数补 Google 风格 docstring，至少标明参数、是否抛 MailError 以及是否阻塞（同步 SMTP）。

#### 10. 模板自带示例行会被当作真实题目导入

- 位置：`backend/app/utils/excel.py` line 109,151-187
- 复核：已实测复现
- 说明：`build_template` 在每个 Sheet 调用 `_add_example` 写入一行示例；`parse_workbook` 对示例行没有任何识别逻辑。已实跑验证：`parse_workbook(build_template())` 返回 `total=6`，6 条示例题全部 `valid=True`（如「以下哪一项是 HTTP 默认端口？」）。用户下载模板后原地填写而不删除示例行时，示例题会随真实题目一起入库，且预览中不会提示。
- 建议：在「说明」Sheet 明确要求删除示例行，或在模板示例行的题干加可识别前缀（如 `【示例】`）并在 `parse_workbook` 跳过以该前缀开头的行。

#### 11. TYPE_TO_SHEET 常量定义后从未被引用（死代码）

- 位置：`backend/app/utils/excel.py` line 57-64
- 复核：源码复核
- 说明：全仓 grep（app/、scripts/、tests/）只有定义处一处命中，且它的映射是「Sheet 名 → 同名」，本身也无信息量。`_parse_row` 直接用 `sheet_name` 作为题型。
- 建议：删除该常量。

#### 12. _parse_options 的「| 分隔」回退分支不可达（文档承诺的行为不生效），另有一处无效赋值

- 位置：`backend/app/utils/excel.py` line 375,413-414
- 复核：源码复核
- 说明：`if not result and "|" in text` 永远为假：`result` 为空只可能是 `parts` 为空，即 text 全为空白，此时 text 里也不会有 "|"。已实跑验证：`_parse_options("甲|乙")` 返回 `['甲|乙']`（单个选项，含竖线），而 docstring 第 403 行承诺会拆成两个；只有 "A.甲|B.乙" 这类带前缀的写法才正常。另外第 375 行 `options = None` 是给已为 None 的变量重复赋值。
- 建议：若确实要支持竖线分隔，把判断提前到前缀解析之前（先按 "|" 拆分再逐个剥离 "A." 前缀）；否则删除该死分支并同步修正 docstring。同时删除第 375 行的无效赋值。

#### 13. preview() 的 db: Session 参数完全未使用（死参数），调用方却在持有未提交事务时等待它返回

- 位置：`backend/app/utils/user_excel.py` line 179
- 复核：源码复核
- 说明：函数体内没有任何 `db` 引用（grep 确认仅第 179 行出现），测试也以 `user_excel.preview(None, ...)` 调用。而路由 `app/api/users.py:227,233` 先执行 `__import_settings_max_mb(db)`（内部 `db.execute(select(Setting))`，见 app/services/system_service.py:106-110），使会话签出连接并开启事务，随后才 `await run_in_threadpool(user_excel.preview, db, content, user.id)`；结合上面 bcrypt 的耗时，连接会在事务打开状态下被占用数分钟，并发预览会耗尽连接池。
- 建议：删除 `db` 参数（调用方同步去掉），或让它承担真实职责——例如在预览阶段用 `dept_scope_ids` 校验分组ID是否落在调用者数据范围内，避免无意义地长期持有会话。

#### 14. 关键返回值与错误结构缺少类型标注（Any/dict 裸用）

- 位置：`backend/app/utils/excel.py` line 29,199,295
- 复核：代理报告
- 说明：`open_workbook` 无返回类型（应为 openpyxl 的 `Workbook`）；`errors: list[dict]` 与 `_parse_row(row: tuple, r_idx: int) -> dict`（excel.py:295、user_excel.py:126）用裸 `dict`，错误项的键（sheet/row/error）只能靠阅读拼装处推断；`app/schemas/question.py:109` 也是 `errors: list[dict]`。改动这些结构时无静态检查兜底。
- 建议：为错误项定义 `TypedDict`（如 `class RowError(TypedDict): sheet: str; row: int; error: str`），`open_workbook(buf: BytesIO) -> Workbook` 补返回类型，并把 `errors` 标注为 `list[RowError]`。

#### 15. PUT /system/settings 只捕获 ValueError，未配置 TRAINING_ENC_KEY 时保存 SMTP 密码会 500

- 位置：`backend/app/api/system.py` line 209-213
- 复核：代理报告
- 说明：system_service.update_settings 对加密项调用 `encrypt_value`，而 encrypt_value 在 TRAINING_ENC_KEY 为空时抛 `RuntimeError("TRAINING_ENC_KEY 未配置，无法保存敏感设置")`（core/security.py:71-73）；路由只 `except ValueError`，RuntimeError 直接变成 500。同文件/同模块对「解密失败」已经做了显式降级（system_service._decrypt_row_value 捕获 RuntimeError/ValueError/InvalidToken 并记 WARNING），说明这是遗漏而非刻意设计：管理员在未配置密钥的部署上打开 SMTP 页面保存密码，只会看到 500 与堆栈，而不是「请配置 TRAINING_ENC_KEY」的可操作提示。
- 建议：在路由同时捕获 RuntimeError 并返回可操作错误（400 或 503），例如：
```python
except RuntimeError as exc:
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
```

#### 16. _iso_bound 的「纯日期」启发式用解析后的值判断，会把显式 UTC 00:00 的上界扩成当天末尾，且纯日期下界被当作 UTC 00:00

- 位置：`backend/app/api/audit.py` line 53-66
- 复核：代理报告
- 说明：`if end_of_day and value.time() == time(0, 0)` 是在 astimezone(utc) 之后判断的，无法区分「客户端只给了日期」与「客户端明确给了 UTC 零点/08:00+08:00 这类等于 UTC 零点的时刻」：`to=2026-02-13T08:00+08:00`（= 00:00Z）会被静默扩到当天 23:59:59.999999Z，多带回约一天记录。另一侧不对称：`from` 不做任何补偿，纯日期 `from=2026-02-13` 会被当作 UTC 零点，在 Asia/Shanghai 部署下丢掉当地 00:00-08:00 的记录（docstring 只解释了上界为什么补到当天末尾）。当前前端 Audit.vue 只传 page/action/target_type，未使用 from/to，所以影响面限于脚本/直接调用方，但边界语义仍应修正或写清。
- 建议：把「是否为纯日期」的判断挪到原始输入字符串上（例如路由参数改用 `date | None` 或保留原始字符串，用是否含 'T' 判断），而不是用解析后的 time() 推断；下界同样按业务时区零点归一，保持两侧口径一致。


---

## 五、测试覆盖映射与测试反模式


> **本轮（2026-09-23 第三批）已补的缺口**：
> 1. 管理端守卫矩阵：18 条路由 × 未登录 401 / 普通用户 403 / dept_admin 仅超管路由 403 / super 放行
>    （`tests/test_admin_guard_matrix.py`，72 例）；
> 2. Excel 导入 HTTP 层：题库/用户导入的 preview→import 全链路、413 体积上限、非 xlsx 400、
>    dept_admin 越权导入管理员行 403 且 token 不被消费、截断标记与缺 Sheet 可见错误
>    （`tests/test_admin_import_http_and_audit_scope.py`，8 例）；
> 3. 审计日志 scope：super_admin 看全量（含系统日志）、dept_admin 只见范围内操作者；
> 4. 并发禁用超管：两个超管并发互禁 → 只成功一个，始终保留可用管理入口；
> 5. 把「断言 SQL 文本」改为**行为断言**：`refresh_daily`/`refresh_user_daily` 在重建阶段
>    持有写锁（另一连接写入被阻塞），不再匹配 `BEGIN IMMEDIATE` 字符串。
> 另补：认证路由 401/400/422、限流 429 + Retry-After、XFF 信任开关、考试真并发、
> 开考幂等与 `max_attempts`/时段拦截、上传读取上限、草稿体积与限流
> （`tests/test_http_auth_rate_limit_and_concurrency.py`，14 例）。
> **本批（2026-09-23 第四批）已补**：账号失效路径（禁用/删除后旧 token → 403）、
> 并发公布成绩（只结算一次、通知邮件只入队一次）、并发开考（唯一索引冲突回退返回同一会话，
> 不 500）、验证码正向断言（原用例只断言错码失败，恒 False 的实现也能全绿）、
> 认证路由其余端点（弱口令 422 / 未知验证码 400 / 重发在未配置 SMTP 时 503、未知邮箱不泄露）。
> 「断言查询条数」一项已随 `test_stats_rank.py` 的删除自然消失（排行榜下线时移除）。
> **本批（2026-09-23 第五批）已补**：分层守卫（services/utils 不得 import fastapi）、
> 标签筛选下推 SQL、可用考试列表上限、审计时间边界（业务时区 vs 绝对时刻）、
> `objective_score` 语义、5xx 结构化日志、`bank_name` 长度上限
> （`tests/test_layering_limits_and_logging.py`，7 例）。
> **收尾（2026-09-23 第六批）**：死常量、`_parse_options` 不可达分支、验证码失败计数归属、
> 未配置 ENC_KEY 时的 503（3 例）。至此第四节清单中**除「保留取舍」外已无未处理项**。


### 分片 B：测试覆盖映射与测试反模式（代理原始结论）

现状：`backend/tests` 45 个文件、463 用例全绿（102s，无 skip/xfail）。覆盖面偏「已修复缺陷的回归」，主链路仍有关键空洞。

#### 覆盖缺口

##### 1. 认证路由层（除 /auth/me 外）零 HTTP 覆盖
- 位置: app/api/auth.py:42-148；测试仅 test_api_routes.py:32,52 打 /api/auth/me
- 说明: login 账号维度限流、send-code 验证码校验 400、register-groups 的 fail-closed/required、change-password/resend/verify 全无路由级断言；漏挂依赖或改错路径不会被发现。
- 建议: 补 /api/auth/login 错密码→401、send-code 错 captcha→400、register-groups 未配置→`{"groups":[],"required":false}`、change-password 弱口令→422。

##### 2. 限流只有类级单测，路由层 429 与 XFF 解析零覆盖
- 位置: tests/test_rate_limit.py:9-50；app/core/rate_limit.py:87(check)、:95(ip_limit)、:25(get_client_ip) 无测试
- 说明: 全仓无任何 429/Retry-After 断言；test_smtp_and_register_groups.py:55 还把 check 打成 no-op。ip_limit 依赖被从路由删掉也不会报警（暴力破解/邮件轰炸面）。
- 建议: TestClient 连打 /api/auth/captcha 至第 61 次断言 429 + Retry-After；monkeypatch TRAINING_TRUST_PROXY=true 断言取 XFF 首段。

##### 3. 考试乐观锁只有串行复现，没有真并发
- 位置: tests/test_exam_review.py:79（「一成一冲突」实为同线程顺序重放）；app/services/exam/scoring.py:57-67、:141-149
- 说明: submit_answer 的 version 竞态与 submit_exam 重复结算（两请求同时交卷）只有真实并发才暴露；全仓仅 review 有线程用例（test_exam_review.py:334）。
- 建议: 仿 test_exam_review.py:334 用 threading + 各自 SessionLocal：并发同 version 提交断言恰好 1 成功 1 个 409；并发 submit_exam 断言 ExamResult 仅 1 行、分数不翻倍。

##### 4. start_exam 的并发兜底分支零覆盖
- 位置: app/services/exam/sessions.py:149-162（IntegrityError→回退既有会话）、:236-241（_persist_exam_questions 并发回滚）
- 说明: test_security_regressions.py:314 只验证 uq_active_exam_session 这条 DB 约束，未验证代码冲突后返回既有会话；该分支抛裸 IntegrityError 时考生开考即 500。
- 建议: 预置 in_progress 会话后调用 start_exam 触发该路径，断言返回既有 session_id 且库中仅 1 个进行中会话。

##### 5. max_attempts 与考试时段规则无行为测试
- 位置: app/services/exam/sessions.py:74-76、:99-101、:128-130；测试中 max_attempts 仅出现在 schema 断言（test_admin_deletes.py:79）
- 说明: 「已达最大尝试次数」400、state=max_reached/not_started/ended、「考试不在开放时段」400 全未验证——无限重考/提前开考不会被拦。
- 建议: max_attempts=1 交卷后再开考断言 400 且 state=max_reached；start_at=未来断言开考 400 且 state=not_started。

##### 6. Excel 导入两条链路的 HTTP 层未测
- 位置: app/api/users.py:354-405、app/api/questions.py:159-210；测试只到服务层（test_import_and_user_excel.py；test_api_admin_routes.py:190-202 只测 .txt→400）
- 说明: /api/admin/users/import/preview 的 400/413、/api/admin/users/import 的 Form confirm_token + peek→403（非超管导入管理员行）、/api/admin/upload/preview 的成功与 413 均未走 HTTP，分块读取与大小上限零覆盖。
- 建议: 各补 200 与 413 各一条（upload_max_size_mb 调成 1），并断言 dept_admin 带 super_admin 行→403 且 token 未被消费。

##### 7. 管理端 require_admin/require_super 守卫只覆盖 questions/system
- 位置: tests/test_api_admin_routes.py:205-213、:305-317；对应 app/api/exams.py:130-239、app/api/audit.py:26、app/api/panel.py:31-40
- 说明: /api/admin/exams*、/admin/exam-results、/admin/review/*、/admin/audit-logs、/admin/panel/refresh(require_super) 均无「普通用户→403」「dept_admin 打超管路由→403」断言。
- 建议: 参数化对两类守卫各路由各断言一次 403（user 与 dept_admin 两种 token）。

##### 8. 审计日志只做 200 冒烟，scope 与时间范围未验证
- 位置: tests/test_api_user_routes.py:395；app/services/audit_service.py:56、app/api/audit.py:26-48
- 说明: 无用例断言 dept_admin 只能看到范围内操作者、actor/action/keyword 过滤生效；A 读 B 部门审计在 HTTP 层无回归。
- 建议: 造两部门审计行，dept_admin token 调 /api/admin/audit-logs 断言只含本部门；带 from/to 纯日期断言含当天记录。

##### 9. publish_results 的并发幂等与邮件副作用未验证
- 位置: app/services/review_service.py:229-296；test_review_verdicts.py:268-286 仅单线程
- 说明: 条件 UPDATE + RETURNING 保证「并发发布只计一次、不重复发信」是核心承诺，但无并发用例。
- 建议: 双线程并发 publish_results，断言 published 合计=1、邮件任务只入队一次。

##### 10. token_version 之外的账号失效路径未测
- 位置: app/core/deps.py:38-40；测试仅覆盖 ver 不匹配→401（test_security_regressions.py:200-217）
- 说明: 用户被禁用/删除后旧 token 应 403/401，但无断言；禁用/删除是高频管理动作。
- 建议: disabled 用户 token 调 /api/auth/me 断言 403；删除用户后同 token 断言 401/403。

#### 测试反模式

##### 1. 验证码测试只有否定断言（名字与断言不符）
- 位置: tests/test_captcha.py:12-19
- 说明: `test_captcha_verify_correct_consumes` 声称验证「正确码」，实际只断言错误码 False；5 个用例无一断言 verify 返回 True，恒 False 的实现可全绿。
- 建议: 取明文答案断言正确码→True 且二次校验 False。

##### 2. 断言 SQL 文本而非行为（测实现细节）
- 位置: tests/test_stats_refresh_locking.py:17-33
- 说明: 只断言捕获语句含 "BEGIN IMMEDIATE"，既未复现「读快照→DELETE 之间被并发写入」的丢失更新，也会因换锁方式误报。
- 建议: 两连接并发跑 refresh_daily 与 refresh_user_daily，断言最终聚合等于源数据条数。

##### 3. 断言查询条数（实现细节、易碎）
- 位置: tests/test_stats_rank.py:386-422
- 说明: `assert len(statements) <= 8` 把「无 N+1」绑死在当前 SQL 条数，任何无关额外查询（如读设置）都会误报。
- 建议: 改为断言与用户数无关（10 人与 100 人两次运行语句数相等）。

##### 4. 文档字符串声称并发验证，实际只做串行断言
- 位置: tests/test_review_suggestions_batch.py:147-169
- 说明: docstring 称「必须由条件 UPDATE 的 rowcount 拦截」，用例却是单线程调用后断言 400，从未触及并发路径（全仓 rowcount 并发断言为零）。
- 建议: 改为双线程并发禁用同一目标，断言只有一方成功且仍剩一个 active 超管。

##### 5. 真实 sleep 造成时间依赖
- 位置: tests/test_rate_limit.py:25-49（`time.sleep(1.1)` ×2）
- 说明: 依赖真实时钟推进窗口，负载高时变慢或假失败；同仓 test_preview_cache_and_mail.py:19-31 已有注入假时钟写法可复用。
- 建议: 注入 fake `time.monotonic` 推进窗口，而非 sleep。

#### 未覆盖模块清单
- app/api/auth.py: 仅 /auth/me 有 HTTP 测试，其余 8 个端点零覆盖
- app/api/users.py: CRUD 有；import/preview 与 import 两个端点零覆盖
- app/api/questions.py: CRUD 有；upload/preview 仅覆盖扩展名拒绝
- app/core/rate_limit.py: 仅 RateLimiter 类；check/ip_limit/get_client_ip 零覆盖
- app/core/like.py: 无直接测试，仅经 question/user/audit 服务的通配符转义间接覆盖
- app/core/deps.py: dept_scope_ids/user_in_scope 有；users_in_scope 无生产调用点（死代码）且零覆盖
- app/core/request_context.py: 间接（test_review_full_backlog_fixes.py:156）
- app/services/audit_service.py: 写入/save_draft 间接覆盖；list_logs 的 scope 与过滤零覆盖


---

## 六、分片 A 补充结论：schemas/* + api/*

### 分片 A：schemas/* + api/*（代理原始结论，已排除 9 类已确认问题）

#### [CRITICAL] `question-type-stats` 的 id 解析未兜底，超长数字查询参数直接 500
- 位置: backend/app/api/questions.py:113
- 说明: `int(x) for x in s.split(",") if x.strip().isdigit()` —— `isdigit()` 对任意长度纯数字串都为 True，而 CPython≥3.11 的 `int()` 有 4300 位上限。`GET /api/admin/question-type-stats?bank_ids=<5000 个数字>` 一次请求即未捕获 ValueError → 500。
- 修复: 解析前限长兜底（`len(t) > 10` 跳过），或改为 `bank_ids: list[int] = Query(default_factory=list)` 交给 Pydantic。

#### [SUGGESTION] 所有 list 型入参都无长度上限，可直达 SQL `IN (...)`
- 位置: backend/app/schemas/auth.py:31、schemas/exam.py:58,60,155-157、schemas/group.py:41、api/users.py:43
- 说明: 这些列表被原样送进 IN（auth_service.py:150、exam/admin.py:131,141、user_service.py:350,378）。SQLite `MAX_VARIABLE_NUMBER` 实测 250000，单请求 ≥30 万 id 即 `OperationalError: too many SQL variables` → 500；`RegisterIn.group_ids` 走公开注册接口（虽被 IP 限流 10/h，一次请求足够）。
- 修复: 每个 list 字段加 `max_length`（如 200），超限 422。

#### [SUGGESTION] `tags` 分支把整张题目表拉进 Python
- 位置: backend/app/api/questions.py:104（→ services/question_service.py:391）
- 说明: 只要传 `tags` 就走 `select(Question.id, Question.type, Question.tags).where(*conditions)`；super_admin 未同时传 bank_ids/group_ids 时 conditions 为空 → 全表载入再 Python 侧过滤标签。
- 修复: 该分支强制至少一个范围条件，或改用 JSON1（`json_each(Question.tags)`）在 SQL 侧过滤。

#### [SUGGESTION] `/exams/available` 无 limit，全表载入后 Python 侧过滤
- 位置: backend/app/api/exams.py:30（→ services/exam/sessions.py:48-59）
- 说明: 把 `status in (published, ongoing) and type=formal` 的全部考试实例化后逐场判可见性/时间窗；管理端 `list_exams` 有 limit，用户端没有。
- 修复: 加 limit（如 200）并明确排序，或把指派分组可见性下推为 SQL 条件。

#### [SUGGESTION] services/utils 层直接依赖 FastAPI，错误类型两套并存
- 位置: services/{question_service.py:7, practice_service.py:5, review_service.py:8, group_service.py:5, user_service.py:9, auth_service.py:12, import_service.py:15, paper_service.py:23}、services/exam/*.py:7-9、utils/user_excel.py:252,266
- 说明: `core/errors.py` 声明「服务层不依赖 FastAPI」，但 13 个 service 仍 import `fastapi.status`，`user_excel` 直接 `raise HTTPException`；同一类「预览过期」错误在 user_excel 抛 HTTPException、在 import_service.do_import 抛 DomainError，响应/行为随调用路径不同。
- 修复: 统一用 `DomainError`（状态码常量收进 core），utils 只抛领域异常，由路由翻译。

#### [SUGGESTION] 路由模块内定义 schema，密码字节校验多份副本
- 位置: backend/app/api/users.py:22-50（副本：schemas/auth.py:10-13、api/users.py:27-32、45-50；另有 user_service.py:523-524、utils/user_excel.py:160-163、core/security.py:21-25）
- 说明: `ResetPasswordIn`/`UserCreateIn` 定义在路由文件，与 schemas/user.py 归属不一致；`_validate_password_bytes` 抄了多遍，bcrypt 上限口径一旦调整漏改一处即分叉。
- 修复: 两个模型移入 schemas/user.py，复用同一校验函数。

#### [SUGGESTION] 上传限额 + 分块读取逻辑在两个路由重复实现
- 位置: backend/app/api/questions.py:182-198 与 api/users.py:214-228
- 说明: 近 20 行逐字重复（Content-Length 预检、1MB 分块、累计超限 413），业务规则写在路由层；两处已分叉（扩展名来自设置 vs 硬编码 .xlsx）。改一处漏一处即留下无上限上传入口。
- 修复: 抽 `core/uploads.py: read_limited(file, max_bytes, allowed_ext)`，两个路由复用。

#### [SUGGESTION] 「仅超管可建管理员账号」在路由与服务两层重复实现
- 位置: backend/app/api/users.py:88,127,247（服务侧 user_service.py:111,192,509）
- 说明: 同一条垂直越权规则 5 处实现且语义不一致：路由对导入整批返回 403，服务对同一情况只记为该行失败（行级 error）。只改一层即产生绕过或行为漂移。
- 修复: 规则只留服务层，路由不重复判断。

#### [SUGGESTION] async 路由内做同步文件 I/O 与同步 DB 写入
- 位置: backend/app/api/system.py:52-91
- 说明: `upload_logo` 是 async，却在事件循环线程执行 `save_path.write_bytes(content)`（≤2MB）、`FILES_DIR.glob`+`unlink`、`update_settings_svc`（SELECT+commit）。与 questions.py:203、users.py:233 刻意 `run_in_threadpool` 的做法自相矛盾，慢磁盘时阻塞全部并发请求。
- 修复: 改为同步 `def upload_logo(...)`，或把文件写入与 DB 更新放进 `run_in_threadpool`。

#### [SUGGESTION] 草稿体积上限在请求体已全量入内存后才生效
- 位置: backend/app/api/audit.py:79-93
- 说明: `payload: dict = Body(...)` 在进入函数前已完成解析，64KB 的 `json.dumps` 判断只限制落库大小，挡不住请求期内存峰值；该接口也无写入频率限制。
- 修复: 先校验 `Content-Length`（或加全局 body limit 中间件），并对 PUT /drafts 加 `check(f"draft:user:{user.id}", ...)`。

#### [NIT] 列表筛选参数未按枚举/范围校验，非法值静默返回空集
- 位置: backend/app/api/questions.py:82,85
- 说明: `?difficulty=0` 因 question_service.py:223 的 `if difficulty:` 被当作「不过滤」返回全部，`?difficulty=99` 返回空列表，而创建/更新同一字段的非法值返回 400——同一语义三种表现。
- 修复: 用 `Literal[...]`/`Field(ge=1, le=3)` 约束。

#### [NIT] 无界 JSON 字段直接落库
- 位置: backend/app/schemas/exam.py:59（rules）、schemas/question.py:62-68（options/left_items/right_items/answer）、schemas/system.py:11（updates）
- 说明: 作答路径有 `validate_answer_size`(16KB) 保护，但题库创建/考试 rules/设置值无体积上限，可写任意大 JSON blob。
- 修复: 复用 core/limits.py 增加体积校验（rules/options ≤64KB、设置值 ≤4KB）。

#### [NIT] 列表接口直接返回 ORM 对象且无 response_model
- 位置: backend/app/api/questions.py:101
- 说明: `{"items": rows}` 是 `Question` ORM 实例，未声明 `QuestionOut`；字段集随模型演进自动变化，而同文件 create/update 受 QuestionOut 约束。
- 修复: 声明 response_model 或显式构造 dict。

#### [NIT] 路由层零日志，5xx 路径只留框架堆栈
- 位置: backend/app/api/*.py（全文件无 logger/print）
- 说明: core/logconfig.py 与请求上下文（IP）都齐备，但路由层无任何日志点；线上排查只能复现。
- 修复: 全局异常处理器统一 `logger.exception`（带路由、actor id，不含 PII）。

#### 已核对未发现问题（避免重复投入）
> 72 字节口令在登录/改密路径无 500（security.py:31 与各 schema 校验互补）；`/admin/questions` 返回 ORM 时 `_sa_instance_state` 不外泄（实测）；Excel 导入 difficulty 已在 excel.py:378 限 1-3；user_excel 预览响应已剔除明文口令与 hash；mock 组卷的 bank_ids 会与开放题库求交，IN 列表有界。


---

## 七、附录：本轮验证方式与命令

```bash
cd backend
./.venv/bin/ruff check app tests scripts      # All checks passed!
./.venv/bin/ruff format --check app tests scripts
./.venv/bin/mypy app                          # Success: no issues found in 70 source files
./.venv/bin/python -m pytest -q               # 609 passed（新增 157 例、随排行下线移除 11 例；整改前 463）

cd ../frontend
npx vue-tsc --noEmit                          # 通过（无输出）
npx eslint src                                # 通过（无输出）
npx vitest run                                # 70 passed (6 files)
npx prettier --check "src/**/*.{ts,vue}"      # All matched files use Prettier code style!
npx vite build                                # ✓ built

# 关键结论的实弹复现（隔离临时库；脚本置于 /tmp，未入库）
#   1) 三个 Update schema 显式 null → 500（整改后 422）
#   2) dept_group_id=999999 → 外键 500（整改后 400）
#   3) bank_ids=5000 位数字 → int() 500（整改后 200）
#   4) _parse_group_ids("inf") → OverflowError（整改后跳过非法片段）
#   5) 8 次不同题量模拟开考 → 8 定义 / 8 会话 / 68 固化题（整改后按 keep 收敛，已交卷成绩保留）
#   6) 指定已有题库但不传 group_id 导入 → 题目 group_id 全为 NULL（未修复，见第四节）
```
