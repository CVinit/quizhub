# 培训考试平台 后端代码审查报告（2026-09-24）

> 审查方式：全量读码（`backend/app` 全部模块 + `backend/scripts` 迁移 + 测试套件），
> 每条结论均**重新定位到具体行**，关键结论在隔离临时库实跑复现（复现脚本置于 /tmp，未入库）。
> 上一轮报告见 [backend_review_2026-09-23.md](backend_review_2026-09-23.md)（本报告第七节对其
> 「已全部修复」的结论作了更正）。
> 修复记录与逐项映射见 [fix_changelog.md](fix_changelog.md) 的「后端全面评审修复（2026-09-24）」。

## 一、Critical（10 项，均已修复）

### C1 删除分组会让「仅指派给该分组」的考试变成全员可见（fail-open）

`services/group_service._strip_group_from_assignments` 把被删分组从 `ExamDefinition.group_ids`
移除；只指派给该分组的考试因此变成 `[]`，而 `exam/common._user_can_access_exam` 把空指派解释为
「不限（全员可见）」。

实跑复现（A 属于该分组、B 不属于任何分组）：

```
删除前：A 可见 = [1]，B 可见 = []
删除后：group_ids == []，B 可见 = [1]      ← 越权可见
```

修复：剔除后会删空时**保留悬空 id**（该考试对所有人不可见，fail-closed）并记 WARNING；
包含关系判定改用 `json_each` 下推 SQL（同时消除写事务内的全表加载）。

### C2 图形验证码可被离线反查表 100% 破解

`core/captcha._render_svg` 用答案作为伪随机种子（`_SeededRng(answer)`），渲染结果与答案一一对应，
攻击者预计算 10⁴ 个 SVG 建表即可瞬时反查。

实跑复现：

```
预计算条目数: 10000
反查得到的答案: 1621
verify(answer) -> True            # 额外 3 次全部命中
```

修复：干扰线/抖动/噪点改用 `random.SystemRandom()`，同一答案每次渲染不同（复现脚本已确认
反查表命中率为 0）。

### C3 用户导入模板自带一条可被导入的部门管理员示例行（口令公开）

`utils/user_excel.build_template` 的示例行是 `lisi@example.com / 部门管理员 / Abc@12345 / 正常`，
而用户解析没有题库模板那样的示例行跳过（`utils/excel.EXAMPLE_PREFIX`）。

实跑复现：`user_excel.preview(build_template())` → `total=2, valid_count=1`，有效行即该部门管理员，
`verify_password("Abc@12345", hash) is True`。受影响者恰是唯一有权创建管理员账号的角色。

修复：示例行邮箱加 `EXAMPLE_PREFIX` 前缀并在解析时跳过；示例口令留空作第二道防线；说明页补充提示。

### C4 手工选题不在删题守卫内 → 考试永久无法开考/发布

`question_service.delete_question` 只统计 `exam_questions`（已固化卷面），而手工选题的考试在首次
开考/发布前没有固化行，于是题目可被删除，`manual_questions` 中残留不存在的 id，之后
`_persist_exam_questions` 抛 400「考试包含不存在的题目」，管理端列表却仍按 `len(manual_questions)`
显示题数。

修复：`delete_question`/`delete_bank` 增加手工选题引用统计（`json_each`），命中则 409。

### C5 题库导入静默丢数据且不报截断

`utils/excel.parse_workbook` 按**物理行** break，`import_service.preview` 却用
`preview_obj.total >= PARSE_ROW_MAX` 反推截断：前置空行时数据行被静默丢弃。

实跑复现（上限 10000，9000 空行 + 2000 数据行）：`total=1000, errors=[], truncated=False`。

修复：上限改按**数据行**计数（空行/示例行不占额度），截断标记由解析器在 break 处显式返回，
`import_service` 直接消费。

### C6 `paper_template_id` / `manual_questions` 不校验存在性

- 不存在的 `paper_template_id` → 外键 IntegrityError → **500**（实跑复现：`FOREIGN KEY constraint failed`）；
- 不存在的 `manual_questions` id → 落库成功但考试永远无法开考（题数还虚高）。

修复：新增 `_validate_paper_template` / `_validate_manual_questions`（对超管同样生效），
并用 `_commit_exam` 把 IntegrityError 映射为 400 兜底并发窗口。

### C7 并发同邮箱建号/注册 → 500

`user_service.create_user` 与 `auth_service.register` 都是「先 SELECT 判存在 → INSERT commit」，
`users.email` 有唯一索引但 commit 未捕获 IntegrityError。`register` 侧更重：验证码已在
`_consume_code` 中 commit 消费，冲突后用户必须重新获取验证码。

修复：两处包 `except IntegrityError → rollback → 400`；`register` 的邮箱重复检查提前到消费
验证码之前（不再白烧验证码）。

### C8 已交卷待复核的会话，`start_exam` 仍返回可续答卷面

`exam/sessions.py` 在 `status == "scoring"` 且已有 `ExamResult` 时直接返回卷面，考生被放回考试
界面并重新计时，但任何作答都被 `submit_answer` 以 400「考试已结束」拒绝。

修复：该状态返回 409「本场考试已交卷，成绩待公布或复核」。

### C9 迁移脚本重建 `stats_user_daily` 无事务保护

`scripts/migrate_2026_09_23_stats_exam_columns.py` 用「RENAME → CREATE → INSERT → DROP」重建表，
而 Python 的 sqlite3 **不会为 DDL 自动开启事务**（实测 `in_transaction` 在 `ALTER TABLE … RENAME`
后仍为 False）。中断即留下「目标表缺失 + 数据滞留 `_old` 表」，重跑又因 `needs_rebuild` 对不存在的
表返回 False 而打印「无需处理」，历史聚合静默丢失（`startup_refresh` 只覆盖今昨两天）。

修复：重建与恢复都包显式 `BEGIN … COMMIT`（中断自动回滚）；新增 `needs_recovery()` /
`recover_interrupted_rebuild()` 从 `_old` 表兜底恢复（与 `migrate_2026_08_28.py` 同名机制同口径）。

### C10 填空题答案空位数与题干不一致仍判合法

解析按 `|` 切分并过滤空段，不与题干空位数交叉校验；判分要求 `len(correct_answer) == len(user_answer)`，
前端按题干中连续下划线数量渲染输入框。

实跑复现：题干「甲____与乙____分别是什么？」+ 答案「甲答案|」→ `valid=True`，该题永远判错。

修复：按 `_{2,}` 统计题干空位数并交叉校验（题干无下划线时按 1 空处理），不一致则行级报错。

## 二、Suggestions（10 项，均已修复）

| # | 问题 | 修复位置 |
| --- | --- | --- |
| S1 | 拖拽题重复左项被静默覆盖（映射数 < 左项数 → 不可作答） | `utils/excel.py` 解析期检测并报行级错误 |
| S2 | 判断题不接受 Excel 布尔单元格与 `TRUE`/`False`（与单选分支口径不一致） | `utils/excel.py` 归一化为大写 + bool 映射 |
| S3 | 「今日活跃」两条刷新路径口径相反（作答路径按 `cnt`，刷新路径按源行存在） | `stats/aggregate.refresh_user_daily` 改用当日练习源行数 |
| S4 | `create_question` 不校验答案字母是否在选项范围内 | `question_service._validate_answer_shape(qtype, answer, options)` |
| S5 | `admin_overview` 每次请求把全部 formal 考试载入 Python 计数 | `stats/panel.py` 子集判定下推 SQL（`json_each` + `NOT EXISTS`） |
| S6 | `_strip_group_from_assignments` 在写事务内全表加载两张表 | `group_service._rows_assigning_group` 用 `json_each` 只取命中行 |
| S7 | `save_draft` 的每用户草稿上限是 check-then-insert（并发可越过） | `audit_service.save_draft` 把上限判定并入 INSERT 的 SELECT |
| S8 | `publish_exam` 无状态前置校验 → 重复点击重复群发通知邮件 | `exam/admin.publish_exam` 仅 `draft → published` 时发通知 |
| S9 | mock 定义复用忽略 `show_analysis` | `exam/mock.start_mock_exam` 复用分支同步更新该列 |
| S10 | 幂等重复交卷把「未公布」一律报成「含简答待复核」 | `exam/scoring.submit_exam` 按 `need_review`/`overtime` 分支 |

## 三、Nits（10 项，均已修复）

| # | 问题 | 修复位置 |
| --- | --- | --- |
| N1 | `user_service.py` 631 行、五类职责 | 拆出 `services/user_import_service.py`（`import_users` + 预取常量），`user_service` 降至 487 行 |
| N2 | `max_questions_per_exam` 是死配置却仍在管理端展示 | 从 `DEFAULT_SETTINGS`/标签表/校验分支移除；`scripts/migrate_2026_09_24.py` 清理存量行 |
| N3 | `db_backup` 备份名与清理 glob 可能分叉 → 清理失效、备份无界增长 | 抽出 `_backup_path()` 供写入与清理共用；`_BACKUP_RE` 不再绑定 `.db` |
| N4 | 模板「所属分组ID」列从不解析（模板承诺不生效） | 解析该列 + 服务层校验存在性/数据范围/与题库分组一致，非法行在预览可见 |
| N5 | 部门管理员只改 `rules` 时被存量 `paper_template_id` 误判 403 | `exam/admin.update_exam` 仅在 payload 真正含该字段时才做模板检查 |
| N6 | 8 处测试 Liar（断言不成立或从未到达目标分支） | 见第四节 |
| N7 | 缺失的授权负向测试 | 见第四节 |
| N8 | `conftest` 用 `setdefault` 设 `TRAINING_ENC_KEY` → 503 用例依赖环境 | 用例内 monkeypatch `app.core.security.SETTINGS_ENC_KEY` |
| N9 | `migrate_2026_08_28` 重建 FK 缺 `ON DELETE CASCADE`；事务内 `PRAGMA foreign_keys=ON` 是 no-op | 补 CASCADE；改为 commit 后再重开外键 |
| N10 | 上轮报告「仅 5 项未落地」的复查判据粒度到文件 | 第七节更正；判据改为「逐条定位到行」 |

## 三之二、测试改写过程中新发现并修复的问题

- **路径参数指定的题库不存在时返回 400 而非 404**：`question_service._get_allowed_bank` 统一抛
  400，而 `update_bank` / `delete_bank` 紧随其后的 `if b is None: raise 404「题库不存在」`
  永不可达（该 helper 只在 `bank_id is None` 时返回 None，而它来自路径参数）—— 意图与实现不一致。
  已给 helper 增加 `missing_status` 参数：**路径参数**来源传 `NOT_FOUND`（404），**请求体**引用
  （创建/更新题目时的 `bank_id`）保持 400（「目标资源不存在」≠「请求体非法」），并补了 404 断言。
- **`delete_user` 的「最后一个超管」守卫无法从 HTTP 到达**：唯一调用方传
  `actor_role=user.role`（`api/users.py`），超管 actor 删除他人时必然还留下至少一个超管；
  actor == target 又被更早的「不能删除当前登录账号」拦住。属纵深防御而非活守卫，予以保留，
  并由测试以服务层直调（actor 与 actor_role 故意不匹配）覆盖该分支。
- **`migrate_2026_09_18.py` 在「考试类聚合列已被移除」的库上会失败（启动阻断）**：
  `merge_ungrouped_duplicates` 把 `exam_count` / `exam_score_sum` / `exam_pass_count`
  硬编码进合并 UPDATE，而这三列已被 `migrate_2026_09_23_stats_exam_columns.py` 删除。
  第一次启动（列还在）正常，**第二次启动**即抛 `no such column: exam_count` → 迁移以
  退出码 1 结束，而 `docker/entrypoint.sh` 用 `set -e` → 容器起不来。
  已改为按表实际 schema 取交集（`_sum_columns`），并补两条回归用例。
- **`migrate_2026_09_23_stats_exam_columns.py` 重建时索引名冲突**（本轮改造中引入并修复）：
  索引创建最初被放进「建表+回填」步骤，而 SQLite 的 `ALTER TABLE … RENAME` 会把原索引
  一并带走并**保留索引名**，于是在 DROP 掉 old 表之前建同名索引会抛
  `index ix_stats_date_user already exists`。该问题由「对**真实库副本**按启动顺序跑两轮
  迁移」的集成验证发现（此前的合成用例未给旧表建命名索引，覆盖不到）。已把索引创建移到
  DROP old 之后，并补回归用例（旧表带命名索引）。

## 四、测试质量

**Liars（8 处，已改写为驱动生产代码）**

1. `test_mock_exam_lookup_scoped_by_owner`：自己写 `created_by` 过滤并断言自己的种子，未调用任何
   生产代码 → 改为经 `exam_service.start_exam` 断言 404。
2. `test_mock_stale_definitions_cleaned`：只开考 4 次却断言 `defs <= 4`（keep 默认 5，恒真）→
   改为超过 keep 上限并断言保留数与关联行清理。
3. `test_admin_deletes` 的「最后一个超管」用例：两次调用完全相同，都停在 `actor == user_id` 分支，
   目标分支从未执行 → 改为用非目标 actor 触发。
4. `test_review_still_rejects_double_review`：未建 `ExamQuestion`，400 来自「复核题目不属于该考试」
   而非「该题已复核」→ 补 fixture 并断言文案。
5. `test_service_and_utils_layers_do_not_import_fastapi`：`"import fastapi" in text` 匹配不到
   `from fastapi import ...` → 改为 AST 判定。
6. `test_group_and_practice` 的练习上限/题库范围断言不敏感（唯一开放题库只有 2 题）→ 增加第二个
   题库并 monkeypatch `PRACTICE_LIMIT_MAX`。
7. `test_unknown_api_path_returns_404_not_200`：`frontend/dist` 不存在时 SPA fallback 未注册，
   用例恒真，且路径遍历守卫无覆盖 → `app.main.FRONTEND_DIST` 抽为模块级常量后可注入临时目录，
   覆盖「未匹配 /api → 404」「`..` 遍历 → 404」「未知非 api 路径 → index.html」三条分支。
8. `test_user_preview_peek_then_consume_survives_role_rejection`：名字声称验证 403，实际从未触发
   拒绝分支 → 改名为 `test_user_preview_peek_is_non_destructive_before_consume` 并如实断言
   peek/consume 语义。

**新增的授权负向用例**：分组增删的 scope 守卫、题库/题目的 scope 守卫、复核列表越界 scope、
跨用户 `POST /exams/session/{sid}/answer`、部门管理员越界分配分组、`/system/logo` 与三个
exam-templates 路由加入守卫矩阵。

**收紧的弱断言**：守卫矩阵断言 `status_code < 500`；`test_paper` 的 `<= 10` → `== 10`；
预览缓存淘汰断言改为观察 `len(cache)`；草稿上限边界改为字面量 50；删题库后补练习数据清理断言；
`test_exam_frozen_reset` 的统计日期改由 `ExamResult.created_at` 推导（消除业务日边界抖动）；
`test_auth_service_flows` 的「只断言抛了异常」补上具体状态码；`test_api_user_routes` 的
`>= 2` 改为精确 +1。

## 五、验证方式与命令

```bash
cd backend
.venv/bin/python -m ruff check app scripts tests        # All checks passed!
.venv/bin/python -m ruff format --check app scripts tests  # 134 files already formatted
.venv/bin/python -m mypy app                            # Success: no issues found in 74 source files
.venv/bin/python -m pytest -q                           # 642 passed, 1 warning in 110.79s
```

> 等价的项目标准命令为 `uv run ruff/mypy/pytest`（本机 uv 缓存只读时用 venv 解释器直调）。
> 基线为 609 passed；本轮新增/改写测试后为 642 passed（含 4 条迁移回归）。

关键结论的实跑复现（隔离临时库，脚本未入库）：

1. 删除分组后范围外用户仍不可见该考试（修复前：变为可见）；
2. 验证码同答案两次渲染不同、离线反查表命中率为 0（修复前：100% 命中）；
3. `user_excel.preview(build_template())` → `valid_count=0`（修复前：1，且是可用的部门管理员）；
4. `paper_template_id=999999` → 400「试卷模板不存在」（修复前：IntegrityError → 500）；
5. 手工选题被删 → 409（修复前：放行，考试之后永久 400）；
6. 9000 空行 + 2000 数据行 → `total=2000, truncated=False`（修复前：`total=1000, truncated=False`，
   静默丢 1000 题）；
7. 判断题布尔/大写单元格、填空空位数不一致、拖拽重复左项 → 分别为通过 / 行级报错 / 行级报错
   （修复前：拒绝 / 放行 / 放行）；
8. 已交卷待复核再次 `start_exam` → 409（修复前：返回可续答卷面）；
9. 迁移中断（已 RENAME 未回填）→ 自动恢复全部行；模拟 INSERT 失败 → 事务回滚、数据不丢；
10. 只答一道简答题的用户，两条统计刷新路径结论一致（修复前相反）；
11. **迁移链集成验证**：对开发库 `data/training.db` 的**副本**按启动顺序（`init_db.py` +
    `migrate_*.py` 全量）连跑 3 轮 → 全部成功、幂等；`stats_user_daily` 的考试类列被移除、
    索引按模型重建、`max_questions_per_exam` 行被清理；`PRAGMA integrity_check` = ok、
    `foreign_key_check` 为空、用户/题目/考试行数不变。**正是这一步发现了上面两个迁移缺陷**
    （合成用例覆盖不到真实库的索引与 schema 组合）。

## 六、产品决策（2026-09-24 已确认）

**`GET /api/admin/exam-results?outcome=failed` 包含未公布/待复核的成绩 —— 保持现状，属有意为之。**

`passed` 只在公布时被置 True，因此 `passed IS FALSE` 天然包含「尚未公布」，与 `outcome=pending`
刻意重叠：管理员需要一屏看到「所有目前未通过的人」，包括还没复核完的。

已在代码与测试中显式记录该决策（`services/exam/admin.list_results` 的 docstring、
`tests/test_admin_status_filters.py` 的计数断言注释），避免下次复查再被误报为缺陷。
若日后改为只统计**已公布**的不及格：在 `list_results` 的 `failed` 分支加
`ExamResult.published.is_(True)`，并把该断言从 2 改为 1。

## 七、附录：对上轮报告的更正

`backend_review_2026-09-23.md` 第二节称「仅 5 项未落地」，该结论不成立：其复查按「文件是否被
改动过」筛选候选，**粒度到文件**，因此任何因其它原因被改动过的文件都被默认视为已修复。
本轮逐条定位到行后确认下列条目当时仍未落地（均已在本次修复）：

- 4.2 P1#3（并发建号 500）、P1#6（分组删除全表加载，且存在 fail-open）、P1#7（文件体积）、
  P1#18（死配置）、P1#21（判断题布尔单元格）、P1#22（填空空位数）、P1#23（拖拽重复左项）。

**下次沿用判据**：每条结论必须指出修复代码所在的**具体行**，而不是「文件被改动过」。
