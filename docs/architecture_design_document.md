# 培训考试平台 — 架构设计文档

> 状态：已确认（Phase 3 产出）
> 依据：`docs/requirement.md`、`docs/ui_ux_specifications.md`
> 规范：PEP 8 + Google Python Style Guide；ESLint 严格 + Prettier；阿里巴巴 Java 手册（建表命名借鉴）

---

## 1. 技术栈与约束

| 层 | 选型 | 说明 |
|---|---|---|
| 后端框架 | FastAPI | 异步，自带 OpenAPI 文档 |
| ORM | SQLAlchemy 2.0（声明式） | 自动建表/迁移，参数化防注入 |
| 数据库 | SQLite3 + WAL 模式 | 单文件 `data/training.db`，≤100 用户、≤10 万题 |
| 包管理 | uv（强制 `.venv`） | |
| 认证 | JWT（HS256） | 邮箱+密码，bcrypt 哈希 |
| 后台任务 | FastAPI BackgroundTasks | 邮件/通知/排行预聚合，不引入 MQ |
| 导入解析 | openpyxl | Excel 模板解析 |
| 前端框架 | Vue 3 Composition API + TS 5.x | |
| UI | Element Plus | |
| 构建 | Vite（pnpm） | 产物由后端静态托管 |

**架构决策**：
1. ORM 用 SQLAlchemy 2.0（非原生 sqlite3）——安全与类型友好。
2. 异步任务用 BackgroundTasks，不引入 Celery/RabbitMQ——100 人规模轻量化。
3. SQLite 开 WAL + `exam_session` 带 `version` 乐观锁——逐题落库并发安全。
4. 排行用预聚合表 `stats_user_daily`/`stats_group_daily`，每日刷新 + 管理端手动触发。
5. 文件存 `backend/data/files/`，DB 记元数据；题目图片走**外部 URL**，系统内不上传。

## 2. 系统分层

```
┌─────────────────────────────────────────────┐
│  前端 SPA (Vue3 + Element Plus)             │
│  路由: 用户端 / 管理端(角色分流)              │
└───────────────────┬─────────────────────────┘
                    │ HTTPS/JSON (JWT)
┌───────────────────┴─────────────────────────┐
│  FastAPI Application                         │
│  api/ (routers) → services/ (业务) → models/ │
│  core/ (安全/JWT/配置)  utils/ (Excel/判分)    │
└───────────────────┬─────────────────────────┘
                    │
┌───────────────────┴─────────────────────────┐
│  SQLite3 (WAL) + 文件存储 data/files/         │
└─────────────────────────────────────────────┘
```

## 3. 目录结构

```
training-platform/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口，挂载静态、CORS、路由、启动建表
│   │   ├── config.py            # 系统设置读取（从 DB settings 表）
│   │   ├── database.py          # SQLAlchemy engine/session，WAL 初始化
│   │   ├── models/              # ORM 模型（一文件一域）
│   │   │   ├── user.py  group.py  question.py  exam.py
│   │   │   ├── record.py  stats.py  system.py  audit.py
│   │   ├── schemas/             # Pydantic 请求/响应
│   │   ├── api/                 # routers
│   │   │   ├── auth.py  users.py  groups.py  questions.py  upload.py
│   │   │   ├── exams.py  records.py  rank.py  admin.py  system.py
│   │   ├── services/            # 业务逻辑
│   │   │   ├── auth_service.py  exam_service.py  grading.py
│   │   │   ├── paper_service.py # 规则组卷
│   │   │   ├── import_service.py# Excel 导入/校验
│   │   │   ├── mail_service.py  # SMTP + BackgroundTasks
│   │   │   ├── stats_service.py # 预聚合刷新
│   │   │   └── audit_service.py # 审计日志
│   │   ├── core/                # security.py(JWT/bcrypt/验证码) deps.py(权限)
│   │   └── utils/               # excel.py, time.py, pagination.py
│   ├── data/
│   │   ├── training.db
│   │   └── files/               # 上传 Excel/错误清单等
│   ├── scripts/
│   │   ├── init_db.py           # 建表 + 默认设置 + 超管账号
│   │   └── refresh_stats.py     # 手动刷预聚合
│   ├── tests/
│   ├── pyproject.toml           # uv
│   └── .venv/
├── frontend/
│   ├── src/
│   │   ├── main.ts  App.vue  router/  (角色路由分流)
│   │   ├── stores/  (Pinia)
│   │   ├── api/    (axios 封装)
│   │   ├── layouts/(UserLayout, AdminLayout)
│   │   ├── views/
│   │   │   ├── user/  (panel, answer, exam, wrong, marks, rank, profile)
│   │   │   └── admin/(overview, users, groups, questions, upload,
│   │   │             examTemplates, mockConfig, exams, examRecords,
│   │   │             review, settings, smtp, register, audit)
│   │   └── components/ (QuestionCard, NavGrid, Countdown, RadarChart, RankTable)
│   ├── dist/                   # 构建产物（后端托管）
│   ├── vite.config.ts
│   └── package.json
├── docs/                       # 各阶段文档
└── start.sh                    # 一键启动
```

## 4. 数据模型（ER 概要）

> 命名：snake_case；时间统一 TEXT(ISO8601)；JSON 列用 TEXT 存 JSON 串。

### 4.1 用户与组织
- **users**(id, email UK, password_hash, name, role ENUM('user','dept_admin','super_admin'), status ENUM('active','pending','disabled'), email_verified BOOL, created_at)
- **groups**(id, name, type ENUM('部门','专业','班级','自定义'), parent_id→groups, sort, created_at)
- **user_groups**(user_id→users, group_id→groups, PK(user_id,group_id))
- **email_verifications**(id, email, code, expire_at, used BOOL)

### 4.2 题库
- **question_banks**(id, name, group_id→groups, created_at) —— 题库来源(bank)维度
- **questions**(id, bank_id→question_banks, type ENUM('单选题','多选题','判断题','填空题','简答题','拖拽题'), question TEXT, options JSON, answer JSON, analysis TEXT, difficulty INT(1-3), tags JSON, score REAL, group_id→groups, created_at, updated_at)
  - options：单选/多选/判断 → string[]；填空/简答 → []；拖拽 → 用 leftItems/rightItems JSON
  - answer：单选“A”；多选“ABC”；判断“正确/错误”；填空“a|b”、每空多等价“a1/a2|b”；简答长文本；拖拽 `{left:right}` 映射
- **question_tags**(id, name UK)

### 4.3 考试与试卷
- **paper_templates**(id, name, mode ENUM('mock','formal'), config JSON, group_ids JSON, created_by→users, created_at)
- **exam_definitions**(id, name, type ENUM('mock','formal'), paper_template_id, manual_questions JSON, rules JSON, group_ids JSON, start_at, end_at, duration_min, pass_score, max_attempts, show_score_immediately BOOL, show_analysis BOOL, need_review BOOL, status ENUM('draft','published','ongoing','ended','reviewing'), created_by→users, created_at)
- **exam_questions**(exam_definition_id, question_id, seq, score, shuffle_map JSON)

### 4.4 作答与进度
- **practice_records**(id, user_id, question_id, bank_id, mode ENUM('sequence','random','type','wrong','mark'), user_answer JSON, is_correct BOOL|null, self_eval BOOL|null, answered_at)
- **question_states**(id, user_id, question_id, status ENUM('unanswered','correct','wrong','mastered'), marked BOOL, marked_note, answered_at, PK(user_id,question_id))
- **exam_sessions**(id, exam_definition_id, user_id, status ENUM('in_progress','submitted','scoring','scored','reviewed'), answers JSON, version INT, started_at, submitted_at, remaining_sec) —— 乐观锁 version
- **exam_results**(id, exam_definition_id, user_id, exam_session_id, score REAL, total_score REAL, passed BOOL, correct_count, total_count, objective_score REAL, need_review BOOL, published BOOL, created_at)

### 4.5 简答复核
- **short_answer_reviews**(id, exam_result_id, exam_session_id, user_id, question_id, user_answer TEXT, reference_answer TEXT, verdict ENUM('pass','fail','partial'), partial_score REAL, reviewer, reviewed_at)

### 4.6 统计预聚合（每日刷新）
- **stats_user_daily**(id, user_id, date, group_id, answer_count, correct_count, wrong_count, exam_count, exam_score_sum, exam_pass_count, UQ(user_id,date,group_id))
- **stats_group_daily**(id, group_id, date, dimension ENUM, user_count, answer_count, correct_count, completion_rate REAL, exam_avg_score, UQ(group_id,date,dimension))
- **refresh_jobs**(id, type, last_run_at, status)

### 4.7 系统与审计
- **settings**(id, key UK, value TEXT, category)
- **audit_logs**(id, actor, action, target_type, target_id, detail JSON, ip, created_at)
- **drafts**(id, user_id, form_key, payload JSON, updated_at, PK(user_id,form_key))

## 5. API 契约（REST，前缀 /api）

> 认证：除 register/login/verify 外均需 `Authorization: Bearer <jwt>`；管理端接口经 `core/deps.py` 校验角色。

### 5.1 认证 auth
- `POST /auth/register` {email,password,name} → 发验证码（BackgroundTasks）
- `POST /auth/verify` {email,code} → 激活
- `POST /auth/login` {email,password} → {token, user}
- `GET /auth/me`
- `POST /auth/resend-verification` {email}
- `POST /auth/change-password` {old,new}

### 5.2 用户/分组（管理端）
- `GET/POST/PUT/DELETE /admin/users`；`POST /admin/users/{id}/approve|disable|enable|reset-password|groups`
- `GET /admin/groups`（树形）；`POST/PUT/DELETE /admin/groups`

### 5.3 题库与上传（管理端）
- `GET /admin/questions`(筛选分页)；`POST/PUT/DELETE /admin/questions/{id}`
- `GET/POST /admin/question-banks`
- `GET /admin/upload/template`（下载 .xlsx）
- `POST /admin/upload/preview`(文件+group_id+bank) → {rows[], total, typeDist, errors[]}
- `POST /admin/upload/import`(confirmToken) → {success, failed}
- `GET /admin/upload/error-report/{jobId}`

### 5.4 考试
- `GET /admin/exam-templates`；`POST /admin/exam-templates/preview-paper`(规则) → 预览题目
- `POST /admin/exam-templates`(保存)
- `GET/POST/PUT /admin/exams`；`POST /admin/exams/{id}/publish`、`/assign`
- `GET/PUT /admin/mock-config`
- 用户端：
  - `GET /exams/available`
  - `POST /exams/{id}/start` → 创建 exam_session，返回题目（已打乱）
  - `POST /exams/session/{sid}/answer` {qId, answer, version} → 逐题落库（乐观锁）
  - `POST /exams/session/{sid}/submit` → 结算
  - `GET /exams/session/{sid}/result`

### 5.5 练习 records
- `GET /records/practice/modes`
- `POST /records/practice/start` {mode, params} → 题目集
- `POST /records/practice/answer` {qId, answer, mode} → 实时判分 + 落库
- `GET /records/practice/progress`
- `POST /records/questions/{qId}/toggle-mark` {note}
- `POST /records/questions/{qId}/short-eval` {mastered:bool}

### 5.6 排行 rank
- `GET /rank?dimension=&metric=&range=` → {table[], chart[{name,value}]}

### 5.7 复核 review（管理端）
- `GET /admin/review/pending`
- `POST /admin/review/{id}` {verdict, partial_score}
- `POST /admin/exams/{id}/publish-results`

### 5.8 面板 panel
- `GET /panel/user`
- `GET /panel/admin?range=`

### 5.9 系统与审计
- `GET/PUT /admin/settings/{category}`
- `POST /admin/settings/smtp/test`
- `GET /admin/audit?page=&action=&actor=&from=&to=`

## 6. 关键业务逻辑

### 6.1 判分 grading.py
```
grade(question, userAnswer):
  单选/判断: userAnswer == answer
  多选: sorted(userAnswer)==sorted(answer)  无部分分
  填空: 逐空比对(大小写不敏感), 全对才算对(无部分分)
  拖拽: answer[k]==v for all k, 且数量相等
  简答: 返回 None(待自评/复核)
```

### 6.2 规则组卷 paper_service.py
- 按 config：题型配比×数量、难度配比、来源（group_id/tag 筛选）、是否重复抽、随机种子。
- 确定性随机（种子）复现；按题型分组查询候选题 → 抽样 → 组成 paper。
- paper_templates 固化题目清单 + 选项打乱映射。

### 6.3 考试会话与乐观锁
- start：建 exam_session(status=in_progress, version=1)，返回题目(打乱后)。
- answer：`UPDATE ... SET answers=..., version=version+1 WHERE id=? AND version=?`，affected=0 → 重试。
- 提交：结算客观题 → exam_results(objective_score, need_review, published：无简答且 show_score_immediately 为 true；有简答则待复核)。

### 6.4 简答复核与公布
- need_review：生成 short_answer_reviews，exam_results.published=false。
- 管理端逐题复核 → 计入 final score。
- 全部复核完 → publish-results → published=true。

### 6.5 邮件 mail_service.py（BackgroundTasks）
- 注册验证码、考试发布通知、复核完成通知；模板/SMTP 从 settings 读取（密码加密存）。
- 测试邮件接口即时发送用于配置校验。

### 6.6 预聚合 stats_service.py
- 每日后台刷新（启动触发当日 + 每日凌晨定时）；管理端可手动触发。
- 重算 stats_user_daily / stats_group_daily；排行命中预聚合表。

## 7. 安全设计

- 密码 bcrypt；JWT HS256，过期 7d。
- 所有 SQL 走 ORM 参数化，无字符串拼接。
- 输入校验：Pydantic schema；上传校验类型/大小（settings 配置上限）。
- 权限：`core/deps.py` 实现 `require_role`、`require_super`、部门管理员数据范围过滤（仅本部门 group_id 子树）。
- XSS：前端 v-text 为主，Element Plus 默认转义。
- 敏感设置（SMTP 密码）加密存储（Fernet，密钥从 env）。
- 审计：管理员写操作经 audit_service 记录。

## 8. 部署与启动

### 8.1 开发
- 后端：`cd backend && uv sync && uv run uvicorn app.main:app --reload`
- 前端：`cd frontend && pnpm i && pnpm dev`（dev 代理 /api 到 8000）

### 8.2 一键启动 start.sh
```bash
#!/bin/bash
set -e
cd frontend && pnpm i && pnpm build
cd ../backend && uv sync
uv run python scripts/init_db.py
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```
访问 `http://localhost:8000`（前端 dist 由 FastAPI StaticFiles 托载，`/api/*` 优先路由）。

### 8.3 初始化
- `scripts/init_db.py`：建表、默认 settings、创建超级管理员（env 配置或默认 `admin@example.com`，首次提示改密）。

## 9. Excel 上传模板规范（精确）

工作簿 Sheet：`单选题 / 多选题 / 判断题 / 填空题 / 简答题 / 拖拽题 / 说明`。
统一表头：
- 单选/多选：`题干 | 选项(A.xx \n B.xx) | 答案(单选A；多选ABC) | 解析 | 难度 | 知识点标签 | 分值 | 所属分组`
- 判断：`题干 | 答案(正确/错误) | 解析 | 难度 | 标签 | 分值 | 分组`
- 填空：`题干(____标空位) | 答案(按空位顺序，空间用|分隔，每空多等价用/分隔) | 解析 | 难度 | 标签 | 分值 | 分组`
- 简答：`题干 | 参考答案 | 解析 | 难度 | 标签 | 分值 | 分组`
- 拖拽：`题干 | 题项与正确容器(每行 题项:正确容器) | 解析 | 难度 | 标签 | 分值 | 分组`
- 说明：示例 + 转换 Prompt 草稿（供豆包/DeepSeek 转换 Word 用）

导入时分组/题库来源由上传步骤选择（覆盖列值空缺）。

## 10. 与需求/UI 的一致性校验

| 项 | 依据 | 一致性 |
|---|---|---|
| 6 题型 + JSON answer | req §4.3 | ✓ questions.answer JSON |
| 实时落库无离线补传 | req §4.4 | ✓ practice_records/exam_sessions 逐题接口 |
| 模拟/正式考试差异、不可暂停 | req §4.5, ui §4.4 | ✓ exam_definitions.rules，无 pause 接口 |
| 简答复核后公布 | req §4.5 | ✓ published 流程 |
| 规则组卷指派分组 | req §4.5 | ✓ paper_templates + assign |
| 上传前选分组 | ui §7 | ✓ upload/preview 入参 |
| 排行四维度+Top10+每日聚合 | req §4.6, ui §3.7 | ✓ stats_*_daily |
| 自定义主题色/Logo | ui §1.1 | ✓ settings |
| 审计日志 | ui §11 | ✓ audit_logs |
| 草稿自动保存 | ui §11 | ✓ drafts |

## 11. 后续文档

- `task_list.md`：Phase 4 开发任务拆分与状态跟踪。
- `deployment.md`：部署运维细节。
- `audit_report.md` / `fix_changelog.md`：Phase 5 产出。
