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
├── start.sh              # 一键启动（Linux/macOS，bash）
├── start.bat             # 一键启动（Windows CMD）
└── start.ps1             # 一键启动（Windows PowerShell）
```

## 快速开始

### 环境要求

- Python 3.10+（推荐 3.12）
- Node.js 18+
- [uv](https://docs.astral.sh/uv/)（后端包管理）
- pnpm 或 npm（前端包管理）

### 一键启动

**Linux / macOS**（bash）：
```bash
./start.sh
```

**Windows**：
- PowerShell：`./start.ps1`（若提示执行策略受限，先 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`）
- CMD：双击 `start.bat` 或命令行运行 `start.bat`

启动后访问 http://localhost:8000

### 手动分步启动

**Linux / macOS**：
```bash
# 前端：安装依赖并构建（后端托管 dist）
cd frontend && pnpm install && pnpm build

# 后端：安装依赖并初始化数据库
cd backend && uv sync && uv run python scripts/init_db.py

# 启动后端
cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Windows（PowerShell）**：
```powershell
# 前端：安装依赖并构建（后端托管 dist）
cd frontend; pnpm install; pnpm build

# 后端：安装依赖并初始化数据库
cd backend; uv sync; uv run python scripts/init_db.py

# 启动后端
cd backend; uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Windows 环境准备

| 工具 | 安装方式 |
| --- | --- |
| Python 3.10+ | [python.org](https://python.org)（安装时勾选 Add to PATH） |
| uv | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Node.js 18+ | [nodejs.org](https://nodejs.org) |
| pnpm | `npm install -g pnpm` |
| Git | [git-scm.com](https://git-scm.com)（顺带获得 Git Bash，可直接跑 `start.sh`） |

Windows 注意点：
- `uvicorn[standard]` 在 Windows 使用 ProactorEventLoop，本项目无依赖 `fork` 的库，功能正常。
- SQLite WAL 模式在 Windows 正常工作，`training.db-wal`/`-shm` 自动生成，无需配置。
- 若 8000 端口被占用，改 `--port 8001`。
- 生产密钥环境变量设置：PowerShell `$env:TRAINING_SECRET_KEY="..."`；CMD `set TRAINING_SECRET_KEY=...`。

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
