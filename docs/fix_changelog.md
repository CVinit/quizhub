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
