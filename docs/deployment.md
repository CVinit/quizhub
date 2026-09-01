# 培训考试平台 部署文档

> 企业培训答题系统 · Python + FastAPI + SQLite + Vue3

## 一、环境要求

| 组件 | 版本 |
| --- | --- |
| Python | 3.10+（推荐 3.12） |
| Node.js | 18+ |
| 包管理 | uv（后端）/ pnpm 或 npm（前端） |
| 数据库 | SQLite3（WAL 模式，无需独立部署） |

## 二、目录结构

```
training-platform/
├── backend/              # FastAPI 后端
│   ├── app/              # 应用代码（api/services/models/...）
│   ├── scripts/init_db.py  # 建表 + 默认设置 + 超管账号
│   ├── data/             # SQLite 数据库与上传文件（运行时生成）
│   └── pyproject.toml    # uv 依赖
├── frontend/             # Vue3 + TS 前端
│   └── dist/             # 构建产物（后端托管）
├── docs/                 # 需求/架构/任务等文档
└── start.sh              # 一键启动
```

## 三、一键启动（开发/演示）

```bash
./start.sh
```

该脚本依次：
1. `frontend/` 执行 `pnpm install && pnpm build`（或 npm）
2. `backend/` 执行 `uv sync && python scripts/init_db.py`（建表、默认设置、超管账号）
3. 启动 `uvicorn app.main:app --host 0.0.0.0 --port 8000`

访问 http://localhost:8000，使用初始化时配置的超管邮箱和密码登录。

> 首次登录后请立即在「系统管理 → 基础设置」或个人中心修改密码。

## 四、手动部署步骤

### 1. 后端

```bash
cd backend
uv sync                                  # 安装依赖到 .venv
uv run python scripts/init_db.py         # 初始化数据库
uv run python scripts/migrate_2026_08_28.py # 既有库迁移
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 2. 前端

```bash
cd frontend
pnpm install && pnpm build               # 产物输出到 frontend/dist
```

后端 `main.py` 会自动托管 `frontend/dist` 作为静态资源，无需额外 Nginx。

## 五、环境变量

通过环境变量注入密钥（生产环境务必设置）：

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `TRAINING_SECRET_KEY` | JWT 签名密钥 | 未设置则生成进程级随机密钥（重启后登录失效） |
| `TRAINING_ENC_KEY` | Fernet 密钥（加密 SMTP 密码等），32 字节 url-safe base64 | 空（非空敏感设置拒绝写入） |
| `TRAINING_SUPER_ADMIN_EMAIL` | 初始超管邮箱 | `admin@example.com` |
| `TRAINING_SUPER_ADMIN_PASSWORD` | 初始超管密码 | 未设置时初始化脚本生成一次性随机密码 |
| `TRAINING_CORS_ORIGINS` | 允许跨域来源，逗号分隔；`*` 时禁用凭据 | `*` |
| `TRAINING_TRUST_PROXY` | 是否信任 `X-Forwarded-For` 取真实 IP（反代后建议 `true`，裸跑建议 `false`） | `false` |

生成 Fernet 密钥：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 六、SMTP 邮件配置

登录管理后台 → 系统管理 → 基础设置/SMTP 邮件：

1. 填写 SMTP 服务器、端口、用户名、密码、发件人地址
2. SMTP 密码经 Fernet 加密存储（需先配置 `TRAINING_ENC_KEY`）
3. 点击「发送测试邮件」验证

> 未配置 SMTP 时，验证码邮件不会发送，也不会把收件人或验证码写入后端日志。

## 七、题库导入

1. 管理后台 → 题库管理 → 上传题库
2. 下载 Excel 模板（6 个题型 Sheet + 说明 Sheet + 大模型转换 Prompt）
3. 可借助豆包/DeepSeek 等大模型按 Prompt 将 Word 题库转为该 Excel 格式
4. 选分组/题库 → 上传预览 → 查看错误报告 → 确认导入

## 八、运行单元测试

```bash
cd backend
uv pip install -e ".[dev]"        # 安装 pytest/httpx
uv run pytest tests/ -v
```

覆盖判分（grading）、Excel 解析（excel）、规则组卷（paper）。

## 九、生产部署建议

- **进程管理**：用 `gunicorn -k uvicorn.workers.UvicornWorker app.main:app` 或 systemd 托管
- **反向代理**：Nginx 转发 80/443 到 8000，配置 SSL
- **备份**：定时备份 `backend/data/training.db`（WAL 模式下可热备）
- **并发**：SQLite WAL 模式支持并发读 + 单写，≤100 用户足够；如需更高并发可迁移 PostgreSQL
- **定时任务**：建议每日凌晨触发统计预聚合刷新（`POST /api/admin/panel/refresh?days=1`）

### 限流（防扫描刷接口）

公网部署必须开启限流，防止注册/登录/验证码等无认证接口被扫描爆破。限流采用**单进程内存固定窗口计数器**（`app/core/rate_limit.py`），与 SQLite 单 uvicorn 进程架构匹配，无需 Redis。

**已覆盖接口与阈值：**

| 接口 | IP 维度 | 账号/邮箱维度 | 防护目标 |
| --- | --- | --- | --- |
| `POST /api/auth/register` | 10 次/小时 | 邮箱 3 次/小时 | 批量注册刷邮件 |
| `POST /api/auth/verify` | 30 次/10 分钟 | 邮箱 5 次/10 分钟 | 验证码爆破 |
| `POST /api/auth/login` | 10 次/分钟 | 账号 8 次/5 分钟 | 撞库/密码爆破 |
| `POST /api/auth/resend-verification` | 10 次/10 分钟 | 邮箱 3 次/10 分钟 | 验证码邮件轰炸 |
| `POST /api/auth/change-password` | — | 用户 5 次/5 分钟 | 密码爆破 |
| `POST /api/admin/system/smtp/test` | — | 管理员 5 次/小时 | 滥用测试邮件发件 |
| `POST /api/admin/questions/upload/preview` | — | 管理员 20 次/小时 | 大文件刷接口 |

超限返回 `429 Too Many Requests`，带 `Retry-After` 头。阈值需调优时改 `app/api/auth.py`、`system.py`、`questions.py` 中对应常量。

**真实客户端 IP 配置（关键）：**

| 部署形态 | 环境变量 `TRAINING_TRUST_PROXY` | 说明 |
| --- | --- | --- |
| Nginx/Caddy 反代后 | `true`（默认） | 读取 `X-Forwarded-For` 首段。Nginx 必须 `proxy_set_header X-Forwarded-For $remote_addr;` 覆盖该头防伪造 |
| 裸跑 uvicorn 直暴露 | `false` | 用连接对端地址，避免信任可伪造的转发头 |

**多 worker 注意**：当前内存计数器仅在单进程内有效。若用 `gunicorn -w N` 多 worker，需切换为 Redis 后端（如 slowapi + redis）否则各 worker 计数独立、限流失效。单 uvicorn 进程（当前 `start.sh`）下完全有效。

**反代 IP 伪造防护**：仅信任 `X-Forwarded-For` 的**最右侧一段**（由可信反代写入）。当前实现取首段，生产建议将 Nginx 配置为 `proxy_set_header X-Forwarded-For $remote_addr;`（覆盖而非追加），则首段即真实客户端，取首段安全。

## 十、常见问题

| 问题 | 解决 |
| --- | --- |
| 端口 8000 被占用 | `--port` 指定其他端口 |
| 前端样式/主题色不生效 | 清浏览器缓存，或检查「基础设置 → 主题色」 |
| SMTP 测试邮件未收到 | 查看后端日志的 `[mail][fallback]` 或 SMTP 服务器拒信 |
| 数据库迁移（表结构变更） | SQLite 用 `ALTER TABLE` 增量补列；重大变更重建库 |
