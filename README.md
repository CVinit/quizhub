# quizhub 培训考试平台

企业培训答题系统 · Python + FastAPI + SQLite + Vue3 + Element Plus。

支持题库管理（6 类题型 Excel 批量导入）、练习答题、模拟考试、正式考试、自动判分、错题本、排行榜、用户管理（手动新增 / Excel 批量导入）、分组组织、审计日志、移动端响应式适配。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.10+ / FastAPI / SQLAlchemy 2.0 / SQLite3(WAL) / uv |
| 前端 | Vue3 Composition API + TypeScript 5.x + Element Plus + Vite + pnpm |
| 鉴权 | JWT(HS256) + bcrypt / 图形验证码 + 邮箱验证码注册 |
| 数据库 | SQLite3（WAL 模式，开箱即用） |

## 目录结构

```
quizhub/
├── backend/              # FastAPI 后端
│   ├── app/              # api / services / models / schemas / core / utils
│   ├── scripts/          # init_db.py 建表 + 默认设置 + 超管账号；convert_docx.py
│   ├── tests/            # 单元测试（grading/paper/excel/captcha/rate_limit）
│   ├── data/             # SQLite 数据库与上传文件（运行时生成）
│   ├── pyproject.toml    # uv 依赖
│   └── uv.lock
├── frontend/            # Vue3 + TS 前端
│   └── src/              # views / layouts / components / api / stores / composables
├── docs/                # 需求 / 架构 / UI-UX / 任务清单 / 部署 / 审计等文档
└── start.sh             # 一键启动（构建前端 → 初始化 DB → 起后端）
```

## 快速开始

### 环境要求

- Python 3.10+（推荐 3.12）
- Node.js 18+
- [uv](https://docs.astral.sh/uv/)（后端包管理）
- pnpm 或 npm（前端包管理）

### 一键启动

```bash
./start.sh
```

启动后访问 http://localhost:8000

### 手动分步启动

```bash
# 前端：安装依赖并构建（后端托管 dist）
cd frontend && pnpm install && pnpm build

# 后端：安装依赖并初始化数据库
cd backend && uv sync && uv run python scripts/init_db.py

# 启动后端
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 默认账号

初始化后默认超级管理员（可通过环境变量覆盖）：

- 邮箱：`admin@example.com`
- 密码：`admin12345`（环境变量 `TRAINING_SUPER_ADMIN_PASSWORD`）

> ⚠️ 生产环境务必通过环境变量注入：`TRAINING_SUPER_ADMIN_EMAIL` / `TRAINING_SUPER_ADMIN_PASSWORD`。

## 生产环境关键配置

启动前通过环境变量注入（缺失时后端会打印安全警告并降级为进程级临时密钥 / 可逆编码）：

| 环境变量 | 用途 |
| --- | --- |
| `TRAINING_SECRET_KEY` | JWT 签名密钥（缺失则进程级随机，重启后所有登录失效） |
| `TRAINING_ENC_KEY` | 敏感设置（如 SMTP 密码）的 Fernet 加密密钥 |
| `TRAINING_SUPER_ADMIN_EMAIL` | 初始超级管理员邮箱 |
| `TRAINING_SUPER_ADMIN_PASSWORD` | 初始超级管理员密码 |

详见 [docs/deployment.md](docs/deployment.md)。

## 文档

- [需求规格](docs/requirement.md)
- [架构设计](docs/architecture_design_document.md)
- [UI/UX 规范](docs/ui_ux_specifications.md)
- [开发任务清单](docs/task_list.md)
- [部署文档](docs/deployment.md)
- [审计报告](docs/audit_report.md)
- [修复记录](docs/fix_changelog.md)

## 测试

```bash
cd backend && uv run pytest
```

覆盖自动判分、试卷生成、Excel 解析、验证码、限流等（30+ 用例）。
