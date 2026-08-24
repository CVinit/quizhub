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


