# 培训考试平台 代码审计报告 (audit_report.md)

> 阶段：Phase 5 代码审计
> 审计日期：2026-08-21
> 审计范围：M1–M13 全部已实现代码（`backend/app/**` + `frontend/src/**`）
> 审计方法：4 路并行子代理（安全 / 功能完整性 / 代码质量 / 性能）+ 人工逐条核对源码 + 实弹漏洞验证
> 问题分级：P0（阻断，立即修复） / P1（当前迭代必须修复） / P2（可选） / P3（优化建议）

---

## 一、审计概述

| 维度 | P0 | P1 | P2 | P3 | 小计 |
| --- | --- | --- | --- | --- | --- |
| 安全 | 2 | 9 | 5 | 3 | 19 |
| 功能完整性 | 1 | 7 | 7 | 1 | 16 |
| 性能 | 1 | 7 | 4 | 6 | 18 |
| 代码质量 | 3 | 11 | 12 | 6 | 32 |
| **去重合计** | **6** | **30** | **27** | **16** | **79** |

> 注：跨维度重复问题已去重，归入主维度。代码质量维度补入本节。

**已实测确认的 P0（实弹验证）**：
1. SPA 路径遍历读取 `/etc/passwd` —— 成功返回文件内容（HTTP 200，2890 字节）。
2. 默认 JWT 密钥伪造超管 token —— 用 `dev-secret-please-change-in-production` 伪造 `sub=1` token，`/api/auth/me` 返回"超级管理员"，`/admin/panel/overview` 返回 200。
3. 考试乐观锁非原子 —— 源码确认 SELECT-then-check-then-UPDATE。
4. 顺序/随机练习全量加载题库 —— 源码确认 `select(Question)` 无 limit。
5. system.py `audit_log` 未导入 NameError —— 实测 `PUT /api/system/settings` 返回 HTTP 500。
6. score 等列 `Mapped[float]` + `Integer` —— 源码确认 9 处，前端 `Questions.vue` 支持 0.5 分但被截断。

---

## 二、P0 问题（阻断，立即修复）

### SEC-P0-1　SPA fallback 路径遍历 → 任意文件读取
- **文件**：`backend/app/main.py:72-79`
- **类别**：安全 / 路径遍历
- **描述**：`/{full_path:path}` 路由对 `full_path` 未过滤 `..`，未校验解析后路径是否在 `dist` 目录内。Starlette `path` 转换器接受 URL 解码后的 `../` 序列。
- **攻击场景**：`GET /%2e%2e/%2e%2e/.../%2e%2e/etc/passwd` → 服务器解码为 `../../../../etc/passwd`，`dist/full_path` 解析到 `/etc/passwd`，`is_file()` 为真，`FileResponse` 返回文件内容。任何运行账户可读文件均可泄露（数据库、密钥、配置）。
- **实测**：`curl --path-as-is "http://localhost:8000/%2e%2e/.../%2e%2e/etc/passwd"` 返回 `root:x:0:0:root...`，HTTP 200。
- **修复**：服务前 `target.resolve()` 校验是否在 `dist.resolve()` 目录内；拒绝含 `..` 的路径。

### SEC-P0-2　默认 JWT 密钥允许 token 伪造
- **文件**：`backend/app/config.py:17`
- **类别**：安全 / 加密
- **描述**：`SECRET_KEY` 默认 `"dev-secret-please-change-in-production"`，该默认值已提交仓库。运维若未设置 `TRAINING_SECRET_KEY` 环境变量，任何知晓此值者即可为任意用户（含 super_admin）伪造合法 HS256 JWT。
- **攻击场景**：攻击者用默认密钥签发 `{"sub":"1","exp":<future>}` → 以超级管理员身份完全接管系统。
- **实测**：用默认密钥伪造 sub=1 token，`/api/auth/me` 返回 `{"role":"super_admin",...}`，`/admin/panel/overview` 返回 200。
- **修复**：环境变量缺失或为默认值时拒绝启动；启动断言打印配置校验日志。

### FUNC-P0-1　考试乐观锁非原子，并发作答会丢失答案
- **文件**：`backend/app/services/exam_service.py:198-213`
- **类别**：功能完整性 / 并发正确性
- **描述**：`submit_answer` 执行 `sess=db.get(...)` → `if sess.version != version: raise 409` → `sess.version=version+1; commit`。这是 SELECT-then-check-then-UPDATE，**非**架构要求的原子 `UPDATE ... WHERE id=? AND version=?`。两个并发请求都读到 version=1、都通过检查、都写 version=2 → 后写覆盖前写，version 不递增，前者答案静默丢失。
- **失败场景**：用户开两个标签页同时对 Q5 作答，均以 version=1 提交，两请求都通过检查，只保留后到的答案，version 停在 2 而非 3。
- **修复**：改为原子条件更新 `UPDATE exam_sessions SET answers=:ans, version=version+1 WHERE id=:id AND version=:ver`，检查 `rowcount`；为 0 则返回 409。或用 SQLAlchemy `version_id_col`。

### PERF-P0-1　顺序/随机练习一次性加载全题库到内存
- **文件**：`backend/app/services/practice_service.py:81-85`；联动 `frontend/src/views/user/Answer.vue:8-14`
- **类别**：性能 / 内存
- **描述**：sequence 与 random 模式 `select(Question).order_by(Question.id)` 无 limit，把全部 ~100k 行（含 options/answer/analysis/tags 等 JSON 大列）一次性载入 Python 再 shuffle；前端 `v-for` 渲染全部题目无虚拟化。
- **影响**：100k 题 × ~1KB ≈ 100MB+/请求；多用户并发直接 OOM；前端 100k DOM 节点卡死浏览器。
- **修复**：sequence 改游标分页（id > last_id LIMIT N）；random 改 `ORDER BY RANDOM() LIMIT :n`；只投影必要列；前端导航网格用虚拟滚动。

### QUAL-P0-1　system.py audit_log 未导入 → PUT /system/settings 运行时 500
- **文件**：`backend/app/api/system.py:95`
- **类别**：代码质量 / 运行时崩溃
- **描述**：`update_settings` 路由调用 `audit_log(...)`，但该模块从未导入 `audit_log`（其他 api 模块都显式 `from app.services.audit_service import log as audit_log`）。调用 `PUT /system/settings` 即抛 NameError → 500。
- **实测**：`curl -X PUT /api/system/settings ...` 返回 HTTP 500。
- **修复**：补 `from app.services.audit_service import log as audit_log`。

### QUAL-P0-2　submit_exam 非幂等 → 重复提交创建重复 ExamResult + 评分膨胀
- **文件**：`backend/app/services/exam_service.py:217-279`
- **类别**：代码质量 / 数据完整性
- **描述**：`submit_exam` 仅检查 `status != in_progress`，随后在两次独立提交中创建新 `ExamResult`（line 257）和新 `ShortAnswerReview`（line 269）。同一会话二次提交（双击/网络重试）会在第一次 commit 设 status=scored 前通过检查，追加第二条 ExamResult + 重复复核记录；`review` 会 `result.score += eqs` 导致分数重复计算。`exam_results.exam_session_id` 无唯一约束。
- **修复**：事务内原子设 `sess.status="scoring"`（`with_for_update`）作提交锁，或在 `exam_sessions.id` 上加唯一约束/upsert，或先查存在结果提前返回。

### QUAL-P0-3　score/pass_score 列 Integer 但 Mapped[float] → 小数分值被截断
- **文件**：`backend/app/models/record.py:73,74,78` + `question.py:49` + `exam.py:43,60` + `stats.py:28,44,45`
- **类别**：代码质量 / 评分正确性
- **描述**：9 处 `Mapped[float] = mapped_column(Integer, ...)`。前端 `Questions.vue` 允许 0.5 分（`:step="0.5"`），`ReviewIn.partial_score` 是 `Optional[float]`。`review_service` `result.score += partial_score`（如 +1.5）时 SQLite 截断为整数，半分静默丢失。
- **修复**：改 `Float`（或 `Numeric(8,2)`）。

---

## 三、P1 问题（当前迭代必须修复）

### 安全 P1

**SEC-P1-1　dept_admin 可提权至 super_admin**
- `backend/app/services/user_service.py:77-90` + `api/users.py:33-43`
- `PUT /admin/users/{id}` 仅 `require_admin`（含 dept_admin），接受原始 dict 的 `role` 并允许设为 `super_admin`。部门管理员可把任意账户提权为超管。
- 修复：仅 super_admin 可改 role；服务端校验调用者权限严格高于被改者。

**SEC-P1-2　dept_admin 数据范围过滤从未生效**（与 FUNC 重叠）
- `backend/app/core/deps.py:47-61`
- `group_subtree_ids` 已定义但全项目零调用。`list_users/questions/exams/results/pending/audit-logs` 均不按 `dept_group_id` 子树过滤，dept_admin 可见/可改所有部门数据。
- 修复：对 dept_admin 在各 list 查询应用 `group_subtree_ids(db, user.dept_group_id)` 过滤。

**SEC-P1-3　考试无服务端时间限制**
- `backend/app/services/exam_service.py:217-280`
- `submit_exam` 仅检查 `status != in_progress`，从不校验 `now > e.end_at`、`remaining_sec <= 0`、`started_at + duration`。倒计时纯客户端。用户可在交卷截止后继续答题。
- 修复：`submit_answer`/`submit_exam` 中计算 `elapsed`，超时则自动交卷/拒绝。

**SEC-P1-4　start_exam 绕过分组指派与时间窗**
- `backend/app/services/exam_service.py:98-131`
- `list_available` 按 `group_ids ∩ user_groups` 和 `start_at/end_at` 过滤，但 `start_exam` 仅检查 `status`。任何登录用户猜枚举 `exam_id` 即可开考任意已发布考试。
- 修复：把 list_available 的分组/时间校验抽成共享函数，在 start_exam 复用。

**SEC-P1-5　SMTP 密码在设置 API 以明文返回**
- `backend/app/api/system.py:78-89` + `services/system_service.py:63-70`
- `GET /system/settings?category=smtp` 调 `get_settings` → `decrypt_value`，SMTP 密码以明文返回 JSON。配合 SEC-P0-2 可被窃取。
- 修复：加密字段在列表 API 仅返回占位符（如 `"******"`），绝不回传解密值。

**SEC-P1-6　Fernet 密钥回退 base64（可逆"加密"）**
- `backend/app/core/security.py:53-58` + `config.py:22`
- 未设 `TRAINING_ENC_KEY` 时 `encrypt_value` 回退 `"plain:"+base64(value)`，却以 `encrypted=True` 入库。拿到 DB 文件者（含 SEC-P0-1 路径遍历）可直接 base64 解码 SMTP 密码。
- 修复：未设 ENC_KEY 时拒绝启动；移除 base64 回退。

**SEC-P1-7　无登录速率限制 / 暴力破解防护**
- `backend/app/services/auth_service.py:91-102`
- login 无尝试计数/锁定/限流；密码最短 6 位。配合默认凭据可凭据填充。
- 修复：按 IP/邮箱做尝试计数 + 指数退避锁定（slowapi 等）。

**SEC-P1-8　邮箱验证码可暴力破解（6 位数字无限制）**
- `backend/app/services/auth_service.py:51-72`
- 6 位数字（100 万组合）、10 分钟有效、`/api/auth/verify` 无尝试计数/限流，失败无副作用。
- 修复：限制尝试次数（3 次失败即失效）+ 端点限流。

**SEC-P1-9　重置密码恒用 "123456" 并明文返回响应**
- `backend/app/api/users.py:61-68,82-83` + `services/user_service.py:67`
- `payload_password()` 恒返回 "123456"，忽略管理员输入，且在 JSON 返回新密码。重置后用户未改密前可被任何知邮箱者用 "123456" 登录。
- 修复：生成随机强密码或一次性令牌；强制下次登录改密；不在响应返回明文密码。

**SEC-P1-10　confirm_token IDOR（未绑定用户、无 TTL）**
- `backend/app/services/import_service.py:21,31,61-66`
- `_preview_cache` 以 token 为键，未绑定 user_id，无 TTL。任何拿到 token 者（日志/Referer 泄露）可用他人 token 导入他人行。
- 修复：token 绑定 user_id，do_import 校验调用者；加 TTL；迁移到 DB/缓存。

**SEC-P1-11　文件上传未强制 upload_max_size_mb 大小限制**
- `backend/app/api/questions.py:84-93`
- `content = await file.read()` 读取前不校验大小，不查 `upload_max_size_mb` 设置。可上传超大文件致内存耗尽 DoS。
- 修复：读取前校验 `file.size` 与设置上限；超大拒绝；流式处理。

**SEC-P1-12　Excel 解析无行数上限 / zip 炸弹防护 + 未用 read_only**
- `backend/app/utils/excel.py:120-146` + `import_service.py:42-58`
- `load_workbook` 默认全量载入（无 `read_only=True`），`iter_rows` 无 max_row 上限，`_full_rows` 二次解析。恶意 xlsx 可致 OOM/挂起。
- 修复：`read_only=True`；迭代前校验 `ws.max_row <= N`；校验解压大小。

**SEC-P1-13　update_exam 用原始 dict + setattr 批量赋值**
- `backend/app/api/exams.py:86-90` + `services/exam_service.py:465-476`
- `payload: dict` 无 Pydantic schema，`manual_questions/rules/paper_template_id` 任意类型注入。
- 修复：改用带类型字段与校验器的 Pydantic schema，拒绝未知字段。

### 功能完整性 P1（安全维度未覆盖）

**FUNC-P1-1　difficulty_dist 难度配比被完全忽略**
- `backend/app/services/paper_service.py:26-84`
- config 文档声明 `"difficulty_dist":{"1":0.3,...}`，但 `generate_paper` 从不读取该字段，仅按 type_quota 随机抽，难度分布未生效。可能生成 100% 困难题。
- 修复：按 (type, difficulty) 分层抽样，按 difficulty_dist 在题型内分配配额。

**FUNC-P1-2　考试选项 shuffle_map 从未生成（违反"打乱后判分"规则）**
- `backend/app/services/exam_service.py:171` + `paper_service.py`
- `ExamQuestion.shuffle_map` 已定义但 `_persist_exam_questions` 恒存 None，`_session_payload` 不返回打乱映射。考试选项从不打乱，两用户看到相同顺序。
- 修复：固化时生成每题 shuffle_map（old→new），持久化并在 payload 返回打乱后选项；判分时反向映射还原再比对。

**FUNC-P1-3　publish_exam / publish_results 不发送邮件通知**
- `backend/app/services/exam_service.py:479-486` + `services/review_service.py:74-105`
- `mail_service.send_exam_publish`/`send_review_done` 已实现但从未被调用，无 BackgroundTasks 注入。
- 修复：注入 BackgroundTasks，向指派用户/会话用户队列发送通知。

**FUNC-P1-4　模拟考试每次开考创建可见 ExamDefinition，污染所有用户考试列表**
- `backend/app/services/exam_service.py:349-371, 32-66`
- `start_mock_exam` 每次创建 `type=mock, status=ongoing, group_ids=None` 的 ExamDefinition；`list_available` 不过滤 mock 且 group_ids=None 无限制 → 用户 A 的模拟考出现在用户 B 列表；表无限堆积。
- 修复：list_available 按 `type=="formal"` 过滤（mock 仅经 `/exams/mock/start` 入口）；或将 mock 会话与 ExamDefinition 解耦（存规则快照）。

**FUNC-P1-5　统计时区错配：UTC 存储 +8 日期前缀 LIKE 匹配**
- `backend/app/services/stats_service.py:26-28, 34-43, 46-53`
- `_date_str` 转 +8 日期，但 `answered_at`/`created_at` 存 UTC ISO。`WHERE answered_at LIKE '{+8日期}%'` 错配，本地 16:00–23:59（UTC 次日 00:00–07:59）的记录被错误归属前一日。日统计/面板/排行全错。
- 修复：存储本地日期列，或 SQL 端归一化为本地日期后再聚合。

**FUNC-P1-6　show_score_immediately=False 且无简答时成绩永远不可见**
- `backend/app/services/exam_service.py:262`
- `published=(not need_review and e.show_score_immediately)`。无简答 + show_score_immediately=False → published=False，但无后续 publish 路径，成绩永久未发布。
- 修复：show_score_immediately=False 视为"等管理员公布"，扩展 publish_results 处理非复核场景；或无复核时始终发布。

### 性能 P1（安全/功能维度未覆盖）

**PERF-P1-1　generate_paper 全量载入候选题再 Python 端抽题**
- `backend/app/services/paper_service.py:43-58`
- 加载所有匹配 group_ids 的题（含全部 JSON 列），Python 端分组、`rng.shuffle`、tags 用 `any()` 全量过滤。
- 修复：按 type 分别 `ORDER BY RANDOM() LIMIT quota`（每题型一条 SQL）；tags 用 `json_each` 或拆 question_tags 关联表；只投影 id/score。

**PERF-P1-2　list_modes / get_progress 用 len(全表 id 列表) 代替 COUNT**
- `backend/app/services/practice_service.py:25,132`
- `select(Question.id)).all()` 拉 100k 行 id 再 `len()`；type_dist 拉 100k 行 Python 分桶。
- 修复：`func.count(Question.id)`；`SELECT type, COUNT(*) GROUP BY type`。

**PERF-P1-3　user_panel 全量载入该用户 QuestionState 后 Python 计数**
- `backend/app/services/stats_service.py:109-116`
- 拉该用户全部状态行（最多 100k）再 `sum(1 for s in ...)` 循环计数。
- 修复：一条聚合 `SELECT SUM(status!='unanswered'), SUM(status IN(...)), ...`；优先读 stats_user_daily 预聚合 + 今日增量。

**PERF-P1-4　refresh_daily 对 answered_at/created_at 做 LIKE 全表扫 + N+1**
- `backend/app/services/stats_service.py:34-74`
- `WHERE answered_at LIKE '{date}%'` 在无索引 VARCHAR 列全表扫；refresh_recent(30)=60 次全扫；且 `for uid: db.execute(UserGroup...)` N+1。
- 修复：给 practice_records.answered_at、exam_results.created_at 加索引或冗余日期列建索引；N+1 改一次 `GROUP BY`；改增量 upsert 当日统计。

**PERF-P1-5　submit_exam 逐题 db.get + 3 次 commit**
- `backend/app/services/exam_service.py:240,265,274,279`
- `for eq: db.get(Question, ...)` N+1（≤100 题/卷）；3 次 commit（ExamResult / 简答记录 / 会话状态），SQLite WAL 每次 commit 抢 writer 锁 + fsync。
- 修复：一次 `WHERE id IN (...)` 批量取题构造 dict；三次 commit 合一。

**PERF-P1-6　_session_payload 逐题 db.get(Question)（开考/续考）**
- `backend/app/services/exam_service.py:181`
- 开考、断点续考、刷新会话都走 N+1。
- 修复：一次 `WHERE id IN (:qids)` 构造 {id: row}。

**PERF-P1-7　list_results 逐条 db.get 三个对象 + 无分页**
- `backend/app/services/exam_service.py:422-443`
- 每行 3 次 db.get（ExamDefinition/User/ExamSession），stmt 无 limit/offset。
- 修复：JOIN 一次查完；路由加分页（page/page_size）。

---

## 四、P2 问题（可选）

| 编号 | 问题 | 文件 |
| --- | --- | --- |
| FUNC-P2-1 | score 等列标 `Mapped[float]` 却用 `Integer` → 小数分值被截断（9 处） | question/record/stats/exam 模型 |
| FUNC-P2-2 | `error-report/{jobId}` 下载路由缺失 | api/questions.py |
| FUNC-P2-3 | publish_results 把 in_progress 会话也标为 reviewed | review_service.py:98-104 |
| FUNC-P2-4 | `_streak` 今日未答即返回 0（抹杀有效连胜） | stats_service.py:270-288 |
| FUNC-P2-5 | 无每日定时刷新任务（仅启动触发） | main.py:46-56 |
| FUNC-P2-6 | 审计日志忽略 from/to 时间范围、keyword 未应用 | api/audit.py / audit_service.py |
| FUNC-P2-7 | 顺序练习无法恢复上次进度（总是第 1 题） | Answer.vue:170 + practice_service |
| SEC-P2-1 | partial_score 无边界（可加任意分） | schemas/exam.py:39 |
| SEC-P2-2 | pass_score / score 可为负（无 ge=0） | schemas/exam.py:25 / question.py |
| SEC-P2-3 | PracticeStartIn.limit 无上限（内存 DoS） | schemas/record.py:9 |
| SEC-P2-4 | 审计日志从不记录 IP | audit_service.py:20 |
| SEC-P2-5 | SMTP 测试端点 to_email 无 EmailStr 校验（头部注入） | schemas/system.py:13 |
| PERF-P2-1 | do_import 逐题 db.flush（未批量插入） | import_service.py:76-90 |
| PERF-P2-2 | audit_service.log 每次额外 commit（writer 锁翻倍） | audit_service.py:33 |
| PERF-P2-3 | 邮件 BackgroundTasks 同步阻塞 worker（15s SMTP 超时） | mail_service.py:58 |
| PERF-P2-4 | list_pending 逐条 db.get 四对象（N+1）无分页 | review_service.py:14-35 |
| PERF-P2-5 | ExamRecords/Wrong/Marks 前端内存 filter 全量（无后端分页） | 三个 .vue |

---

## 五、P3 问题（优化建议）

| 编号 | 问题 | 文件 |
| --- | --- | --- |
| FUNC-P3-1 | change_password 不使旧 JWT 失效（7 天内仍有效） | auth_service.py:105 |
| FUNC-P3-2 | `_preview_cache` 无 TTL（内存泄漏） | import_service.py:21 |
| SEC-P3-1 | 重发验证码时旧码未失效（扩大暴力窗口） | auth_service.py:75-88 |
| SEC-P3-2 | 登录错误消息泄露账户状态（枚举） | auth_service.py:94-100 |
| SEC-P3-3 | 审计日志无哈希链（可被 DB 直改篡改） | models/system.py:21 |
| SEC-P3-4 | publish_results 与 review 存在交错竞争（无行锁） | review_service.py:38 |
| PERF-P3-1 | questions.difficulty 无索引但被过滤 | models/question.py:47 |
| PERF-P3-2 | exam_sessions.status / exam_results.exam_session_id 无索引 | models/record.py:55,70 |
| PERF-P3-3 | question_states (user_id,question_id) 复合唯一约束 + user_id 单列索引重复 | models/record.py:38-41 |
| PERF-P3-4 | audit_logs action LIKE '%...%' 无法走索引 | audit_service.py:45 |

---

## 六、清洁领域（已确认安全）

| 维度 | 结论 | 检查范围 |
| --- | --- | --- |
| SQL 注入 | **清洁**。全部使用 SQLAlchemy ORM + 参数化；`like(f"%{kw}%")` 为参数化 LIKE 非字符串插值；唯一原生 `.execute()` 为 `database.py:22-23` 静态 PRAGMA | services/*, api/*, core/deps.py, utils/excel.py |
| XSS | **清洁**。前端无 `v-html`/`innerHTML`/`dangerouslySetInnerHTML`；题干/选项/解析均经 Vue `{{ }}` 转义渲染 | frontend/src/** |
| CSRF | **基本清洁**。认证用 `Authorization: Bearer`（localStorage），非 cookie，无 `set_cookie`，缓解经典 CSRF（但 SEC-P0 CORS 配置需修正） | 全栈 |

---

## 七、修复计划

### 本轮立即修复（P0 全部 + 高影响 P1）
- **P0**：SEC-P0-1（路径遍历）、SEC-P0-2（密钥启动断言）、FUNC-P0-1（原子乐观锁）、PERF-P0-1（练习分页）
- **P1 高影响**：SEC-P1-1/2（提权+数据范围）、SEC-P1-3/4（考试时间/分组）、SEC-P1-5/6（SMTP 明文+密钥回退）、SEC-P1-9（重置密码）、FUNC-P1-4（mock 污染）、FUNC-P1-6（成绩永久未发布）、PERF-P1-1/2/5（组卷/COUNT/交卷 N+1）

### 视情况修复（其余 P1 + P2）
- FUNC-P1-1（difficulty_dist）、FUNC-P1-2（shuffle_map）、FUNC-P1-3（邮件通知）、FUNC-P1-5（时区）
- PERF-P1-3/4/6/7、SEC-P1-7/8/10/11/12/13、各 P2 项

修复记录见 `docs/fix_changelog.md`。
