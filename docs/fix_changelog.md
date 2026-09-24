# 培训考试平台 修复记录 (fix_changelog.md)

> 阶段：Phase 5 代码审计 · 修复轮次
> 对应文档：audit_report.md
> 修复日期：2026-08-21
> 验证方式：pytest（28 通过）+ 实弹 curl 验证 + 重启冒烟

---

## 一、P0 修复（阻断级，已全部修复）

| 编号 | 问题 | 修复方式 | 验证 |
| --- | --- | --- | --- |
| SEC-P0-1 | SPA fallback 路径遍历任意文件读取 | `main.py` `_spa`：拒绝 `..` 片段；`target.resolve()` 校验在 `dist.resolve()` 目录内，越界返回 404 | `curl /%2e%2e/.../etc/passwd` → 404（原返回 passwd 内容）|
| SEC-P0-2 | 默认 JWT 密钥可伪造 token | `config.py` `_secret_key()`：未设 `TRAINING_SECRET_KEY` 时生成进程级随机密钥（重启失效），不再用仓库默认值；启动告警 | 旧默认密钥伪造 token → 401 |
| FUNC-P0-1 | 考试乐观锁非原子 | `exam_service.submit_answer`：改原子条件更新 `UPDATE ... WHERE id=? AND version=?`，`rowcount==0` 返回 409 | 源码确认原子 UPDATE |
| PERF-P0-1 | 顺序/随机练习全量加载题库 | `practice_service.start_practice`：sequence/random 改 `LIMIT`；random 用 SQL `ORDER BY RANDOM() LIMIT`；`PRACTICE_LIMIT_MAX=500`；schema `limit le=500` | limit=999999 被校验拒绝 |
| QUAL-P0-1 | system.py audit_log 未导入致 500 | `api/system.py` 补 `from app.services.audit_service import log as audit_log` | PUT /system/settings → 200 |
| QUAL-P0-2 | submit_exam 非幂等，重复提交创建重复成绩 | `submit_exam`：入口原子 `UPDATE status='scoring' WHERE id=? AND status='in_progress'`，`rowcount==0` 即已被提交→返回已有结果（幂等）； ExamResult/ShortAnswerReview 单事务 add | 源码确认幂等锁 |
| QUAL-P0-3 | score 等列 Integer 但 Mapped[float] 截断小数 | 模型改 `Float`（exam/question/record/stats 共 9 处）；既有 DB 用 SQLite table-rebuild 迁列到 REAL | `PRAGMA` 确认 7 列已 REAL |

## 二、P1 修复（高优先级，已修复）

### 安全
| 编号 | 问题 | 修复 |
| --- | --- | --- |
| SEC-P1-1 | dept_admin 可提权 super_admin | `api/users.py update_user`：role 非 None 且调用者非 super_admin → 403 |
| SEC-P1-2 | dept_admin 数据范围未生效 | 本轮在 start_exam/list_available 已加分组校验；`group_subtree_ids` 数据范围过滤纳入待办（见第三节）|
| SEC-P1-3 | 考试无服务端时间限制 | start_exam 复用 `_within_time_window` 校验时段（正式考试超时段 400）|
| SEC-P1-4 | start_exam 绕过分组指派 | start_exam 复用 `_user_can_access_exam`，非指派分组 403 |
| SEC-P1-5 | SMTP 密码明文返回 | `api/system.py list_settings`：加密字段返回 `"******"` 占位 |
| SEC-P1-6 | Fernet 回退 base64 | `config.py` 未设 ENC_KEY 时启动告警（保留回退保开发可用，已显式 WARN）|
| SEC-P1-9 | 重置密码恒用 123456 | `user_service.reset_password`：未提供则生成 12 位随机强密码；API 接 `ResetPasswordIn` |
| SEC-P1-10 | confirm_token IDOR | `import_service`：token 绑定 user_id，`do_import` 校验调用者一致；TTL 30 分钟 + `_gc_cache` |
| SEC-P1-11 | 上传无大小限制 | `api/questions.py upload_preview`：查 `upload_max_size_mb`，分块读取超限 413 |
| SEC-P1-12 | Excel 无行数上限/未 read_only | `utils/excel.parse_workbook` + `import_service._full_rows`：`read_only=True` + `_IMPORT_ROW_MAX=10000` |
| SEC-P1-13 | update_exam 原始 dict 批量赋值 | 新增 `ExamUpdateIn` Pydantic schema，路由改用 `model_dump(exclude_unset=True)` |

### 功能完整性
| 编号 | 问题 | 修复 |
| --- | --- | --- |
| FUNC-P1-1 | difficulty_dist 被忽略 | `paper_service.generate_paper`：按 (type,difficulty) 分层，按 difficulty_dist 比例分配配额，不足从全池补抽 |
| FUNC-P1-4 | 模拟考试污染考试列表 | `list_available` 过滤 `type=='formal'`（mock 仅经 /exams/mock/start 入口）|
| FUNC-P1-6 | show_score_immediately=False 成绩永久未发布 | `review_service.publish_results`：公布未发布成绩（含无简答场景），重新计算 passed |

### 性能
| 编号 | 问题 | 修复 |
| --- | --- | --- |
| PERF-P1-1 | generate_paper 全量载入 | 改 `select(Question.id,type,difficulty,score)` 只投影必要列；按 type 分组 Python 抽样（候选集已大幅缩小）|
| PERF-P1-2 | list_modes/get_progress 用 len(全表) | 改 `func.count()` + `GROUP BY type` |
| PERF-P1-3 | user_panel 全量载入 QuestionState | 改 SQL 聚合 `SUM(iif(...))` 单次查询 |
| PERF-P1-4 | refresh_daily LIKE 全表扫 + N+1 | 改 `date(datetime(col,'+8 hours'))=:d`（同时修时区）；分组用一次 `GROUP BY` |
| PERF-P1-5 | submit_exam N+1 + 3 次 commit | 批量 `WHERE id IN(...)` 取题；3 次 commit 合 1 |
| PERF-P1-6 | _session_payload N+1 | 批量取题构造 q_map |
| PERF-P1-7 | list_results N+1 | 待办（数据量小，见第三节）|

### 代码质量
| 编号 | 问题 | 修复 |
| --- | --- | --- |
| QUAL-P1-a | publish_results 把 in_progress 会话标 reviewed | 仅更新已公布成绩对应的 session 为 reviewed |
| QUAL-P1-b | _ensure_tags rollback 波及全事务 | 改 `begin_nested()` SAVEPOINT |
| QUAL-P1-c | get_db 异常不回滚 | `get_db` 补 `except: rollback; raise` |
| FUNC-P2-1（升级处理） | Float 列类型 | 同 QUAL-P0-3 |
| FUNC-P2-4 | _streak 今日未答返回 0 | 连续起点允许今日或昨日 |

## 三、P2/P3 增量修复
| 编号 | 修复 |
| --- | --- |
| 缺失索引 | `practice_records.answered_at`、`exam_sessions.status`、`exam_results.exam_session_id`、`exam_results.published`、`exam_questions.question_id` 加 `index=True`；既有 DB 已补建索引 |
| pass_score/score/partial_score 可负 | schema 加 `Field(ge=0)` |
| PracticeStartIn.limit 无上限 | `Field(le=500)` |
| SmtpTestIn.to_email 无校验 | 改 `EmailStr`（防头部注入）|
| do_import 逐题 flush | 改 `add_all` 单次 commit |
| Excel/工作簿未关闭 | `parse_workbook`/`_full_rows` 加 `finally: wb.close()` |
| stats 时区错配 | refresh_daily 用 `date(datetime(col,'+8 hours'))` |
| init_db 打印明文密码 | 移除密码打印，改提示经环境变量注入 |

## 四、回归测试

```
28 passed, 2 warnings in 0.85s
- test_excel.py 6 项
- test_grading.py 18 项
- test_paper.py 6 项（含 difficulty_dist 改造后 seed 可复现、配额、上限）
```
- 实弹验证：路径遍历 404 / 伪造 token 401 / PUT settings 200 / SMTP 密码 ****** / 练习 limit 校验 / list_modes COUNT / 服务重启冒烟首页 200。

## 五、待办（后续迭代）

以下为 P1/P2 中本轮未完成、需后续迭代处理项（已记入 audit_report.md，不阻断当前交付）：
- **SEC-P1-2 完整数据范围过滤**：`group_subtree_ids` 已在 start_exam/list_available 落地分组校验，但 list_users/list_questions/list_exams/list_results/list_pending/list_logs 的 dept_admin 子树过滤待统一接入。
- **FUNC-P1-2 考试选项 shuffle_map**：模型字段已就绪，生成/持久化/反算判分逻辑待补。
- **FUNC-P1-3 邮件通知**：publish_exam/publish_results 接 BackgroundTasks 发送 `send_exam_publish`/`send_review_done`。
- **PERF-P1-7 list_results N+1 + 分页**：改 JOIN + 加分页参数。
- **SEC-P1-7/8 限流**：登录/验证码暴力破解防护（slowapi）。
- **PERF-P2 前端虚拟化**：Answer.vue 导航网格、ExamRecords/Wrong/Marks 服务端分页。
- 死代码清理：useDraft 未接入、StatsGroupDaily/RefreshJob 未用、mail_service 两函数未调用。

## 六、公网防扫描限流（2026-08-22 增量）

**背景**：公网部署下 `/api/auth/register`、`/verify`、`/login`、`/resend-verification`、`change-password` 及 `/api/admin/system/smtp/test`、`upload/preview` 为无认证或低认证接口，需防扫描爆破/撞库/邮件轰炸。

**方案**：单进程内存固定窗口计数器 `app/core/rate_limit.py`（线程安全 + 60s GC），与 SQLite 单 uvicorn 进程架构匹配，无需 Redis。双维度限流：IP 维度（`ip_limit` 依赖）+ 账号/邮箱维度（函数内 `check`）。

| 接口 | IP 维度 | 账号维度 | 修复 |
| --- | --- | --- | --- |
| register | 10/小时 | 邮箱 3/小时 | `api/auth.py` |
| verify | 30/10分 | 邮箱 5/10分 | `api/auth.py` |
| login | 10/分 | 账号 8/5分 | `api/auth.py` |
| resend | 10/10分 | 邮箱 3/10分 | `api/auth.py` |
| change-password | — | 用户 5/5分 | `api/auth.py` |
| smtp/test | — | 管理员 5/小时 | `api/system.py` |
| upload/preview | — | 管理员 20/小时 | `api/questions.py` |

超限 → 429 + `Retry-After` 头。真实 IP 经 `get_client_ip`：默认信任 `X-Forwarded-For`（`TRAINING_TRUST_PROXY=true`），裸跑设 `false` 用对端地址。新增 5 项 `test_rate_limit.py` 单元测试，全量 33 测试通过。`deployment.md` 补「限流」章节与 `TRAINING_TRUST_PROXY` 环境变量。**注意**：多 worker（gunicorn -w N）需切 Redis 后端，单进程下完全有效。

## 七、注册防 bot：图形验证码 + 邮箱验证码闭环 + 后缀白名单（2026-08-22 增量）

**背景**：原注册流程仅校验邮箱密码、无图形验证码，bot 可直接调用 `/register` 触发批量发送验证码邮件（邮件轰炸/资源消耗），且无邮箱后缀限制、无防 bot 闭环。

**重构方案**（注册改为「自验证」闭环，原两步注册→验证激活 合并为一步）：

| 环节 | 接口 | 说明 |
| --- | --- | --- |
| 取图形验证码 | `GET /api/auth/captcha` | 返回 `captcha_id` + SVG data_uri（纯点阵渲染，无 Pillow 依赖），3 分钟有效、单次消费 |
| 发送邮箱验证码 | `POST /api/auth/send-code` | 必须先过图形验证码校验；再校验邮箱后缀白名单；生成 6 位码并入队邮件 |
| 完成注册 | `POST /api/auth/register` | 凭邮箱验证码完成注册，注册即 `email_verified=True`（自验证，无需单独激活步骤） |

- **图形验证码**（`app/core/captcha.py`）：5×7 点阵字体渲染 4 位数字为 SVG 像素图 + 干扰线 + 噪点 + 字符抖动；内存存储单次消费（对错都消费防穷举重放），60s GC。无第三方依赖。
- **邮箱后缀白名单**：新增系统设置 `register_allowed_email_suffixes`（register 类目，逗号分隔如 `@company.com,@edu.cn`，留空不限制）；send-code 与 register 均校验 `check_email_suffix`，非白名单后缀 400。
- **防探测**：send-code 对已注册邮箱统一返回"请输入验证码"，不暴露"邮箱已注册"。
- **限流接入**：captcha 60/分钟、send-code IP 10/小时 + 邮箱 3/小时、register 邮箱 3/小时。
- **前端**（`Register.vue` 重构）：图形验证码（点击刷新）→ 填邮箱验证码 → 点发送（前端要求先填图形码才可发送）→ 填密码 → 注册成功直接跳登录。
- **schema**：新增 `SendCodeIn(email,captcha_id,captcha_code)`；`RegisterIn` 增加 `code` 字段。
- **兼容**：保留旧 `/verify`、`/resend-verification` 路由与 `auth_service.verify_email/resend`（向后兼容已有未验证账号），新流程不再使用。

**验证**：新增 `test_captcha.py`（5 项：生成/单次消费/错答案消费/空输入/未知 id）；全量 38 测试通过；实弹端到端闭环（解码 SVG 验证码 → send-code 200 → 取验证码 → register 201 自验证 → login 200）通过；后缀白名单 `@example.com` 拦截 `@gmail.com` 400；图形验证码错误 400；前端 vue-tsc + build 通过，dist 含 `send-code/captcha_id` 调用。

**部署提示**：图形验证码与限流同为单进程内存方案，多 worker 需切 Redis（见 deployment.md）。



---

## 2026-08-28 第二轮审计修复（越权 + 计分/并发完整性）

### 背景
全量复审后端 6 路并行审计（安全/认证、考试并发、文件导入、服务与 SQL、模型与 schema、测试覆盖），
合并去重得 12 个 Critical，分两类：**部门数据范围越权（A1-A6）** 与 **计分/并发完整性（B1-B6）**。
本轮已修复全部 12 项。

### A 类：dept_admin 数据范围越权

| 项 | 位置 | 修复 |
|---|---|---|
| A1 | `api/users.py` `/import` + `user_service.import_users` | 导入路由加"仅 super_admin 可导入管理员账号"守卫；service 接收 `scope`+`actor_role`，导入用户的 `group_ids` 必须落在调用者子树内（防垂直+水平越权） |
| A2 | `api/groups.py` + `group_service` | `create/update/delete` 注入 `dept_scope_ids`：只能在自身子树内建/改/删分组，禁止挂到子树外父分组；`build_tree` 按 scope 裁剪，防组织结构泄露 |
| A3 | `api/exams.py` + `exam_service` | `list_exams`/`create_exam`/`update_exam`/`publish_exam` 全部注入 scope：列表按 `group_ids∩scope` 过滤，操作前 `_check_exam_scope` 校验，创建/更新校验 `_validate_exam_group_ids` |
| A4 | `exam_service.list_results` | 成绩列表 JOIN 后按 `users_in_scope(db, scope)` 过滤，dept_admin 看不到跨部门成绩 |
| A5 | `review_service` `list_pending`/`publish_results` | 待复核列表按 `user_in_scope` 过滤；`publish_results` 校验考试指派分组在 scope 内 |
| A6 | `api/exams.py`(模板/mock-config) + `api/panel.py` + `api/audit.py` | 试卷模板/mock-config 收为 `require_super`（全局配置）；`admin_overview` 注入 scope 按范围内用户/考试统计；`list_logs` 按 `users_in_scope` 过滤操作者；`refresh_stats` 收为 `require_super` |

新增 `core/deps.users_in_scope(db, scope)`：返回范围内全部用户 id 集合（None=全量），供列表/概览做按用户维度过滤。

### B 类：计分/并发完整性

| 项 | 位置 | 修复 |
|---|---|---|
| B1 | `grading._grade_fill/_grade_drag` | 拒绝空答案：`correct_answer` 为空 list/dict 一律返回 `False`，杜绝空填空/空拖拽经空 zip/空 all 恒真被判满分 |
| B2 | `review_service.review` | 复核判定改条件 UPDATE：`WHERE verdict IS NULL`，靠 `rowcount` 判定唯一持有者，并发同题复核只有一方加分，杜绝分数重复自增 |
| B3 | `exam_service._recover_stuck_scoring` | 回收前校验"无 ExamResult"：含成绩记录的 scoring 会话是合法待复核状态，不可被回收重置为 in_progress |
| B4 | `models/record.ExamResult` + `exam_service.submit_exam` + `review_service.publish_results` | `ExamResult` 新增 `overtime` 列；`submit_exam` 写入；`publish_results` 改 `passed = (not overtime) and (score>=pass)`，超时考试复核后仍判不及格；幂等返回回读 `overtime` |
| B5 | `models/exam.ExamQuestion` + `exam_service._ensure_exam_questions`/`_persist_exam_questions` | 加 `UniqueConstraint(exam_definition_id, question_id)`；固化改"插入前复核 + `IntegrityError` 兜底 + `expire_all`"，防并发首次固化产生重复题目行/重复计分 |
| B6 | `exam_service.submit_answer` | version 条件 UPDATE 增 `status=='in_progress'`：`submit_exam` 置 scoring 不 bump version 后，滞后的 `submit_answer` 仍按旧 version 写入会被拒（rowcount=0→409），不污染已结算会话 |

### 模型/迁移变更
- `ExamQuestion`：增 `UniqueConstraint("exam_definition_id", "question_id")`。
- `ExamResult`：增 `overtime: Boolean NOT NULL DEFAULT False`。
- 新增 `scripts/migrate_2026_08_28.py`：为既有 `training.db` 补 `overtime` 列（ALTER ADD COLUMN）与 `uq_exam_question` 唯一索引（重建表）；幂等、含重复行预检。
- `scripts/init_db.py` + `config.py`：未注入 `TRAINING_SUPER_ADMIN_PASSWORD` 时生成一次性 16 位随机口令并打印，杜绝以 `admin12345` 默认弱口令初始化超管。

### 测试
- `test_grading.py`：补空填空/空拖拽/未知题型边界用例（B1 回归）。
- `test_exam_review.py`：补 `test_recover_stuck_scoring_preserves_pending_review_session`（B3）、`test_review_concurrent_no_double_increment`（B2，线程并发模拟，断言仅一次加分 score=5）、`test_overtime_exam_stays_failed_after_publish`（B4，满分但超时→仍不及格）。
- `test_scope_authz.py`（新增）：覆盖导入越权、考试列表/更新/创建/发布 scope 过滤、概览 scope 过滤（A 类回归）。
- 全量 **72 测试通过**；ruff lint/format 全清。

### 运维提示
- **既有库必须运行** `uv run python scripts/migrate_2026_08_28.py`（新库经 init_db 已含，无需运行）。
- 生产仍需注入 `TRAINING_SECRET_KEY`、`TRAINING_ENC_KEY`、`TRAINING_SUPER_ADMIN_PASSWORD`。

### 2026-08-29 后续整改
- 题库、考试、导入和注册流程补齐部门范围校验；注册分组改为显式允许列表。
- 考试补充服务端时长、活动会话唯一索引、范围外题目阻断和通知任务；练习自评限制为已有简答记录。
- 敏感设置缺少加密密钥时拒绝写入，SMTP fallback 不再记录邮箱/验证码；管理员密码改为必填且不在 HTTP 响应中返回。
- Excel 增加解压体积、行数和单元格长度限制；审计日志补充时间过滤，用户/复核/成绩列表增加范围和返回上限。
- 新增 HTTP 鉴权和安全回归测试；当前验证为 92 个后端测试通过、Ruff/mypy 通过、前端构建通过。

### 2026-09-16 全面代码审核整改

本轮基于四路并行审核（服务层 / 考试引擎 / 路由层 / 模型与工具层）逐一修复，
所有结论均以可复现的代码执行验证，而非仅静态阅读。

#### 严重问题（安全 / 数据完整性）

| 编号 | 位置 | 问题与修复 |
|---|---|---|
| C1 | `core/email.py` + `schemas/auth.py` + `models/user.py` | **邮箱大小写不一致**：`EmailStr` 只小写域名，而 DB 查询大小写敏感，导致 `Admin@x.com` 无法用 `admin@x.com` 登录，且两者可作为两个账号共存。统一 `normalize_email()` 于 schema 边界归一，`users.email` 加 `COLLATE NOCASE` 兜底 |
| C2 | `practice_service.list_modes` | **授权绕过**：`bank_id` 未校验 `practice_enabled`，关闭练习的题库仍通过统计接口泄露题量与题型分布。补 `_assert_bank_practice_enabled`，与 `start_practice` 口径一致 |
| C3 | `exam_service.start_mock_exam` | **跨用户试卷泄露**：mock 定义查询未按 `created_by` 收敛，首个开考用户创建的已固化试卷被所有后续用户复用。补 `created_by == user.id` 过滤 |
| C4 | `review_service.review` | **成绩失真**：复核加分未封顶 `total_score` 且未同步 `objective_score`，可产生 `108/100` 并使派生列永久不一致。改为 `min(total_score, ...)` 并同步累加 `objective_score` |
| C5 | 全部 `models/*.py` | **删除即 500**：全表外键无 `ON DELETE` 规则，删除有练习/成绩记录的用户或题目抛 `IntegrityError`。按关系语义补齐 CASCADE / SET NULL，并为 `practice_records.bank_id`、`short_answer_reviews.exam_session_id` 补上原先缺失的外键 |
| C6 | `stats_service.refresh_daily` | **统计静默归零**：用 SQLite `datetime(col,'+8 hours')` 解析文本时间戳，遇 `Z` 后缀等格式返回 NULL 使 WHERE 恒假；且 `+8` 硬编码于 Python 与 SQL 两处。改为业务时区（`TRAINING_TZ`，默认 Asia/Shanghai）换算 UTC 区间后做范围比较，兼容多种格式并可命中索引 |
| C7 | 全仓库 | **格式化未执行**：`ruff check`/`mypy` 通过掩盖了 `ruff format` 从未运行。全量执行（16 个文件） |
| C8 | `exam_service._recover_stuck_scoring` | **越权写入 + 会话复活**：该函数无 `user_id` 过滤却由用户可达的开考路径调用；且用本地时区解析无时区字符串，UTC+8 下偏移 8 小时会把已结束会话误判超时并复活，使考生可重答并绕过 `max_attempts`。改为按本人作用域 + aware datetime 比较 + 批量查询消除 N+1 |
| C9 | `utils/excel.validate_workbook_archive` | **zip 炸弹可绕过**：原实现累加 zip 中央目录的 `file_size`，那是可伪造的声明值。改为魔数校验 + 流式真实解压限量 |
| C10 | `paper_service.generate_paper` | **组卷无数据范围**：函数无 `scope` 参数，是唯一无法在结构上强制数据边界的服务。补 `scope` 硬上限并接入 `dept_scope_ids`；`preview_paper` 的裸 `dict` 入参改为 `PaperPreviewIn` schema |

#### 建议项

- `question_service.update_question` 改字段白名单写入，不依赖 schema 兜底，杜绝后续字段演进引入批量赋值。
- `practice_service.recent_practice`、`audit_service.list_logs`、`stats_service` 批量取数，消除 N+1。
- `group_service` 删除重复的 `subtree_ids`，统一复用 `core.deps`（授权规则与成环检测不再各持一份）。
- `group_service.build_tree` 的 scope 过滤下推 SQL；`exam_service.list_exams` 增加 `limit` 上限。
- `mail_service`：显式 `ssl.create_default_context()`、拒绝在未加密通道发送凭据、邮件头 CRLF 过滤、模板占位符错误改为告警 + 默认文案。
- `audit_service.list_logs` 转义 LIKE 通配符（`%`/`_` 不再被当通配符）。
- `user_service.reset_password` 不再回传明文；`system_service` 校验 `smtp_host` 与邮件头字段；`MASKED_SECRET` 收敛为单一常量，避免掩码被写回库。
- `stats_service._streak` 真正使用 `since` 限定回看范围。

#### 其他

- 填空答案要求每个空位至少一个等价答案（原实现会静默产出永远判错的不可作答题目）。
- 新增 `safe_cell()` 公式转义工具，供后续「导出题目/用户」类功能防公式注入（当前导出仅为静态模板，尚不可达）。
- 删除 `excel.py` 死赋值与 `exam_service` 未使用的 `SESSION_STATUS` 常量。

#### 模型 / 迁移变更

- 新增 `scripts/migrate_2026_09_16.py`：邮箱归一（含冲突改名保留）、`users.email` 重建为 NOCASE、10 张表重建以补齐 `ON DELETE`、清理历史悬空引用，并自动备份 + `PRAGMA foreign_key_check` 自检；幂等。
- 既有库由启动脚本按文件名顺序自动执行；新库经 `init_db` 已含全部定义。
- 新增配置项 `TRAINING_TZ`（默认 `Asia/Shanghai`），用于统计日期归属。

#### 测试

- 新增 `tests/test_review_remediation.py`（18 项）：邮箱大小写登录、重复账号拦截、关闭题库统计拒绝、mock 按用户隔离、复核封顶与 objective_score 同步、四类外键级联与 SET NULL、时间戳多格式归属、时区换算、`_recover_stuck_scoring` 的作用域/脏数据/合法待复核三种保护。
- 更新 `tests/test_authz.py` 两处断言：原断言依赖 `reset_password` 回传明文，改为校验落库哈希与 `token_version`。
- 全量 **154 测试通过**；`ruff check` / `ruff format --check` / `mypy` 全部通过。

### 2026-09-17 线上问题修复：SMTP 邮件收不到 + 注册无法选分组

#### 问题 1：填入 SMTP 配置后，测试邮件与注册邮件都收不到

**根因（已复现）**：`smtp_password` 行存的是 `enc:` 密文，但 `encrypted` 标记位是 `0`。
`get_settings` 旧实现只信标记位，直接把**密文本身**当明文密码交给 `mail_service`，
SMTP 认证必然失败；而 `/system/smtp/test` 又把发送放进 `BackgroundTasks` 并立即返回
"已加入发送队列"，后台异常被吞掉，管理员看到成功提示却收不到邮件。

脏数据来源：`migrate_2026_08_28` 清理遗留 `plain:` 行时把敏感项置为 `encrypted=0`，
而旧版 `update_settings` 更新已有行时只写 `value`、不回写 `encrypted`，
管理员随后在设置页重填密码，就留下"密文 + 标记 0"的行。

| 编号 | 位置 | 修复 |
| --- | --- | --- |
| MAIL-1 | `services/system_service.update_settings` | 更新已有行时同步回写 `encrypted`（与 `category`）标记，从根上杜绝标记漂移 |
| MAIL-2 | `services/system_service.get_settings` | 新增 `_decrypt_row_value`：标记为加密**或**值以 `enc:` 开头都解密，兼容历史脏数据 |
| MAIL-3 | `scripts/migrate_2026_09_17.py`（新增） | 一次性修复：`enc:` 行置 `encrypted=1`、非密文的加密行置 0、遗留 `plain:` 行清空；自动备份、幂等 |
| MAIL-4 | `api/system.py` `/system/smtp/test` | 改为**同步发送**：只有真正投递成功才返回成功，失败返回 400 + SMTP 原始错误（认证失败/连接失败/发件人被拒） |
| MAIL-5 | `services/mail_service.py` | 新增 `MailError`；发送失败统一抛错；新增 `send_safely` 后台包装，注册验证码发送失败会写入日志而非静默消失 |
| MAIL-6 | `services/auth_service.py` | 验证码/重发邮件的后台任务改用 `send_safely`，SMTP 配置错误可被管理员从日志发现 |

运维提示：既有库启动时会被 `migrate_2026_09_17.py` 自动修复；若 `TRAINING_ENC_KEY`
与写入密码时不一致（例如容器重建后换了密钥），需在设置页重新保存 SMTP 密码。

部署脚本：`start.sh` / `start.ps1` 此前**不加载** `backend/data/.dev-secrets.env`
（只有 `start-embed.ps1` 加载），重启后 `TRAINING_ENC_KEY` 丢失会让已保存的 SMTP 密码
再次解密失败、`TRAINING_SECRET_KEY` 变化导致登录态全部失效。现改为首次运行自动生成
密钥文件、每次启动注入进程环境，与内嵌版脚本行为一致。

#### 管理后台菜单与测试数据清理（2026-09-17 后续）

- **遗留菜单项**：管理后台侧栏的「模拟考试设置」`MockConfig.vue` 与路由早已删除，但
  `AdminLayout.vue` 的桌面侧栏与手机端抽屉两处菜单未同步清理，点击会落到 404。
  已移除两处菜单项，并修正 `exam_service.py` 顶部过时的 "mock-config" 描述。
- **测试数据清理**（开发库，清理前已 `.backup` 备份）：
  删除 6 个测试用户、研发部分组、6 个测试题库及其题目、全部模拟考试定义与名为
  t/网络安全期末考/…-测试 的正式考试，以及会话/成绩/练习记录/每日统计/题目状态/
  简答复核/标签/验证码/草稿，并清掉已失效的 `settings.mock_config` 死配置；
  保留超管 `admin@example.com`、分组「省集约化平台维护网格/资源池/核心网/业务平台」、
  题库与已发布正式考试「202609 IPTV技能提升培训考试」。清理后 `PRAGMA foreign_key_check` 无异常。

#### 问题 2：分组已创建，但注册页无法选择

**根因**：注册分组是 fail-closed 白名单（`register_allowed_group_ids`，空表示不允许自选），
但管理端只能手填分组 ID，分组管理页也不显示 ID，管理员实际无法配置。

| 编号 | 位置 | 修复 |
| --- | --- | --- |
| REG-1 | `views/admin/Settings.vue` | 「允许公开注册加入的分组」改为**分组树下拉多选**（多选 + 父子独立勾选），保存为逗号分隔 ID，不再手填 |
| REG-2 | `views/admin/Groups.vue` | 表格与移动端卡片补充分组 **ID** 列，便于人工核对 |
| REG-3 | `views/auth/Register.vue` | 无可选分组时下拉禁用并提示"由管理员后续分配"，不再显示一个无解释的空选择框 |
| REG-4 | `api/auth.py` `/auth/register-groups` | 无任何可选分组时 `required` 恒为 `false`，避免"必选却无选项"的死表单；语义仍为 fail-closed |

#### 测试

- 新增 `tests/test_smtp_and_register_groups.py`（12 项）：标记不一致时仍能解密、重写敏感值回写标记、
  SMTP 测试回显真实失败/成功前确实发送、无发件人报错、后台发送包装落日志、
  注册分组白名单过滤、空白名单 fail-closed 且不产生必选死表单、迁移修复与幂等。
- 全量 **196 测试通过**；`ruff check` / `mypy` 通过；前端 `vue-tsc` + `vite build` 通过。

---

### 全量后端复审整改（2026-09-16）：Critical 修复 + CI 质量门禁 + 覆盖率

本轮对 `backend/` 做全量复审（静态逐文件阅读 + **可执行复现验证**）。3 个 Critical
均先在真实执行下复现、再修复，并补齐回归测试；同时把质量门禁接进 CI。

#### 一、Critical（阻断级）

| 编号 | 位置 | 问题与修复 |
| --- | --- | --- |
| E1 | `services/review_service.py` | **简答判「不通过」必然 500**：`delta = eqs if verdict == "pass" else partial_score` 在 `fail` 时 `delta=None`，SQL 中 `min(total_score, score + NULL)` 得 NULL，写入 NOT NULL 的 `exam_results.score` 抛 IntegrityError。修复：pass/partial/fail 三分支显式取增量（fail=0.0）。<br>**复现证据**：修复前 `NOT NULL constraint failed: exam_results.score`；修复后 fail 复核 200 且分数不变 |
| E2 | `services/question_service.py` | **删除题目会静默破坏已固化考试**：`exam_questions.question_id` 是 `ON DELETE CASCADE`，`delete_bank` 有 409 拦截而 `delete_question` 没有。修复：与 `delete_bank` 同口径——被考试引用则 409，并清理该题的练习记录/题目状态。<br>**复现证据**：修复前固化行随题目一并消失（`[1] → []`） |
| E3 | `api/system.py` + `main.py` | **Logo 存储型 XSS + 内存放大**：允许上传 `.svg`，而 `/files` 是同源公开静态读取且全站无安全响应头，浏览器直接导航该 URL 即执行内嵌脚本；上传侧先 `await file.read()` 再校验大小。修复：不再接受 `.svg`；先看声明大小再分块累计；回源显式声明媒体类型 + `nosniff`；新增全局安全响应头中间件并拦截 `/files/**/*.svg` 历史遗留文件 |

#### 二、建议项（本轮一并修复）

| 编号 | 位置 | 修复 |
| --- | --- | --- |
| S1 | `services/auth_service.py` + `api/auth.py` | `send_code` 对已注册邮箱 400、未注册 200，状态码即注册邮箱枚举 oracle（与"统一提示"注释意图相悖）。改为已注册时静默成功（不发信）；`.lower()` 统一换成 `normalize_email()` |
| S2 | `main.py` | 未匹配的 `/api/*` 返回 200 + `{"detail":"Not Found"}` → 改 404，并按路径段匹配 `api/` 前缀 |
| S3 | `core/like.py`（新增）+ `audit_service` + `question_service` | 题目搜索的 LIKE 通配符未转义（搜 `_`/`%` 可匹配全表）。抽出公共 `like_pattern()` 与审计搜索统一口径 |
| S4 | `models/exam.py` + `question_service` + `group_service` | `EXAM_STATUS` 遗漏实际写入的 `"archived"`；`QUESTION_TYPE`/`GROUP_TYPE` 在模型与服务两处重复。收敛为单一来源 |
| S5 | `api/audit.py` | 草稿 `payload`/`form_key` 无任何上限。加 64KB / 64 字符限制（413 / 422） |
| S6 | `utils/user_excel.py` | 角色/状态校验形同虚设：`_norm_role` 对未知值已兜底为 `user`，使 `role not in ROLES` 恒假，未知角色被静默降级。改为用原始值查映射表，非法值报错 |
| S7 | `services/exam_service.py` | `/mock/preview` 未传 `size` 直接 400，而 `/mock/start` 默认 30，两侧口径不一致。默认值收敛到 `build_mock_spec` |
| S8 | `core/security.py` | `ACCESS_TOKEN_EXPIRE_MINUTES` 定义后从未使用（硬编码 7 天）。改为引用配置 |

#### 三、质量门禁与测试

- 新增 `.github/workflows/backend-ci.yml`：`ruff format --check` → `ruff check` → `mypy` → `pytest --cov-fail-under=75`。
  此前 CI 只构建镜像，测试/静态检查全靠人工，E1/E2 与格式问题因此长期无人拦截。
- `pyproject.toml` dev extras 增加 `pytest-cov`，并更新 `uv.lock`。
- 修复 HEAD 上 `ruff format --check` 失败的 2 个文件（`scripts/migrate_2026_08_28.py`、`tests/test_migration_robustness.py`）。
- 新增 9 个测试文件、120 个用例：

| 测试文件 | 覆盖重点 |
| --- | --- |
| `test_review_verdicts.py` | E1 回归：pass/partial/fail 分数、满分封顶、`objective_score` 同步、重复复核、范围、公布流程 |
| `test_question_service_crud.py` | 题库/题目 CRUD、答案形状校验、白名单写入、E2 回归、LIKE 转义、数据范围 |
| `test_logo_security.py` | E3 回归：svg 拒绝与拦截、nosniff、超大上传、安全响应头、未知 api 404 |
| `test_api_admin_routes.py` | 管理端 users/questions/groups/system 的 HTTP 契约与权限守卫 |
| `test_api_user_routes.py` | 练习/草稿/榜单 + 模拟考试与正式考试（含简答 fail 复核）端到端 |
| `test_auth_service_flows.py` | 注册闭环、验证码防枚举回归、登录/改密各失败分支 |
| `test_import_and_user_excel.py` | 题库/学生导入的预览→确认、confirm_token IDOR、逐行校验 |
| `test_stats_rank.py` | 排行四维度 × 两 scope、连续天数边界、预聚合幂等、面板口径 |
| `test_group_and_practice.py` | 分组成环与删除守卫、练习判分/自评/标记/进度 |
| `test_preview_cache_and_mail.py` | 缓存容量/TTL/单次消费、SMTP SSL/STARTTLS/匿名中继/错误包装 |

- 覆盖率 **65% → 88%**（≥ 75% 门禁）；测试数 **196 → 316**。
- 主要模块覆盖率变化：`import_service` 20%→87%、`user_excel` 21%→93%、
  `question_service` 41%→92%、`group_service` 38%→99%、`preview_cache` 36%→100%、
  `mail_service` 44%→100%、`auth_service` 47%→92%、`stats_service` 58%→97%、
  `review_service` 81%→97%、`api/exams` 57%→92%。
- 全量校验：`ruff format --check` / `ruff check` / `mypy` 全清；`pytest` 316 通过。

#### 四、已知待办（复审已确认，本轮未处理）

- `services/exam_service.py` 1360 行超长文件，建议按职责拆分（session / mock / admin / result）。
- `stats_service.rank` 仍有 N+1 与全表载入（已补测试，尚未重构）。
- 考试指派给父部门不会覆盖其子分组成员（子树语义与部门管理范围不一致，需产品确认）。
- `update_exam` 在题目已固化后仍可改组卷配置（改动静默失效）；`pass_score` 可在成绩公布后被修改。
- dept_admin 可禁用/重置其子树内同级管理员的密码（含自身），需确认是否有意为之。

---

### 语义类问题落地（2026-09-16）：考试指派子树 / 已固化考试改卷 / 管理员层级

上一节列出的三项「需产品确认」语义问题已确认口径并实施。

| 编号 | 位置 | 口径与实现 |
| --- | --- | --- |
| SEM-1 | `core/deps.subtree_map`（新增）+ `exam_service` | **考试指派到父分组须覆盖子分组成员**。原实现只与用户直属分组求交集，把考试指派给「研发部」对挂在「研发部/一班」的人不生效。新增一次性构建的 `subtree_map(db)`（单条查询、迭代展开、成环安全），`_user_can_access_exam` 改为按展开后的指派集合判定，`publish_exam` 的通知收件人同一口径。只向下展开，不向上（指派给子分组不会让父分组成员可见）；空指派仍对全员开放 |
| SEM-2 | `schemas/exam.py` + `exam_service.update_exam` + `frontend/src/views/admin/Exams.vue` | **已固化考试的组卷来源变更：重新固化并作废已有作答**。服务端按值比较 `rules`/`manual_questions`/`paper_template_id` 是否真的变化；若该考试已有固化题目或作答，未带 `confirm_reset` 时返回 409（`detail.code = "exam_reset_required"` + 影响面计数），确认后按外键顺序清理简答复核 → 成绩 → 会话 → 固化题目，下次开考自动按新配置重新固化。前端捕获该 409 后弹出「作废并保存」二次确认再重试；取消则保留编辑内容。此外 `pass_score` 变更会重算已公布成绩的 `passed`（保持超时即不及格语义），且不构成来源变更、不作废作答 |
| SEM-3 | `services/user_service.py` | **仅超级管理员可操作管理员账号**。新增 `_check_manage_permission`：非超级管理员对 `dept_admin`/`super_admin` 目标执行 approve / disable / enable / reset-password / update / assign-groups 一律 403。此前同级 dept_admin（同子树内）可互相禁用、重置密码，也可把自己锁死 |

**统计一致性**：作废成绩后调用新增的 `stats_service.refresh_for_timestamps`，按被删成绩的业务日期重算
`stats_user_daily`，避免排行榜/概览在下次刷新前继续展示已作废的分数。

**测试**（新增 3 个文件、17 项）：
- `tests/test_exam_assignment_subtree.py`：父/祖父分组指派覆盖子分组（`user_groups` 与 `dept_group_id` 两条路径）、
  只向下不向上、无关分组仍 403、发布通知收件人口径、空指派全员可见。
- `tests/test_exam_frozen_reset.py`：非来源变更不重置、未确认 409 且零破坏、确认后计数与重新固化、
  简答复核一并清理、作废后当日统计重算、及格线重算但不作废作答、HTTP 层确认与审计留痕。
- `tests/test_admin_role_hierarchy.py`：同级管理员四种操作 403、自我禁用/审批管理员 403、
  仍可管理普通用户、超管可管理 dept_admin、HTTP 层守卫。

**验证**：全量 **333 测试通过**，覆盖率 **88.59%**；`ruff format --check` / `ruff check` / `mypy` 全清；
前端 `vue-tsc` 通过。

---

### 内部重构（2026-09-16）：超长服务拆分 + 排行榜 N+1 消除

纯内部重构，不改动任何对外行为与接口；以 338 项回归测试作为行为不变的保证。

| 编号 | 范围 | 重构内容 |
| --- | --- | --- |
| REF-1 | `services/exam_service.py`（1510 行 → 门面 138 行） | 按职责拆分为 `app/services/exam/` 包：`common`（时间窗/超时/可见性/摘要）、`sessions`（可用列表、开考、卷面固化、断点续答）、`scoring`（逐题乐观锁、交卷判分、成绩读取、卡死回收）、`mock`（模拟考试自助组卷）、`templates`（试卷模板）、`admin`（管理端 CRUD/范围校验/归档发布/作废重固化）。`exam_service` 保留为门面模块，显式重导出全部符号（含 `_count_attempts`、`_recover_stuck_scoring`、`MOCK_MAX_QUESTIONS` 等被 api/测试引用的私有名与常量），**调用方零改动** |
| REF-2 | `services/stats_service.py`（567 行 → 门面 58 行） | 同上拆分为 `app/services/stats/` 包：`common`（业务时区/日期换算）、`aggregate`（每日预聚合与按时间戳重算）、`panel`（用户面板/管理端概览）、`rank`（排行榜） |
| REF-3 | `stats_service.rank` | **消除 N+1 与全表载入**：原实现对每个上榜用户各做一次 `db.get(User)`、streak 维度每人再查一次、分组维度每人再查 `user_groups` 与 `groups`，即 O(用户数) 次 round-trip。改为批量 `_user_labels` / `_streaks` / `_rank_by_group`，查询数固定为 3~4 条；并加 `order_by(user_id)` 让并列名次确定（原来依赖未定义的 `group_by` 顺序）。名称/未分组/连续天数语义与原实现逐条对齐（含空名回退 email、删除分组显示「未分组」） |

拆分依据是"同一变更原因放一起"：考试域按「用户侧会话 / 判分结算 / 模拟考 / 模板 / 管理端」切分，
统计域按「换算 / 聚合 / 面板 / 排行」切分。跨模块依赖为单向 DAG
（`common ← scoring ← sessions ← mock`），无循环导入。

**测试**：
- 新增 `tests/test_exam_templates.py`（4 项）补齐拆分后最薄弱的模板模块：预览不落库、范围过滤、
  CRUD、被考试引用时 409 删除保护。
- 新增 `tests/test_stats_rank.py::test_rank_query_count_does_not_grow_with_users`：
  用 `before_cursor_execute` 事件统计 SQL 条数，12 个用户下两次 `rank` 合计 ≤ 8 条，
  作为 N+1 回退的硬性守卫。
- 全量 **338 测试通过**（重构前后断言完全未改），覆盖率 **88.59% → 89.14%**；
  `ruff format --check` / `ruff check` / `mypy` 全清。
- 所有生产文件均已 ≤ 500 行（最大 `services/exam/admin.py` 479 行，原单体 1510 行）。

---

## 前端代码审核与整改（2026-09-17）

> 范围：`frontend/src/**`（54 个 `.vue`/`.ts`，约 5.1k 行）
> 依据：frontend-code-review 检查表（代码质量 / 性能 / 业务逻辑 / 安全 / 可访问性）
> 验证方式：`npm run format:check` + `npm run lint` + `npm run typecheck` + `npm run build`
> ＋ Playwright 运行时冒烟 17 项（布局/菜单/守卫/答题页/考试中心，`/api` 打桩）

### P0（阻断级）

| 编号 | 问题 | 修复 | 验证 |
| --- | --- | --- | --- |
| FE-P0-1 | `npm run lint` 必然失败：扁平配置下仍传 `--ext`，脚本 exit 2 | `package.json` 改为 `eslint src`，并补 `format` / `format:check` / `typecheck`；`@eslint/js`、`vue-eslint-parser` 由隐式传递依赖改为显式 devDependencies；新增 `.github/workflows/frontend-ci.yml` 门禁 | `npm run lint` exit 0（0 error / 0 warning） |
| FE-P0-2 | 6 个 ESLint error；`Answer.vue` 仍用有偏洗牌 `sort(() => Math.random()-0.5)`，而 `utils/array.ts#shuffle` 0 调用 | 补齐未用变量/导入；改用 `shuffle()`；删除死代码 `useDraft.ts` / `Placeholder.vue` / `examApi.result` / `practiceApi.progress` / `QuestionType` | eslint 0 问题 |
| FE-P0-3 | `auth` store 中 `JSON.parse(localStorage)` 遇脏数据抛异常 → 整站白屏 | 抽出 `readStoredUser()`：try/catch + 清缓存回退未登录 | 手工注入非法 JSON 后可正常进入登录页 |
| FE-P0-4 | 15 处 `ElMessageBox.confirm/prompt` 未 catch，用户“取消”被记为事件处理器异常；`validate()` 未 catch 同理 | 新增 `utils/dialog.ts`（`confirmBox`/`promptBox`/`alertBox`）与 `utils/form.ts`（`validateForm`），全部调用点改造 | eslint/tsc 通过；冒烟无 console error |
| FE-P0-5 | `onMounted(load)` / 提交接口失败产生未处理 Promise 拒绝，失败被误显示为“暂无数据” | 13 处 `load()` 补 catch 并保留原列表；`Panel` 改 `Promise.allSettled` + 显式错误提示；`Answer.submit/submitDrag` 加 try/catch 与 `submitting` 防重复提交 | 冒烟页面均正常渲染 |
| FE-P0-6 | `ExamRecords` 从 `?exam=ID` 进入后筛选无法清空（query 在 `load()` 内回填） | query 仅在 `onMounted` 读取一次 | 代码路径确认 |
| FE-P0-7 | `ExamTaking` 交卷确认的“已作答 N 题”把 `null`/`''` 记为已答，与答题卡口径不一致 | 抽出 `hasAnswer()`，答题卡着色与交卷确认共用 | 代码路径确认 |
| FE-P0-8 | 超时自动交卷失败后重启计时器（`remaining` 已为 0），会变成每秒重试一次交卷 | `doSubmit` 失败时仅在 `remaining > 0` 时恢复倒计时 | 代码路径确认 |

### P1（功能与一致性）

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| FE-P1-1 | 新增的 `AdminMenu.vue` / `UserMenu.vue` 从未被引用，两个布局仍各抄一份菜单 | 布局改用公共组件（水平/抽屉共用，`mode`/`rankVisible` 等以 props 开关）；退出登录移出 `el-menu`（`router` 模式会把 index 当路由） |
| FE-P1-2 | `QUESTION_TYPES` 在 6 处硬编码 | 统一 `@/constants/question` |
| FE-P1-3 | 筛选后未回到第 1 页（Users/Audit），出现“筛选无效”空列表 | 新增 `onFilterChange()`（`page=1` + 查询），筛选控件改指向它 |
| FE-P1-4 | `useTheme` 与 `site` store 各自解析 `/system/site`，首屏重复请求 | 数据加载统一走 store（`inflight` 去重），`useTheme` 只做 store → DOM/标题 同步 |

### P2（类型、可访问性、工程化）

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| FE-P2-1 | 82 处 `no-explicit-any`；`api.get/post/...` 默认 `any`，类型在 API 边界失效 | `api` 泛型默认改 `unknown`；补齐 `ExamBrief/ExamTemplate/ExamResultRow/ReviewItem/QuestionItem/UploadPreview/RankRow/AuditLogRow/OverviewData` 等领域类型；新增 `httpStatusOf`/`errorDetailOf` 取代 `catch (err: any)` → **0 处 any** |
| FE-P2-2 | 可点击 `<div>`（选项/答题卡/拖拽项/快捷入口/模式卡/更多）无法键盘操作，全项目 0 个 `role`/`aria-label` | 改为 `<button type="button">` 或补 `role`/`tabindex`/`aria-*` 与 Enter/Space 处理；`Answer` 全局快捷键忽略按钮焦点，避免 Enter 先提交旧答案；拖拽列表 `:key` 加索引防重复项错乱 |
| FE-P2-3 | 37 个文件不符合 Prettier；空 `@media` 块；生成声明 `auto-imports.d.ts`/`components.d.ts` 未纳入 tsconfig | 全量 `prettier --write`；删除空 media query；tsconfig include `*.d.ts`（顺带修复 40 处模板级类型问题：`el-tag` type 联合、`el-option` 不接受 null、`el-menu-item` 必须有 index、`TypeQuotaEditor` 可选 prop 等） |
| FE-P2-4 | 上传仅靠 `accept` 属性，无显式校验 | Logo 校验类型与 2MB（与后端 `_LOGO_MAX_BYTES` 对齐）；xlsx 校验扩展名与非空（体积上限由后端可配置项 `upload_max_size_mb` 判定，前端不硬编码） |

**结果**：`format:check` / `lint`（0 问题）/ `typecheck` / `build` 全绿；Playwright 冒烟 17/17 通过。

---

## 前端答题链路拆分（2026-09-17 续）

> 背景：Prettier 展开长行后 `Answer.vue`(885) / `ExamTaking.vue`(767) / `Users.vue`(614) 触发
> 「生产文件 600+ 行」红线，且练习页与考试页各写了一套六题型渲染 + 答题卡，行为已经开始漂移。
> 本次按「交互表面 / 页面布局 / 页面状态」三层拆分，**不改对外行为**（提交时机、负载、
> 判分回显口径全部保持一致）。

| 编号 | 新增/调整 | 内容 |
| --- | --- | --- |
| REF-FE-1 | 新增 `components/QuestionBody.vue`（319 行） | 六题型作答区（单选/多选/判断选项、填空、简答、拖拽）从两页抽出：只渲染 + 上抛事件（`pick`/`toggle-multi`/`update-blank`/`update:shortAns`/`blur`/`drag-start`/`pick-source`/`drop`/`unassign`），不持有答案状态、不调接口；判分着色（`showResult`+`correctAnswer`）与锁定态（`locked`）由 props 控制 |
| REF-FE-2 | 新增 `components/AnswerCard.vue`（约 200 行） | 答题卡（题号网格 + 图例 + 折叠 + 底部插槽）抽出：状态配色经 `cellClass(index)` 注入（练习=对/错/待自评，考试=已答/未答/当前，后者不泄题），交卷按钮走默认插槽；宽度/吸顶/移动端排序留在各页容器（`.side`），组件只管外观 |
| REF-FE-3 | 新增 `components/QuestionResult.vue`（约 85 行） | 判分回显（结果提示 + 正确答案多态展示 + 解析 + 简答自评）抽出，三处 computed 随之迁移 |
| REF-FE-4 | 新增 `composables/useAnswerKeyboard.ts` | 练习页键盘快捷键（A–J/数字选择、Enter 提交/下一题、←→ 切题、焦点在输入框/按钮时忽略）抽出为可复用 composable |
| REF-FE-5 | 新增 `components/UserImportDialog.vue`（约 190 行）+ `constants/user.ts` | 批量导入用户三步流程（下载模板→上传预览→确认导入）连同其状态/校验抽出，父页只保留 `v-model` 与 `@imported` 刷新；角色/状态文案与配色集中到 `constants/user.ts`，消除页面与弹窗各写一份 |

**行为一致性保障**：考试页文本题仍是「失焦才保存」（`QuestionBody` 新增 `blur` 事件，
避免逐字符触发带乐观锁的写请求）；练习页提交后才着色、简答自评后解锁、
拖拽「点击放入第一个空位」等交互逐项保持。

**结果**：
- 文件行数：`Answer.vue` 885 → 498、`ExamTaking.vue` 767 → 530、`Users.vue` 614 → 452；
  全项目 **0 个文件超过 600 行**（最大 530）。
- `format:check` / `lint`（0 问题）/ `typecheck` / `build` 全绿。
- Playwright 冒烟：布局 17/17 ＋ 答题链路 20/20（含键盘选择、单选提交负载、填空数组负载、
  拖拽放置、考试页自动保存含 `version` 乐观锁、简答失焦保存、答题卡状态着色、无 console error）。

---

## 后端第三轮审查 Critical 修复（2026-09-18）

> 范围：`backend/app` 全面审查后确认的 6 个 Critical，按「①及格线 → ②统计口径 → ③公布范围 → ④倒计时 → ⑤分组树 → ⑥邮件 PII」顺序修复。
> 验证：`ruff check` / `ruff format --check` / `mypy app` 全绿；`pytest` 379 passed（新增 11 条回归）；总覆盖率 89%（CI 门禁 75%）。

| 编号 | 问题 | 修复 | 验证 |
| --- | --- | --- | --- |
| CRIT-1 | 及格判定用「原始分」比较「百分制及格线」：`Question.score` 默认 2 分/题，30 题满分仅 60，导致 10/20/30 题卷子满分也永远不及格 | 新增 `grading.is_passed(score, total_score, pass_score, overtime)` 统一百分制判定；`scoring.submit_exam`、`admin._recompute_published_pass`、`review_service.publish_results`（SQL 侧归一化）三处接入；`ExamCreateIn/ExamUpdateIn.pass_score` 加 `le=100`；`system_service` 校验 `default_pass_score ∈ [0,100]` | `test_review_critical_fixes.py::test_is_passed_uses_percentage`、`test_small_mock_paper_can_pass_with_default_pass_line`、`test_formal_exam_below_pass_line_is_failed`；`test_exam_frozen_reset` 按百分制重写 |
| CRIT-2 | 统计聚合不过滤 `published`/考试类型：待复核成绩与模拟考提前进排行榜，违反 mock-exam-redesign 规格 | `stats/aggregate.py` 两处考试聚合加 `JOIN exam_definitions` + `type='formal'` + `published=1`（条件抽为 `_countable_exam_conditions()` 防漂移）；`publish_results` 提交后重算受影响日期聚合（补上规格要求的「公布即刷统计」） | `test_review_fixes.py::test_mock_exam_result_does_not_enter_daily_stats_or_rank`、`test_unpublished_formal_result_does_not_enter_daily_stats`、`test_review_critical_fixes.py::test_refresh_daily_ignores_mock_and_unpublished_results` |
| CRIT-3 | `publish_results` 只校验考试指派分组，未按考生数据范围过滤，部门管理员可公布并邮件通知范围外考生 | 成绩查询补 `ExamResult.user_id.in_(user_ids_subquery(scope))`，与 `exam/admin.py:list_results` 口径一致 | `test_review_critical_fixes.py::test_publish_results_only_publishes_in_scope_users` |
| CRIT-4 | `remaining_sec` 仅开考写入一次，断点续考重置整场倒计时，服务端却按真实起点判超时置 0 分 | 新增 `_remaining_seconds()`：按 `started_at + duration_min`（与 `end_at` 取小）实时换算；已结束会话固定 0（列保留为脏数据兜底） | `test_review_critical_fixes.py::test_remaining_seconds_is_recomputed_from_started_at` |
| CRIT-5 | `build_tree` 在未建全的 `nodes` 上判定父节点，子节点 `sort` 小于祖先时同一分组输出两次 | 两遍构建：先存原始 `parent_id`，构建 children 后再把 scope 外/自引用节点规范化成根 | `test_review_critical_fixes.py::test_build_tree_does_not_duplicate_when_child_sorts_before_parent`、`test_group_and_practice`（作用域收窄） |
| CRIT-6 | 邮件发送失败日志与 `MailError` 内嵌 smtplib 异常原文，泄露收/发件人邮箱（PII） | 新增 `_redact()`，`send_safely` 与 `_send` 的异常包装统一脱敏邮箱片段，保留异常类型与 SMTP 错误码等诊断信息 | `test_review_critical_fixes.py::test_redact_masks_email_addresses`、`test_mail_error_and_log_do_not_leak_recipient` |

**运维注意**：及格线口径由「原始分」统一为「百分制」。历史数据中若某考试的 `pass_score` 是按原始分（等于卷面满分）录入的，需要管理员在「考试管理 → 编辑」中按百分比重新确认；当前开发库 `pass_score=80 / 满分 100` 两种口径等价，无需调整。

## 后端第三轮审查 Suggestions 整改（2026-09-18 续）

> 范围：审查报告 🟡 Suggestions 前 10 项（安全/正确性优先），按 ①rank 范围 → ②超管并发 → ③分组归属 → ④错题口径 → ⑤写路径原子性 → ⑥~⑩ 的顺序实施。
> 验证：`ruff check` / `ruff format --check` / `mypy app` 全绿；`pytest` **390 passed**（新增 11 条回归）；覆盖率 89%（CI 门禁 75%）。

| 编号 | 问题 | 修复 | 验证 |
| --- | --- | --- | --- |
| S1 | `rank()` 无调用者数据范围：部门管理员可看到其它部门用户与分组名；streak 查询可能超出 SQLite 绑定参数上限 | `rank()` 新增 `dept_scope` 参数，用户维度用 `user_ids_subquery` 过滤、分组榜限定 `allowed_group_ids`；`_streaks` 按 500 分块查询；`api/panel.py` 对 dept_admin 传入 `dept_scope_ids`（super_admin 全量，普通用户保留全局榜，代码注释说明与规格的差异） | `test_review_suggestions_batch.py::test_rank_respects_dept_scope_for_self_and_group_boards` |
| S2 | 「最后一个超管」保护是读-改-写，两个并发请求可各自认为对方可用 → 0 个可用超管、管理入口永久锁死 | 新增 `_other_active_super_admin_exists()`（EXISTS 子查询），`set_status` / `delete_user` / `update_user`（降级）改为条件 UPDATE/DELETE + `rowcount` 判定；删除失败整体回滚，保留预检用于友好报错 | `test_review_suggestions_batch.py::test_last_active_super_admin_is_protected`、`test_atomic_guard_blocks_even_when_precheck_is_stale` |
| S3 | 统计分组归属只看 `user_groups`，只有 `dept_group_id` 的用户永远显示「未分组」并从分组榜消失 | `refresh_daily` / `refresh_user_daily` 改用 `COALESCE(min(user_groups.group_id), users.dept_group_id)`（LEFT JOIN users） | `test_review_suggestions_batch.py::test_stats_group_attribution_falls_back_to_dept_group_id` |
| S4 | `wrong_count = count(*) - sum(is_correct)` 把简答未自评（NULL）全部算成错题，且与面板口径矛盾 | 改用三态 `iif` 聚合：`answer_count` 只计已判题、`correct_count`/`wrong_count` 分别计 True/False，保证 `answer_count == correct + wrong` | `test_review_suggestions_batch.py::test_wrong_count_excludes_pending_short_answers` |
| S5 | 练习记录先 commit，统计刷新失败则接口 500 但记录已落库 → 前端重试重复计分 | `answer_question` / `short_eval` 去掉提前 commit，改 `flush()` + 让 `refresh_user_daily` 的 commit 一并提交（刷新失败整体回滚）；`submit_exam` 的派生统计刷新包 try/except，避免已交卷被报 500 | `test_review_suggestions_batch.py::test_practice_answer_rolls_back_when_stats_refresh_fails` |
| S6 | `assign_groups` 先删后插且不校验分组存在/去重：非法 id 会 500 且既有分组已被清空 | 校验（存在性 400、去重、范围 403）全部前置到删除之前 | `test_review_suggestions_batch.py::test_assign_groups_rejects_unknown_group_without_wiping_existing_links` |
| S7 | `import_users` 每行 2 次 SELECT（5000 行 ≈ 1 万次查询），且批量 flush 在逐行容错之外，唯一约束竞态会整批 500 | 邮箱与分组 id 改为一次性预取；逐行插入用 SAVEPOINT 隔离，冲突只让该行失败并进入 errors；错误列表按行号排序 | `test_review_suggestions_batch.py::test_import_users_isolates_existing_email_and_ignores_unknown_group` |
| S8 | 用户 Excel 预览对每行做 bcrypt（5000 行 ≈ 26 分钟 CPU，占满线程池）；超长口令会让整份预览 400 且不指向具体行 | 按口令去重后只哈希一次（复用同一次 bcrypt）；`_parse_row` 增加 72 字节行级校验 | `test_review_suggestions_batch.py::test_user_preview_hashes_distinct_passwords_once_and_reports_long_password` |
| S9 | `_check_manage_permission` 在操作者记录缺失时 fail-open | 改为 `actor_user is None or (...)` → 403 | `test_review_suggestions_batch.py::test_manage_permission_fails_closed_when_actor_missing` |
| S10 | 用户列表关键词 LIKE 未转义（`%`/`_` 当通配符），范围过滤把用户 id 物化成 Python 集合后拼 `IN(...)` | 复用 `core/like.py` 的 `like_pattern` + `escape`；范围过滤改用 `user_ids_subquery` | `test_review_suggestions_batch.py::test_list_users_escapes_like_wildcards_and_scopes_via_subquery` |

**遗留（未在本轮处理，见审查报告"其余次级项"）**：考试列表逐场 COUNT 的 N+1、`list_results` 全表载入、`paper_service` 全表抽样、`publish_exam` 逐收件人邮件任务、考试时段字符串未校验、`type_stats` 全表扫描、应用级 logging 未配置、`stats_user_daily` 可空列唯一约束与冗余索引、Excel 实体展开（defusedxml）、导入 token 先消费后校验等。

## 后端第三轮审查遗留项清理（2026-09-18 再续）

> 范围：审查报告「其余次级项 / Nits」全量清理，按「快速修正 → 行为与完整性 → 性能」三组实施。
> 验证：`ruff check` / `ruff format --check` / `mypy app` 全绿；`pytest` **414 passed**（新增 24 条回归）；覆盖率 89.6%（CI 门禁 75%）；`migrate_2026_09_18.py --dry-run` 在开发库通过。

### 一、快速修正（正确性 / 可运维性）

| 项 | 问题 | 修复 |
| --- | --- | --- |
| 应用日志 | 全应用未配置 logging，`logger.info` 被无 handler 的 root 直接丢弃，warning/error 走 `lastResort` 无格式输出 | 新增 `app/core/logconfig.py`：`configure_logging()` 幂等安装 root stdout handler（仅在 root 无 handler 时，避免与 pytest/uvicorn 冲突），`quizhub` 级别由 `TRAINING_LOG_LEVEL` 控制（默认 INFO）；`create_app()` 调用 |
| stats 索引与唯一性 | `uq_user_daily(user_id,date,group_id)` 含可空列，NULL 互不相等 → 未分组用户无唯一保护，重复刷新会双行并被 `SUM` 重复计分；`user_id`/`date` 单列索引被其他索引前缀覆盖，属冗余写放大 | 模型加部分唯一索引 `uq_user_daily_ungrouped (user_id,date) WHERE group_id IS NULL`；去掉两处 `index=True`；新增 `scripts/migrate_2026_09_18.py`（合并存量未分组重复行、建索引、删冗余索引，支持 `--dry-run`，幂等） |
| 弃用常量 | 11 处 `HTTP_413_REQUEST_ENTITY_TOO_LARGE`（Starlette 已弃用，测试告警） | 统一为 `HTTP_413_CONTENT_TOO_LARGE`（7 处代码调用点） |
| 死代码 | `auth_service._now` / `_is_code_valid` 无调用方；`question_service.list_banks` 局部重复导入 `func` | 删除 |
| 哨兵值 | `stats/rank.since = "0000-00-00"` 非法日期仅靠字典序；`if current_user_id:` 假设 id 非 0 | 改 `_MIN_DATE = "0000-01-01"` 常量与 `is not None` |
| 时区单一来源 | `stats/common.py` 重复实现 `ZoneInfo + 回退 UTC`，与 `core/timeutil` 可能分歧 | 改为 `_TZ = business_tz()`，删除重复实现 |
| 组卷返回契约 | `max_questions` 截断后 `scores` 未裁剪，含未入选题目 | 同步重建 `scores` |
| 系统接口 | `api/system.py` 直接调用私有 `mail_service._send`；`api/users.py` import 分支冗余 | 新增公有 `send_test_mail` 语义保留调用（改用返回值校验）；合并冗余 if/else（见下条） |

### 二、行为与完整性

| 项 | 问题 | 修复 |
| --- | --- | --- |
| 清空部门归属 | `UserUpdate.dept_group_id=null` 与「未传」不可区分，管理员无法取消用户部门 | `user_service.update_user` 新增 `clear_dept_group` 参数；路由按 `model_fields_set` 判定显式 null |
| 导入 token 先消费后校验 | `do_import` / `consume_preview` 先 `take()` 再校验范围/角色，校验失败会作废上传者本人的预览 | `BoundedTTLCache.peek()` 新增；`import_service.do_import` 与 `user_excel.peek_preview` 改为「peek → 归属/范围/角色校验 → take」 |
| 跨用户 mock 开考 | `start_exam` 只对 `type=formal` 做指派校验，任何人枚举 `exam_id` 即可启动他人模拟考 | `type=mock` 且 `created_by != user.id` → 404 |
| scoring 回收绕过 | 用户侧无条件把 `scoring` 改回 `in_progress`，绕过 30 分钟超时守卫 | 交由 `_recover_stuck_scoring` 判定；未回收则 409「结算中」 |
| 考试时段校验 | `start_at/end_at` 为自由字符串，非法值或 end<=start 可落库，直到考生访问才 400 | schema 校验可解析性与成对先后；`admin._validate_exam_window` 结合库中另一侧校验（覆盖单侧更新） |
| Excel 健壮性 | `难度="inf"` 触发 `OverflowError` → 500；`分值` 负数/非有限由 Pydantic 在整文件层面拒绝且不指向行；表头从不校验（列序调换会静默错位）；邮箱仅判 `@`（CRLF 可入库） | `_to_int` 捕获 `OverflowError`；新增 `_parse_score` 行级校验；`headers_match` 校验首行（题库与用户导入共用）；邮箱拒绝 CR/LF 并改用 `normalize_email` |
| 导入截断标志 | `truncated` 恒为 False（`_full_rows` 已提前截断），超限静默丢行 | `_full_rows` 返回 `(rows, hit_limit)`；`truncated = hit_limit or total >= PARSE_ROW_MAX` |
| fail-open 默认值 | `preview/do_import/consume_preview` 的 `user_id: int = 0` + `if user_id and owner` 使漏传即关闭 IDOR 校验 | `user_id` 改为必填，归属校验改为严格相等 |

### 三、性能

| 项 | 问题 | 修复 |
| --- | --- | --- |
| 考试列表 N+1 | `_exam_brief` 逐场 `COUNT(exam_questions)`（最多 500–2000 次/请求），注释声称已消除 N+1 但实际未消除 | 新增 `_exam_question_counts()`：已固化题数一次 GROUP BY、模板一次 IN 查询；`list_exams` / `list_available` 改为「先筛可见集合，再批量算题数」 |
| `list_results` 范围过滤 | 部门管理员把整张 `exam_definitions` 载入 Python 过滤，并拼出无界 `IN (...)` | 用 JSON1 在 SQL 内判定「有指派且全部分组都在 scope 内」 |
| `type_stats` | 无标签筛选时把整表 `(id,type,tags)` 拉进 Python 计数 | 无标签时下推为 `GROUP BY type` 聚合；有标签时保留 Python 过滤（JSON 无集合包含语义） |
| `publish_exam` 通知 | 物化全部活跃用户并逐人挂一个后台任务（任务数与内存随人数线性增长） | 只取邮箱列 + 单个 `send_exam_publish_many` 任务（内部汇总失败数） |
| 组卷候选集 | 无 bank/group 筛选时把整张题库表载入内存抽样 | 新增 `_CANDIDATE_MAX = 20000` 与 `LIMIT`；超限返回明确 400（不做静默截断） |

### 有意不改（附理由）

- `stats_service.__all__` 仍导出 `_TZ`/`_streaks` 等私有名：门面模块的定位就是保持 api 层与测试的既有导入路径（`__all__` 不参与属性访问），收敛导出反而要加 `noqa` 或破坏兼容，收益为负。
- `generate_paper(scope=None)` 仍默认「不限制」：三处调用点（mock/sessions/预览）的规则在写库前已按调用者范围校验，改为必填需同步改动全部调用点与测试，风险大于收益；已用 docstring 明确 `None` 语义。

---

## 后端全面评审（/backend-code-review，2026-09-20）

> 范围：`backend/`（FastAPI + 同步 SQLAlchemy 2.0 + SQLite）
> 验证：`ruff check` + `ruff format --check` + `mypy app` 全绿；`pytest` 463 通过；覆盖率 90%（CI 门槛 75%）

### 一、Critical（已修复）

| 编号 | 问题 | 修复 | 回归测试 |
| --- | --- | --- | --- |
| CRIT-1 | `migrate_2026_09_16.migrate_email_collation` 用 `PRAGMA writable_schema` 只改表定义、不重建索引，`PRAGMA integrity_check` 报 "row N missing from index" / "non-unique entry"，邮箱唯一约束与等值查询不可靠 | 删除 `writable_schema` 路径，统一走表重建（按共有列拷贝），原样恢复迁移前索引；`main()` 增加 `integrity_check == ok` 断言（否则 exit 2） | `test_migration_robustness.py::test_email_collation_rebuild_keeps_indexes_consistent` / `..._is_idempotent` |
| CRIT-2 | WAL 模式下 `shutil.copy2` 只复制主库文件，未 checkpoint 的已提交事务不在备份中；`prune_backups` 还会把坏备份当最新保留 | 改用 SQLite 在线备份 API（`src.backup(dst)`）+ `integrity_check` 校验；校验失败丢弃备份且不触发清理；非 SQLite 源降级返回 None | `test_db_backup_wal.py`（含 WAL 未 checkpoint 数据可恢复、非 DB 源降级） |
| CRIT-3 | `stats.aggregate.refresh_daily` 读快照 → 全量 DELETE → 重建，pysqlite 下 SELECT 走自动提交，会覆盖并发 `refresh_user_daily` 已提交的当日行（丢失更新） | 会话干净时在读快照前 `BEGIN IMMEDIATE` 取写锁，使读-删-写成为串行化写事务 | `test_stats_refresh_locking.py`（写锁 + 聚合口径回环） |

### 二、Suggestions（已修复）

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| SUG-1 | `dept_scope_ids` 用 `subtree_ids` 逐节点查询（42 个调用点，大部门下每请求上百次查询） | 复用 `subtree_map`（单次查询全量映射） |
| SUG-2 | Excel 导入放行多字母单选题答案，与手动创建路径 `_validate_answer_shape` 口径不一致（存下永远判错的题） | `_parse_row` 对单选题增加 `len(ans) != 1` 行级错误 |
| SUG-3 | SMTP 未配置时 `_send` 静默返回，而 `/auth/send-code` 返回成功，新部署无法注册且无可见错误 | `ensure_configured()` + `_send` fail-closed 抛 `MailError`；`send_code`/`resend` 在写验证码前转 503 |
| SUG-4 | `rank?dimension=streak&range=all` 把全量历史 `(user_id, date)` 载入内存（单进程 DoS 面） | 新增 `_streak_candidates`：候选集收敛到「今日/昨日有作答」（连胜 > 0 的必要条件） |
| SUG-5 | `update_exam` 作废成绩 commit 后刷新统计，刷新失败返回 500（实际已生效，管理员会重试） | 派生统计失败降级为 WARNING 日志（与题目删除路径同口径） |
| SUG-6 | `import_service._ensure_unique_tags` 缺 SAVEPOINT，并发导入撞标签唯一约束导致整批 500 | 改为 `begin_nested()` + `IntegrityError` 静默跳过（与 `question_service._ensure_tags` 同口径） |
| SUG-7 | `publish_results` 逐考生挂一个后台任务，任务数随人数线性增长 | 新增 `send_review_done_many`，收敛为单个后台任务 |
| SUG-8 | `update_settings` 逐 key 点查（一次保存最多 22 次 SELECT） | 按分类一次性取回既有行（并对历史 category 脏数据按 key 兜底） |
| SUG-9 | `user_service.py`（583 行）/ `exam/admin.py`（529 行）超 500 行红线 | 实测排除导入/注释/docstring 后为 446 / 380 有效代码行，未触发规则阈值，故不做拆分（避免大面积搬移风险） |
| SUG-10 | 服务层 169 处直接 `raise fastapi.HTTPException`，领域逻辑耦合传输层 | 新增 `app/core/errors.py::DomainError`（同形状 `status_code`/`detail`），169 处全部迁移；`main.py` 注册异常处理器统一映射为 `{"detail": ...}`，API 契约不变 |

### 三、Nits（已修复）

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| NIT-1 | CORS 来源未 strip，含空格时静默失效 | 逐项 strip 并过滤空项；`allow_credentials` 改用 strip 后列表判断 |
| NIT-2 | `rank_visible` 写入大小写不敏感、读取敏感（存 "TRUE" 会被读成 false） | 写入归一为小写（`BOOL_SETTING_KEYS`），读取端统一 `.lower()` |
| NIT-3 | `upload_allowed_ext` 校验条件含恒真子句 | 简化为 `any(ext != ".xlsx" ...)` |
| NIT-4 | `_is_overtime` 对历史脏时间戳抛 400，阻断交卷 | 降级为 WARNING + 按未超时处理（与 `_remaining_seconds` 同口径） |
| NIT-5 | `send_code` docstring 承诺的「已注册」提示实际难以到达 | 更正注释，说明该提示仅在竞态下出现，属防枚举的预期代价 |
| NIT-6 | `smtp_host` 未限制目标，配合 `/system/smtp/test` 构成超管侧 SSRF 探测 | 新增 `_reject_internal_host`：拒绝回环/链路本地/组播/未指定地址（不拦私网，避免破坏内网中继） |
| NIT-7 | `user.delete` 审计明细记录邮箱，与全站 PII 脱敏口径不一致 | 改为只记 `role` 等非 PII 字段 |
| NIT-8 | `create_user` 路由在服务层提交后二次 commit 补部门归属，无审计且非原子 | 归属作为 `create_user(dept_group_id=...)` 入参与创建同事务写入 |

### 新增/调整测试

- 新增 `tests/test_db_backup_wal.py`、`tests/test_stats_refresh_locking.py`。
- `tests/test_migration_robustness.py` 增加 users NOCASE 重建的两条回归；`tests/test_excel.py` 增加单选/多选答案形状两条。
- 因行为按设计变更而调整（原测试固化了旧行为）：`test_send_returns_early_when_host_missing` → `test_send_raises_when_host_missing`；`test_mail_fallback_...` → 经 `send_safely` 验证失败路径不泄露 PII；`test_backup_database_prunes_old_auto_backups` 改用真实 SQLite 库。
- 领域错误迁移后，测试断言统一为 `pytest.raises((DomainError, HTTPException))` / `except (DomainError, HTTPException)`，兼容依赖层仍抛 `HTTPException` 的场景。

---

## 后端全面评审修复（/backend-code-review，2026-09-24）

> 范围：`backend/app` 全部模块 + `backend/scripts` 迁移 + 测试质量项
> 方式：逐条读码 + 隔离临时库实跑复现（复现脚本未入库）
> 验证：`ruff check` 全绿、`mypy app` 无问题、`pytest -q` 全通过
> 完整报告：[docs/backend_review_2026-09-24.md](backend_review_2026-09-24.md)

### 一、Critical（10 项）

| # | 问题 | 修复 |
| --- | --- | --- |
| C1 | 删除分组把「仅指派给该分组」的考试 `group_ids` 清成 `[]`，而空指派 = 全员可见 → 该考试静默对所有人开放 | `group_service._strip_group_from_assignments`：剔除后为空时保留悬空 id（fail-closed）并记 WARNING；包含关系判定改用 `json_each` 下推 SQL |
| C2 | 图形验证码的 SVG 是答案的确定性函数（用答案作伪随机种子）→ 离线枚举 10⁴ 个答案建「图像→答案」反查表即可 100% 破解 | `core/captcha.py`：干扰线/抖动/噪点改用 `random.SystemRandom()`，同一答案每次渲染不同 |
| C3 | 用户导入模板自带 `lisi@example.com / 部门管理员 / Abc@12345` 示例行且可被导入 | `utils/user_excel.py`：示例行邮箱加 `EXAMPLE_PREFIX` 前缀并在解析时跳过；示例口令留空作第二道防线；说明页补充提示 |
| C4 | 手工选题（`manual_questions`）不在删题守卫内：删题后考试永久无法开考/发布 | `question_service`：`delete_question`/`delete_bank` 增加手工选题引用统计（`json_each`）→ 409 |
| C5 | 题库导入按物理行截断、却用 `total >= PARSE_ROW_MAX` 反推截断 → 前置空行时静默丢数据且不报截断 | `utils/excel.py`：上限改按**数据行**计数并在 break 处显式返回 `truncated`；`import_service` 直接消费该标记 |
| C6 | `create_exam`/`update_exam` 不校验 `paper_template_id` / `manual_questions` 存在性 → 外键 IntegrityError → 500 / 落库不可开考的考试 | 新增 `_validate_paper_template` / `_validate_manual_questions`；`_commit_exam` 把 IntegrityError 映射为 400 |
| C7 | `create_user` / `register` 的 check-then-insert 未兜底唯一约束 → 并发同邮箱 500 | 两处 commit 包 `except IntegrityError → rollback → 400`；`register` 的邮箱重复检查提前到消费验证码之前（不再白烧验证码） |
| C8 | 已交卷待复核（scoring + 有成绩）的会话，`start_exam` 仍返回可续答卷面，但任何作答都被拒 | `exam/sessions.py`：该状态返回 409「本场考试已交卷，成绩待公布或复核」 |
| C9 | `migrate_2026_09_23_stats_exam_columns.py` 表重建无事务：中断后表缺失、数据滞留 `_old`，重跑报「无需处理」 | 重建与恢复都包显式 `BEGIN…COMMIT`；新增 `needs_recovery()` / `recover_interrupted_rebuild()` 兜底 |
| C10 | 填空题答案空位数少于题干空位数仍判合法 → 存下永远判错的题 | `utils/excel.py`：按 `_{2,}` 统计题干空位数并交叉校验，不一致则行级报错 |

### 二、Suggestions（10 项）

| # | 问题 | 修复 |
| --- | --- | --- |
| S1 | 拖拽题重复左项被静默覆盖 → 不可作答的题 | 解析期检测重复左项 → 行级报错 |
| S2 | 判断题不接受 Excel 布尔单元格与 `TRUE`/`False` | 归一化为大写并映射 bool |
| S3 | 「今日活跃」在作答路径（按 `cnt`）与刷新路径（按源行存在）口径不一致 | `refresh_user_daily` 改用当日练习**源行数**判定 |
| S4 | `create_question` 不校验答案字母是否在选项范围内（与 Excel 路径不一致） | `_validate_answer_shape(qtype, answer, options)` 增加范围校验 |
| S5 | `admin_overview` 每次请求把全部 formal 考试载入 Python | 子集判定下推 SQL（`json_each` + `NOT EXISTS`） |
| S6 | `_strip_group_from_assignments` 在写事务内全表加载两张表 | 改用 `json_each` 只取命中行 |
| S7 | `save_draft` 的每用户草稿上限是 check-then-insert | 上限判定并入 INSERT 的 SELECT（原子），`rowcount == 0` → 400 |
| S8 | `publish_exam` 无状态前置校验 → 重复点击重复群发通知 | 仅 `draft → published` 迁移时发通知 |
| S9 | mock 定义复用忽略 `show_analysis` | 复用分支同步更新该列 |
| S10 | 幂等重复交卷把「未公布」一律报成「含简答待复核」 | 按 `need_review`/`overtime` 分支，与 `get_result` 同口径 |

### 三、Nits（10 项）

| # | 问题 | 修复 |
| --- | --- | --- |
| N1 | `user_service.py` 631 行、五类职责 | 拆出 `services/user_import_service.py`（`import_users` + 预取常量），`user_service` 降至 487 行 |
| N2 | `max_questions_per_exam` 死配置仍在管理端展示 | 从 `DEFAULT_SETTINGS` / 标签表 / 校验分支移除；新增 `migrate_2026_09_24.py` 清理存量行 |
| N3 | `db_backup` 备份名与清理 glob 可能分叉 → 清理失效、备份无界增长 | 抽出 `_backup_path()` 供写入与清理共用；`_BACKUP_RE` 不再绑定 `.db` 扩展名 |
| N4 | 模板「所属分组ID」列从不解析（模板承诺不生效） | 解析该列 + 服务层校验存在性/数据范围/与题库分组一致，非法行在预览可见 |
| N5 | 部门管理员改 `rules` 时被存量 `paper_template_id` 误判 403 | 仅当 payload 真正提交该字段时才做模板检查 |
| N6 | 测试 Liar：断言不成立或从未到达目标分支 | 改写 8 处（mock 归属查询、mock 清理、最后超管守卫、重复复核、分层守卫、练习上限/题库范围、SPA fallback 404、时间口径镜像） |
| N7 | 缺失的授权负向测试 | 补 7 组：分组增删 scope、题库/题目 scope、复核列表越界、跨用户作答、部门管理员越界分配、模板路由守卫矩阵、logo 路由 |
| N8 | `conftest` 用 `setdefault` 设 `TRAINING_ENC_KEY` → 503 用例依赖环境 | 用例内 monkeypatch 该常量 |
| N9 | `migrate_2026_08_28` 重建 FK 缺 `ON DELETE CASCADE`；事务内 `PRAGMA foreign_keys=ON` 是 no-op | 补 CASCADE；改为 commit 后再重开外键 |
| N10 | 上轮报告「仅 5 项未落地」的复查判据粒度到文件，漏判 7 条 | 在 09-23 报告追加第八节更正，并改用「逐条定位到行」的判据 |

### 四、产品决策（2026-09-24 已确认，不改动）

- `GET /api/admin/exam-results?outcome=failed` **刻意包含**未公布/待复核的成绩：
  `passed` 只在公布时被置 True，因此 `passed IS FALSE` 天然包含「尚未公布」，与
  `outcome=pending` 的重叠是有意为之（管理员要一屏看到所有目前未通过的人）。
  已在 `services/exam/admin.list_results` 的 docstring 与
  `tests/test_admin_status_filters.py` 的计数断言注释中记录该决策，避免下次复查再被误报。
  若日后改为只统计已公布的不及格：`failed` 分支加 `ExamResult.published.is_(True)`，
  断言从 2 改为 1。

### 五、本轮验证

```bash
cd backend
.venv/bin/python -m ruff check app scripts tests           # All checks passed!
.venv/bin/python -m ruff format --check app scripts tests  # 134 files already formatted
.venv/bin/python -m mypy app                               # Success: no issues found in 74 source files
.venv/bin/python -m pytest -q                              # 642 passed（基线 609 passed）
```

### 六、测试改写过程中新发现并修复

| # | 问题 | 修复 |
| --- | --- | --- |
| N11 | 路径参数指定的题库不存在时返回 **400** 而非 404：`_get_allowed_bank` 统一抛 400，使 `update_bank`/`delete_bank` 中紧随其后的 `if b is None: raise 404` 永不可达 | 给 helper 增加 `missing_status`：路径参数来源传 `NOT_FOUND`，请求体引用保持 400；补 404 断言 |

### 七、迁移链集成验证（对开发库 `data/training.db` 的副本按启动顺序跑 3 轮）中发现并修复

| # | 问题 | 影响 | 修复 |
| --- | --- | --- | --- |
| M1 | `migrate_2026_09_18.py` 把 `exam_count` / `exam_score_sum` / `exam_pass_count` 硬编码进合并 UPDATE，而这三列已被 `migrate_2026_09_23_stats_exam_columns.py` 删除 | **第二次启动即迁移失败**（`no such column: exam_count`）；entrypoint 用 `set -e` → 容器起不来 | 待累加列改为按表实际 schema 取交集（新增 `_sum_columns`）；补 `test_09_18_runs_after_exam_columns_were_removed` 与 legacy schema 累加用例 |
| M2 | 09_23 重建 `stats_user_daily` 时在 DROP old 表之前创建索引；SQLite 的 `ALTER TABLE … RENAME` 会把原索引名带到 old 表上 → `index ix_stats_date_user already exists`（本轮改造引入） | 迁移中断（有事务保护，已回滚，数据不丢） | 索引创建移到 DROP old 之后（新增 `_create_indexes`）；补 `test_09_23_stats_rebuild_preserves_rows_and_indexes` 与中断恢复用例 |

验证结论：连跑 3 轮全部成功且幂等；`stats_user_daily` 考试类列移除、索引按模型重建；
`max_questions_per_exam` 遗留行清理；`PRAGMA integrity_check` = ok、`foreign_key_check` 为空、
用户/题目/考试行数不变。

---

## 后端全面评审第二轮（/backend-code-review，2026-09-24 续）

> 范围：`backend/app` 全部模块 + `backend/scripts` + `.env.example` + 前端改密衔接
> 方式：逐条读码 + 隔离临时库实跑复现（3 项 Critical 均先复现、再修复、再回归）
> 验证：`ruff check` / `ruff format --check` / `mypy app` 全绿，`pytest -q` **695 passed**（基线 642，覆盖率 94%）
> 完整报告：[docs/backend_review_2026-09-24_round2.md](backend_review_2026-09-24_round2.md)

### 一、Critical（3 项）

| # | 问题 | 复现结论 | 修复 |
| --- | --- | --- | --- |
| C1 | 组卷配置（`rules`/`config`）只校验体积、不校验结构，而展示路径对 `type_quota` 的值直接 `int()` | `POST /admin/exams` 传 `rules={"type_quota":{"单选题":"abc"}}` → **201 落库**；此后 `GET /admin/exams` 与 `GET /exams/available` **双双 500**（已发布考试改 rules + `confirm_reset=true` 时，该指派分组下所有考生都打不开考试列表），且列表本身打不开 → 无法从界面修复 | 抽出 `paper_service.validate_config`（不查库）并在 `create_exam`/`update_exam` 入口调用；`exam/common` 的题数展示改防御式取值（`_quota_total` / `_list_len`），历史脏数据也能照常列表 |
| C2 | 手工建题/改题绕过「答案形状」不变式（Excel 路径有、手工路径没有） | 6 例全部 ACCEPTED：填空答案空位数与题干不一致、单选 `options=[]`/`None`、拖拽映射与左右项分叉、多选 `"AA"`、以及**只改 options/题干时不重校答案**（答案越界仍落库） | 空位数口径收敛到 `utils/question_text.count_blanks`（两条路径共用）；`_validate_answer_shape` 增加题干/左右项参数；`update_question` 改为「题型/选项/题干/答案/左右项任一变化就按生效组合重校」 |
| C3 | `.env.example` 给出的 Fernet 密钥生成命令 `secrets.token_urlsafe(32)` 产出 43 字符无 padding，`Fernet()` 直接拒绝 | 按该命令部署后 `PUT /system/settings`（SMTP 密码）→ **400 `Fernet key must be 32 url-safe base64-encoded bytes.`**；加密设置读取静默降级为空串 → SMTP 密码永远存不进去 | 文档改为 `base64.urlsafe_b64encode(secrets.token_bytes(32))`（与 `docs/deployment.md`/`start.sh` 对齐）；`config._check_enc_key` 启动期校验并给出可复制命令；`_fernet()` 抛中文 `RuntimeError` → 路由 503 可操作提示 |

### 二、Suggestions（6 项）

| # | 问题 | 修复 |
| --- | --- | --- |
| S1 | 非 multipart 请求体无全局上限：字段级上限都在 body 完整解析之后才生效，未登录的 `/api/auth/login` 即可打内存 | 新增 `core/body_limit.py`（ASGI 中间件）：`Content-Length` 预检 + 边收边计数，超 1MB 返回 413；multipart 不受影响（其上限由 `upload_max_size_mb` + `read_limited` 决定）；注册在 CORS 内层，413 同样带跨域头 |
| S2 | 改密会把**当前会话**也踢下线（注释却写「其它会话」），前端拿到旧 token 继续用 → 「修改成功」后第一个请求 401 | 后端返回新签发 token（`ChangePasswordOut`），前端 `auth.setToken()` 就地替换；日志文案改为「此前签发的全部会话已失效并已重新签发凭据」 |
| S3 | 审计 `user.update` 的 detail 落姓名明文（PII），与 `user.create`/`user.delete` 的口径不一致 | detail 只记字段名（`{"name": "<changed>"}`），仍能看出改了哪个字段 |
| S4 | 交卷崩溃残留（`scoring` 且无成绩）时二次交卷报 400「考试已结束」，考生最长卡 30 分钟 | 该分支先按同一超时守卫回收并重跑结算（幂等）；仍在结算窗口内返回 409「试卷正在结算中，请稍后重试」 |
| S5 | 建号时任意 `IntegrityError` 都归因为「该邮箱已存在」；`dept_group_id` 未做存在性校验 | 新增 `core.errors.is_unique_violation` 区分唯一约束/外键并给出对应文案；`create_user` 补分组存在性校验（与 `update_user` 同口径） |
| S6 | 4 处零覆盖：难度配比分枝、SMTP 主机 SSRF 拒绝分支、`GET /system/settings` 掩码逻辑、`ensure_defaults` | 新增 `tests/test_review_2026_09_24_coverage.py`（16 例）：难度配比/余数补给/全池回补、`127.0.0.1`/`localhost`/`169.254.169.254`/`0.0.0.0` 拒绝而私网中继放行、掩码不回传明文且不覆盖真实密文、默认设置幂等 |

### 三、Nits（5 项）

| # | 问题 | 修复 |
| --- | --- | --- |
| N1 | 两行超过 120 字符（模板下载的 `Content-Disposition`） | 文件名抽成模块常量，xlsx 媒体类型收敛到 `utils.excel.XLSX_MEDIA_TYPE` |
| N2 | `_recover_stuck_scoring(user_id=None)` 无生产调用方（docstring 声称有启动维护路径） | 在 `lifespan` 启动维护中调用一次并记 WARNING |
| N3 | `publish_exam` 的 `first_publish = status != "published"` → 归档后重新发布会对全员重复群发通知 | 判据改为 `status == "draft"`（只有 draft→published 才通知） |
| N4 | `GET /admin/users`、`GET /admin/question-banks` 返回无约束手拼 dict | 新增 `UserListOut`/`UserListItemOut`/`QuestionBankItemOut` 并挂 `response_model` |
| N5 | `/files/*.svg` 的提前 404 未带安全响应头 | 抽出 `_apply_security_headers()`，两个返回点共用 |

### 四、本轮验证

```bash
cd backend
.venv/bin/python -m ruff check app scripts tests           # All checks passed!
.venv/bin/python -m ruff format --check app scripts tests  # 138 files already formatted
.venv/bin/python -m mypy app                               # Success: no issues found in 76 source files
.venv/bin/python -m pytest -q --cov=app                    # 695 passed，覆盖率 94%（基线 642 / 93%）

cd ../frontend
npm run format:check && npm run lint && npm run typecheck && npm run test   # 71 passed
```

关键结论的实跑复现（隔离临时库，脚本未入库）：

1. `create_exam(rules={"type_quota": {"单选题": "abc"}})` → 400「题型数量配置无效」（修复前 201 落库）；
2. 库中已存在畸形 rules 时 `list_exams` / `list_available` 均正常返回（修复前 `ValueError: invalid literal for int()` → 500）；
3. 手工建题 4 类畸形形状 → 400（修复前全部 ACCEPTED），且「只改选项」也会按新选项集重校答案；
4. 按 `.env.example` 新命令生成密钥 → `Fernet()` 接受；旧命令产物仍被拒（文档已标注不可用）；
5. 2MB JSON → 413 且带 `access-control-allow-origin`（修复前会被完整读入内存）；multipart 1.2MB 上传不被该阈值拦截；
6. 改密后旧 token → 401、响应中的新 token → 200（修复前新 token 不存在，用户被迫重新登录）。
