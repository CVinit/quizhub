# 培训考试平台 后端代码审查报告（2026-09-24 第二轮）

> 审查方式：全量读码（`backend/app` 全部模块 + `backend/scripts` 迁移 + 测试套件 + `.env.example`/Docker 部署面），
> 每条结论均定位到具体行；**3 项 Critical 先在隔离临时库实跑复现、再修复、再回归**（复现脚本置于 /tmp，未入库）。
> 上一轮报告见 [backend_review_2026-09-24.md](backend_review_2026-09-24.md)，
> 修复记录见 [fix_changelog.md](fix_changelog.md) 的「后端全面评审第二轮」。

## 一、Critical（3 项，均已修复）

### C1 组卷配置只校验体积、不校验结构 → 一条坏数据让两个考试列表 500

`ExamCreateIn.rules` / `ExamUpdateIn.rules` 是裸 dict（仅 `validate_json_size` ≤64KB），内部结构
（`type_quota` 的值必须是 int）从不校验；而题数展示路径 `exam/common._exam_question_count(s)`
直接 `sum(int(v) for v in quota.values())`。`generate_paper` 里确实有这套校验，但它只在**开考/发布**时
才被调用，创建/更新时不触发。

实跑复现：

```
POST /api/admin/exams  rules={"type_quota": {"单选题": "abc"}}   → 201 落库
GET  /api/admin/exams                                            → ValueError → 500
```

更严重且完全可达的路径：对**已发布**考试 `PUT /api/admin/exams/{id}` 带
`rules={"type_quota": {"单选题": "abc"}}` + `confirm_reset=true`（前端修改组卷来源的正常流程）
→ 冻结题被作废、rules 落库，随后：

- `GET /api/exams/available` → 500：该指派分组下**所有考生**打不开考试列表；
- `GET /api/admin/exams` → 500：管理员无法从界面定位/修复这条数据（列表本身打不开）。

`{"type_quota": null}` → `TypeError`、`{"type_quota": [1,2]}` → `AttributeError`，同样 500。

修复：抽出 `paper_service.validate_config(config)`（不查库）作为**唯一**的入口校验，在
`create_exam`/`update_exam`（以及模板创建路径经 `generate_paper`）调用；展示路径改为防御式取值
（`_quota_total` / `_list_len`），即使历史脏数据已落库，列表也必须照常可读。

### C2 手工建题/改题绕过「答案形状」不变式 → 存下永远判错、无法作答的题

Excel 导入路径（`utils/excel._parse_row`）对这批不变量做了完整校验（上一轮 C10/S1/S4），
但管理端手工创建/更新路径没有，且 `update_question` 的校验分支本身有缺口。实跑复现 6 例，**全部 ACCEPTED**：

| 输入 | 后果 | 同一份数据走 Excel 路径 |
| --- | --- | --- |
| 填空题：题干 2 个空、`answer=[["甲答案"]]` | `grading._grade_fill` 要求 `len(correct)==len(user)` → 任何作答恒判错 | 行级报错「答案空位数(1)与题干空位数(2)不一致」 |
| 单选题 `options=[]`（或 `None`）+ `answer="A"` | 无选项可渲染，恒判错 | 报错「选项为空或格式错误」 |
| 拖拽题 `answer={"HTTP":"80"}` + `left_items=[]`/`right_items=[]` | 映射与左右项无交叉校验，渲染不出可作答的题 | 由答案反向构造左右项，不可能分叉 |
| 多选题 `answer="AA"` | 排序比较后永不相等 | — |
| `PUT /questions/{id}` 只传 `options=["甲","乙"]`（原答案 `C`） | `if "type" in data and "answer" not in data` 与 `if "answer" in data` **两个分支都不成立** → 不校验，落库越界答案 | — |
| `PUT` 只把填空题题干从 1 个空改成 2 个空 | 同上，空位数不再交叉校验 | — |

修复：空位数口径收敛到 `utils/question_text.count_blanks`（Excel 与手工路径共用同一实现）；
`_validate_answer_shape` 增加 `question_text` / `left_items` / `right_items` 参数；`update_question`
改为「题型/选项/题干/答案/左右项任一变化 → 按本次生效的组合重跑一次校验」。

### C3 `.env.example` 的 Fernet 密钥生成命令产出的密钥会被 Fernet 拒绝

`.env.example:7-9`（`docker-compose.yml` 顶部让用户 `cp .env.example .env`）写的是
`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` —— 产出 **43 字符无 padding** 的字符串，
`base64.urlsafe_b64decode` 抛 `binascii.Error: Incorrect padding`，`Fernet()` 随即抛
`ValueError: Fernet key must be 32 url-safe base64-encoded bytes.`（Python 3.13.9 + cryptography 50.0.0 实测）。

实跑复现：按该命令部署后 `PUT /api/system/settings {"category":"smtp","updates":{"smtp_password":"s3cret"}}`
→ **400 + 一句英文的 cryptography 内部错误**；同时 `get_settings` 对加密项降级为空串 →
SMTP 密码永远存不进去、也读不出来，邮件链路整体不可用。

同仓另外两处口径是对的（`docs/deployment.md:133` 用 `Fernet.generate_key()`，
`start.sh:28`/`start.ps1:44` 用 `base64.urlsafe_b64encode(secrets.token_bytes(32))`），只有 compose 部署指引
指向的这份是错的。

修复：文档改为正确命令并显式标注「不能用 token_urlsafe(32)」；`config._check_enc_key` 启动期校验格式
并打印可直接复制的生成命令（只记 ERROR 不退出：登录/练习等不依赖该密钥的功能应继续可用）；
`core/security._fernet()` 把 cryptography 的 `ValueError` 包装为中文 `RuntimeError` → 路由 503 可操作提示。

## 二、Suggestions（6 项，均已修复）

| # | 问题 | 修复位置 |
| --- | --- | --- |
| S1 | 非 multipart 请求体没有全局上限：字段级上限都在 body 完整解析之后才生效，未登录的 `/api/auth/login` 即可打内存 | 新增 `core/body_limit.py`（ASGI 中间件，`Content-Length` 预检 + 流式计数，超 1MB → 413）；multipart 不受影响 |
| S2 | 改密把**当前会话**也踢下线（注释却写「其它会话」），前端拿旧 token 继续用 → 「修改成功」后第一个请求 401 | 后端回传新 token（`ChangePasswordOut`），前端 `auth.setToken()` 就地替换；日志文案更正 |
| S3 | 审计 `user.update` 的 detail 落姓名明文（PII），与 `user.create`/`user.delete` 口径不一致 | detail 只记字段名（`{"name": "<changed>"}`） |
| S4 | 交卷崩溃残留（`scoring` 且无成绩）时二次交卷报 400「考试已结束」，考生最长卡 30 分钟 | 先按同一超时守卫回收并重跑结算（幂等），窗口内返回 409「试卷正在结算中」 |
| S5 | 建号时任意 `IntegrityError` 都归因为「该邮箱已存在」；`dept_group_id` 未校验存在性 | 新增 `core.errors.is_unique_violation` 区分归因；补分组存在性校验 |
| S6 | 4 处零覆盖：难度配比分枝、SMTP 主机 SSRF 拒绝分支、设置接口掩码逻辑、`ensure_defaults` | 新增 `tests/test_review_2026_09_24_coverage.py`（16 例） |

## 三、Nits（5 项，均已修复）

| # | 问题 | 修复 |
| --- | --- | --- |
| N1 | 两行 >120 字符（模板下载 `Content-Disposition`） | 文件名抽常量；xlsx 媒体类型收敛到 `utils.excel.XLSX_MEDIA_TYPE` |
| N2 | `_recover_stuck_scoring(user_id=None)` 无生产调用方 | 在 `lifespan` 启动维护中调用一次并记 WARNING |
| N3 | `publish_exam` 判据为 `status != "published"` → 归档后重新发布重复群发通知 | 改为 `status == "draft"` |
| N4 | 用户/题库列表返回无约束手拼 dict | 新增 `UserListOut`/`UserListItemOut`/`QuestionBankItemOut` 并挂 `response_model` |
| N5 | `/files/*.svg` 的提前 404 未带安全响应头 | 抽出 `_apply_security_headers()`，两个返回点共用 |

## 四、验证方式与命令

```bash
cd backend
.venv/bin/python -m ruff check app scripts tests           # All checks passed!
.venv/bin/python -m ruff format --check app scripts tests  # 138 files already formatted
.venv/bin/python -m mypy app                               # Success: no issues found in 76 source files
.venv/bin/python -m pytest -q --cov=app                    # 695 passed，覆盖率 94%

cd ../frontend
npm run format:check && npm run lint && npm run typecheck && npm run test   # 71 passed
```

关键结论的实跑复现（隔离临时库，脚本未入库）：

1. `create_exam(rules={"type_quota": {"单选题": "abc"}})` → 400（修复前 201 落库）；
2. 库中存在畸形 rules 时 `list_exams` / `list_available` 均正常返回（修复前 500）；
3. 手工建题 4 类畸形形状 → 400（修复前全部 ACCEPTED）；「只改选项」也会按新选项集重校答案；
4. 按 `.env.example` 新命令生成的密钥被 `Fernet()` 接受；旧命令产物仍被拒；
5. 2MB JSON → 413 且带跨域头；multipart 1.2MB 上传不被该阈值拦截；
6. 改密后旧 token → 401、响应中的新 token → 200。

## 五、本轮新增测试

| 文件 | 例数 | 覆盖 |
| --- | --- | --- |
| `tests/test_review_2026_09_24_fixes.py` | 37 | C1（9 种畸形 rules + 脏数据列表可读 + 合法配置不受影响）、C2（5 类畸形形状 + 只改选项/题干 + 合法形状回归）、C3（文档命令可执行、非法密钥 503 可操作、启动期 ERROR）、S1（413 / chunked 计数 / multipart 放行 / 404 安全头）、S2（新 token 可用、旧 token 失效）、S3（detail 无姓名）、S4（崩溃残留可续算 / 结算中 409）、S5（分组不存在 400）、N3（通知只发一次）、N4（响应契约字段） |
| `tests/test_review_2026_09_24_coverage.py` | 16 | 难度配比分层抽样（比例、余数补给、池不足回补）、`validate_config` 各类畸形、SMTP 主机 SSRF 拒绝与私网放行、设置掩码（不回传明文、不覆盖密文）、`ensure_defaults` 幂等 |
| `frontend/src/stores/auth.spec.ts` | +1 | `setToken` 就地替换凭据（改密衔接） |
