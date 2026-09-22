# 模拟考试重构设计方案（v2：取消后台管理，完全用户自助）

> 状态：**已实施**（2026-09-17 完成，见文末实施记录）
> 目标版本：2026-09 迭代
> 关联文档：`docs/requirement.md` 4.5、`docs/ui_ux_specifications.md` 3.2/5.5、`docs/audit_report.md` FUNC-P1-4
> **v2 变更**：取消后台「模拟考试设置」页面与 `mock_config` 全局配置，模拟考试的题库范围、题量、题型比例**全部由用户开考前自由设置**。

---

## 1. 需求变更说明

v1 方案的思路是"管理员设边界、用户选范围"。v2 改为**彻底取消后台配置层**：管理员不再有任何模拟考试相关配置项，`/admin/mock-config` 接口与「模拟考试设置」页面整体下线，组卷三要素（题库、题量、题型比例）全部开放给用户。

### 1.1 变更带来的真实风险（必须先解决）

取消管理员配置层，会**顺带取消它承载的几项约束**。这些约束不是"配置"，而是安全与产品口径的防线，必须在用户自助形态下重建：

| 原由 `mock_config` 承载 | 取消后的问题 | v2 对策 |
|---|---|---|
| `bank_ids` 预筛范围 | 用户可任意指定 `bank_ids` | **改为硬校验 ⊆ `enabled_bank_ids(db)`**（关闭练习的题库一律不可选，静默剔除） |
| `max_questions` 上限 | 用户可请求 100000 题拖垮服务 | **新增用户级硬上限** `MOCK_MAX_QUESTIONS`（常量，默认 100），不可被请求参数放宽 |
| `type_quota` 题型配额 | 用户可指定任意题型与数量 | 允许自由设置，但**逐题型钳制到可用题量**，且总和受硬上限约束 |
| `duration_min` / `pass_score` | 失去来源 | 回退到系统设置 `default_exam_duration_min` / `default_pass_score`（与现状一致） |
| `show_analysis` | 失去来源 | 用户开考前自行勾选（练习性质，默认开启） |

> ⚠️ **本次重构最容易被忽略的一点**：`enabled_bank_ids` 这道"关闭练习的题库不可练"的防线，此前是靠 `_restrict_mock_config_to_enabled_banks()` 兜住的。取消配置层时若一并删掉这段收敛逻辑，用户就能通过模拟考试练到标注为「仅考试使用」的题库——**开关将彻底形同虚设**。这是 v2 必须保留并强化的第一红线（见 §5.1）。

### 1.2 另一处必须新建的能力

管理员配置层原本还顺带提供了"哪些题库可选"的信息源。取消后，**用户端没有任何接口能拿到题库列表与题型题量统计**——`/admin/question-banks` 与 `/admin/question-type-stats` 全部是 `require_admin`（`api/questions.py:31/105`）。而用户要自由设置题型比例，就必须先知道"这个题库里各题型各有多少题"。

因此 v2 需要**新增用户端专用的题库与题型统计接口**，且该接口必须自带范围收敛（只返回开放练习的题库、只统计开放范围内的题目）。

---

## 2. 方案总览

### 2.1 交互形态：开考前一个"自由设置"面板

用户端 `ExamList.vue` 点击「开始模拟考试」→ 打开设置对话框（取代原 `MockConfig.vue` 的后台配置）：

```
┌─ 模拟考试设置 ───────────────────────────────────────┐
│                                                      │
│  题库范围  ☑ 全选题库                                 │
│            ☑ 网络安全基础（120 题）                   │
│            ☐ HCIA-Datacom（200 题）                   │
│            ☐ 数据库原理（80 题）                      │
│            └ 已选 1 个题库，共 120 题                 │
│                                                      │
│  题量      [10] [20] [30] [50] [100]  或自定义 [30]   │
│                                                      │
│  题型比例  ┌──────────┬────────┬──────────┐          │
│            │ 单选题   │ 多选题 │ 判断题  │  ...     │
│            │ 120 可用 │ 30可用 │ 50 可用  │          │
│            │ [ 20 ]   │ [ 5 ]  │ [ 5 ]    │          │
│            └──────────┴────────┴──────────┘          │
│            [ 按可用题量自动分配 ] [ 清空 ]            │
│            合计 30 题 ✓ 与设定题量一致                │
│                                                      │
│  ☐ 仅客观题（跳过简答题）                             │
│  ☑ 交卷后回显答案解析                                 │
│                                                      │
│  预计：30 题 / 90 分钟 / 60 分                        │
│                          [取消]  [开始考试]           │
└──────────────────────────────────────────────────────┘

校验失败态（禁用「开始考试」）：
│            合计 25 题 ⚠ 与设定题量 30 不一致，还差 5 题│
```

**设计取舍（对齐"简单快速"）**：默认态**什么都不用改**即可开考——题库默认全选、题量默认 30、题型比例默认「按可用题量自动分配」，合计自动等于 30，一进对话框就是可提交状态。想精细控制的用户才改动题型数字（改动后受 §2.2 ③ 的一致性校验约束）。三个要素既"可自由设置"，又不需要用户每次都逐项设置。

### 2.2 三要素的行为定义

**① 题库范围（多选，可自由设置）**
- 默认全选所有开放练习的题库；
- 可勾选任意子集（支持单选一个库 = 主题一致的专项模拟），也可只选部分库；
- 服务端**强制**与 `enabled_bank_ids(db)` 取交集，请求里的非法 id 静默剔除；剔除后为空 → 回退为全部开放题库。

**② 题量（档位 + 自定义）**
- 档位 `[10, 20, 30, 50, 100]` 单击即选，默认 30；
- 保留自定义数字输入，上限为硬上限 `MOCK_MAX_QUESTIONS = 100`；
- **在手动配额模式（③-b）下，题量是"校验目标"而非独立约束**：用户手填的各题型数量之和必须等于题量，否则报错；实际出题量由手填数量决定（见 ③-b）。

**③ 题型比例（自动为默认，可手动修改）**

用户在「按可用题量自动分配」的基础上可随时手动改动任一题型数字：

- **自动分配（默认初始值）**：按范围内各题型可用题量的**比例**分配，`quota[type] ∝ avail[type]`，最大余数法取整。题库里哪种题型多，卷子里就多——卷面构成贴近题库真实构成。
  例：范围 30 单选 / 10 多选，题量 40 → 自动填入 `单选 30、多选 10`。
- **手动修改**：用户可直接改动表格里任一数字（也可点「按可用题量自动分配」重新回填）。
  规则如下：
  1. **逐题型钳制**：`quota[type] = min(用户填值, avail[type])`，超出可用量的部分不生效，UI 即时红字提示"超出可用 N 题"；
  2. **总和必须等于题量**：`Σquota == size` 才允许提交。不等时前端禁用「开始考试」按钮并提示差额（如"当前 25 题，与设定题量 30 不一致，还差 5 题"）；服务端同样校验，不一致返回 400；
  3. **不自动补足、也不静默缩减**：用户填多少就是多少（受 1 的钳制）。改了题量档位不会覆盖用户已手填的题型数字，只会让第 2 条校验失败并提示用户调整——**避免系统悄悄改掉用户手填的值**。

> **边界：范围内题量本身不足时**。若 `Σavail < size`，用户无论怎么填都无法让总和等于题量。此时 UI 在题量输入框处直接提示"该范围共 N 题，最多可出 N 题"，并**自动把题量下调到可满足的最大值**（例如范围共 18 题、用户选了 30 → 题量自动变为 18），使用户仍能满足"总和 == 题量"。该自动下调只在题量档位**超过范围内总题量**时发生，且有明确文案说明，不会与用户手填的题型数字冲突。

**④ 仅客观题开关（新增）**
对话框提供一个「仅客观题（跳过简答题）」开关，勾选后：
- 自动分配模式下，将简答题权重置 0，其余题型按比例重分配；
- 手动模式下，把简答题填值清零，并按其余题型可用量比例重新分配到总和等于题量；
- 等价于用户手动把简答题设为 0，但省去手动调整其余题型的操作成本。

### 2.3 数据模型

**不新增表**。用户开考时的选择快照固化进 `ExamDefinition.rules`（JSON 列）：

```jsonc
// ExamDefinition(type=mock).rules
{
  "bank_ids": [3],                    // 用户选定的范围
  "requested_size": 30,               // 用户请求的题量
  "type_quota": { "单选题": 12, "多选题": 8, "判断题": 10 },  // 最终配额，Σ == requested_size
  "allocation": "manual",             // auto | manual（记录用户是否手改过，仅用于回填 UI）
  "objective_only": true,             // 是否勾选「仅客观题」
  "show_analysis": true
}
```

**按 (user, 范围, 题量, 配额) 复用 mock 定义。**
现状是"每个用户最多一个 ongoing mock 定义"，引入用户自选维度后必须细化，否则用户换库/换题量后会拿回旧定义的固化试卷。复用键为 `(created_by, bank_ids排序, requested_size, type_quota, allocation, objective_only)` 的规范化 JSON 比较。

- 键相同 → 复用，继续上次未完成的作答；
- 键不同 → 新建定义；
- 旧的、**无任何作答记录**的 ongoing mock 定义在新建时清理，避免 `exam_definitions` 无界增长（FUNC-P1-4 的教训）；有作答的保留。

### 2.4 接口变更

| 接口 | 变更 | 说明 |
|---|---|---|
| `GET /admin/mock-config` | **删除** | 后台配置下线 |
| `PUT /admin/mock-config` | **删除** | 同上 |
| `GET /mock/banks` | **新增**（用户端） | 返回**开放练习**的题库列表（含题数、各题型题量）。取代原本管理员才能访问的 `/admin/question-banks` + `/admin/question-type-stats` |
| `POST /mock/preview` | **新增**（用户端） | 入参 `{bank_ids, size, type_quota?, allocation, objective_only?}` → 返回 `{count, total_score, type_dist, avail_by_type, avail_total, quota_sum, quota_valid, truncated}`，供对话框实时预览与校验。不落库 |
| `POST /exams/mock/start` | **改签名** | 入参改为 `{bank_ids, size, type_quota?, allocation, objective_only?, show_analysis?}`，全部可选并带默认值（向后兼容）。`Σtype_quota != size` 时返回 400 |

**为什么把两个 admin 接口合并成一个 `/mock/banks`**：用户只需要"可见题库 + 题量统计"这一件事，且必须强制走 `enabled_bank_ids` 收敛。直接放开 admin 接口权限会连题库的 `practice_enabled` 管理能力一起暴露，不可取。

### 2.5 后台改动：整体下线

| 位置 | 改动 |
|---|---|
| `frontend/src/views/admin/MockConfig.vue` | **删除文件** |
| `frontend/src/router/index.ts:37` | 删除 `admin-mock-config` 路由 |
| `frontend/src/layouts/components/AdminMenu.vue:18` | 删除「模拟考试设置」菜单项 |
| `frontend/src/api/exam.ts` | 删除 `getMockConfig` / `saveMockConfig` |
| `backend/app/api/exams.py` | 删除两个 `/admin/mock-config` 路由 |
| `backend/app/services/exam_service.py` | 删除 `save_mock_config` / `get_mock_config_full`；`start_mock_exam` 重写为接收用户 spec |
| `settings` 表中的 `mock_config` 行 | **保留不删**（历史留痕），代码不再读取 |

---

## 3. 后端改动清单

| 文件 | 改动 |
|---|---|
| `app/services/paper_service.py` | 新增 `allocate_quota(db, bank_ids, size, strategy, manual_quota)`：统计 `avail[type]` → 自动分配或校验手填 → 钳制与回流 → 返回最终配额。`generate_paper` 增加"直接吃现成 `type_quota`"的路径（已有），无需改动其抽题逻辑 |
| `app/services/exam_service.py` | 重写 `start_mock_exam(db, user, spec)`；新增 `mock_banks(db)`、`preview_mock_paper(db, user, spec)`；新增 `_resolve_mock_scope(db, requested_bank_ids)` 取代 `_restrict_mock_config_to_enabled_banks`，语义为「请求 ∩ 开放题库，空则回退全部开放题库」；定义复用键逻辑按 §2.3 细化 |
| `app/schemas/exam.py` | 新增 `MockStartIn`、`MockPreviewIn`（`extra="forbid"`）。**顺带收口**：删除 `MockConfigIn`（裸 `dict`，`schemas/exam.py:112-114`，与 `PaperPreviewIn` 修复过的问题同源），随接口一并消失 |
| `app/api/exams.py` | 删除 admin mock 路由；新增 `GET /mock/banks`、`POST /mock/preview`；`POST /exams/mock/start` 接入 `MockStartIn` |
| 常量 | `MOCK_SIZE_PRESETS = (10,20,30,50,100)`、`MOCK_DEFAULT_SIZE = 30`、`MOCK_MAX_QUESTIONS = 100` |
| `app/services/stats_service.py` | **补充**面板"模拟考试平均分"（需求 4.6/3.2 已承诺但未实现，见 §7） |

**不改动**：`ExamSession`/`ExamQuestion`/`ExamResult` 表结构、结算与复核链路、`list_available` 的 `type == "formal"` 过滤、按 `created_by` 的隔离。

---

## 4. 前端改动清单

| 文件 | 改动 |
|---|---|
| `views/user/ExamList.vue` | 「开始模拟考试」改为打开 `MockExamSetupDialog`；卡片描述文案更新为"自由选择题库与题量" |
| `components/MockExamSetupDialog.vue` | **新增**：题库多选 + 题量档位/自定义 + 题型比例（自动/手动切换）+ 回显解析开关 + 实时预览 |
| `components/TypeQuotaEditor.vue` | **改造复用**：现组件依赖 admin 的 `questionApi.typeStats`，改为接收上层传入的 `availByType`（数据来自 `/mock/banks`），从而同时服务用户端对话框 |
| `api/exam.ts` | 删除 mock config 两个方法；新增 `mockBanks()` / `previewMock()`；`startMock(payload)` 改带参 |
| `router/index.ts`、`AdminMenu.vue` | 删除模拟考试设置入口（见 §2.5） |
| `views/user/ExamTaking.vue` | 无需改动 |

---

## 5. 安全与数据隔离（必须保持的防线）

取消管理员配置层后，以下每条都要有回归测试：

1. **关闭练习的题库不可选**：`bank_ids` 强制与 `enabled_bank_ids(db)` 取交集。用户传已关闭题库 id 时**静默剔除**（不报错，避免探测题库状态）。**这是 v2 的第一红线**——原防线由 `_restrict_mock_config_to_enabled_banks` 提供，重构时极易被连带删除。
2. **题量硬上限**：`MOCK_MAX_QUESTIONS = 100`，服务端常量校验，不受请求参数影响；同时 `generate_paper` 既有的 `max_questions ∈ 1~1000` 校验保留为第二道。
3. **题型配额钳制与一致性**：用户手填的 `type_quota` 逐题型 `min(quota, avail[type])`；键必须在 `QUESTION_TYPE` 白名单内（防止伪造题型名绕过配额）；且 `Σquota == size` 必须成立，否则 400。**服务端不信任前端的校验结果**，`/mock/preview` 与 `/exams/mock/start` 必须复用同一个校验函数，避免两侧口径漂移。
4. **按用户隔离**：mock 定义查询强制 `created_by == user.id`（沿用 `test_mock_exam_lookup_scoped_by_owner`），并提供多范围场景的扩展用例。
5. **不进可用列表**：`list_available` 继续只查 `type == "formal"`。
6. **不进排行榜**：排行榜与日均统计继续只纳入 `published` 的正式考试成绩。
7. **新用户端接口的范围收敛**：`/mock/banks` 只返回 `practice_enabled=True` 的题库及其题量，不泄露 `practice_enabled` 为 false 的题库存在性与题量。
8. **定义不堆积**：按 §2.3 清理无作答的旧定义（FUNC-P1-4 回归）。
9. **深链防护**：`start_mock_exam` 不信任前端，所有校验在服务端重做（预览与实际开考必须走同一套校验函数，避免两侧口径漂移）。
10. **超时与并发**：`_is_overtime`、乐观锁 `submit_answer`、`scoring` 回收逻辑**完全不动**。

---

## 6. 测试计划

新增/扩展 `backend/tests/`：

| 用例 | 断言 |
|---|---|
| `test_mock_scope_excludes_disabled_bank` | 请求里显式传已关闭题库 id → 被剔除，卷面无该库题目（扩展既有用例） |
| `test_mock_scope_user_selected` | 选定单库 → 卷面 100% 来自该库 |
| `test_mock_size_hard_cap` | `size=100000` → 400 或钳制到上限；`size=0`/负数 → 400 |
| `test_mock_quota_auto_proportional` | 单 30 / 多 10 的库，题量 40 → 自动配额为 单选 30、多选 10 |
| `test_mock_quota_manual_clamped` | 手填单选题 999 题（仅 30 可用）→ 钳制到 30，不越界 |
| `test_mock_quota_sum_must_equal_size` | 题量 30、手填合计 25 → 400（前后端一致） |
| `test_mock_quota_sum_equals_size_ok` | 题量 30、手填合计 30 → 正常出题，卷面题型分布与手填一致 |
| `test_mock_quota_unknown_type_rejected` | 伪造题型名 → 400 |
| `test_mock_objective_only_switch` | 勾选「仅客观题」→ 卷面无简答题；自动模式下其余题型按比例重分配且总和不变 |
| `test_mock_size_auto_downgraded_when_insufficient` | 范围仅 18 题、请求题量 30 → 题量自动下调为 18，可正常开考（不再有 truncated 静默缩水路径） |
| `test_mock_quota_adapts_to_bank_composition` | 只有单选题的库题量 30 → 自动分配实得 30 题全为单选（不缩水成 10 题） |
| `test_mock_definition_reused_per_spec` | 同用户同 spec → 复用；换范围/换题量 → 新建，旧的无作答定义被清理 |
| `test_mock_isolation_across_users` | 用户 A 的定义不被 B 复用（含多范围场景） |
| `test_mock_banks_endpoint_only_enabled` | `/mock/banks` 不含关闭练习的题库 |
| `test_mock_not_in_available_list` | mock 定义不出现在 `/exams/available` |
| `test_mock_avg_score_in_panel` | 面板模拟考试平均分口径正确，不含未发布成绩 |
| `test_admin_mock_config_removed` | `/admin/mock-config` 返回 404（路由已下线） |

扩展 `test_paper.py` 覆盖 `allocate_quota` 边界（配额 0、`N > Σavail`、单题型、余数分配、手填超额回流）。

---

## 7. 顺带修复：模拟考试平均分

需求 4.6 与 UI 规范 3.2 均承诺面板展示"模拟考试平均分"，但全仓库无实现。本次一并补齐：`stats_service.user_panel()` 新增聚合 `AVG(ExamResult.score / ExamResult.total_score * 100)`，条件为 `user_id == me` + 所属定义 `type == "mock"` + `published == True`，归一化为百分制。仅展示于本人面板，**不进入排行榜**；无记录显示 `—`。

---

## 8. 迁移与兼容

- **无表结构变更**，不需要 `scripts/migrate_*.py`。
- **`settings.mock_config` 行保留**：代码不再读取，历史配置留痕，避免不可逆删除。
- **存量 ongoing mock 定义**：其 `rules.bank_ids` 是历史展开值，新逻辑下会被当作"用户选定范围"。建议在启动维护路径中将其置为 `archived`（有作答的会话与成绩保留不动），让用户下次开考走新流程。
- **接口兼容**：`POST /exams/mock/start` 的 payload 字段全部可选并带默认值（默认全库 / 30 题 / 自动分配），旧前端即使不带参也不会 400。
- **文档同步**：`docs/requirement.md:59`、`docs/ui_ux_specifications.md:159`（模拟考试设置页整节删除）、管理端菜单结构图（`ui_ux_specifications.md:132-140` 的「模拟考试设置」节点）均需更新。

---

## 9. 已确认的决策（v2 定稿）

| # | 决策项 | 结论 |
|---|---|---|
| 1 | 题库范围 | **可自由设置**（多选，默认全选）；服务端强制收敛到开放练习题库 |
| 2 | 题型比例 | **默认按可用题量自动分配，但用户可手动修改任一题型数字** |
| 3 | 题量硬上限 | `MOCK_MAX_QUESTIONS = 100` |
| 4 | 手填配额不足时 | **不补足**：`Σ手填 == 题量` 才允许开考，不等则前后端一致报错并提示差额 |
| 5 | 「仅客观题」开关 | **要**，一键清零简答题并按其余题型可用量比例重分配 |

**由决策 4 派生的关键交互（需在实现中落实）**：

- 题量档位与题型表格**不是两个独立约束**。手动模式下题量是"校验目标"：改题量后若与手填总和不符，禁用「开始考试」并提示差额，**不覆盖用户已填的题型数字**。
- 范围内总题量本身不足时，题量输入框提示"该范围共 N 题"，并**自动把题量下调到 N**，使用户仍能满足"总和 == 题量"；这是唯一的自动改值场景，且有明确文案说明。


---

## 10. 实施记录（2026-09-17）

### 后端

| 文件 | 改动 |
|---|---|
| `app/services/paper_service.py` | 新增 `allocate_quota()` / `largest_remainder()` / `_clamp_and_redistribute()` |
| `app/services/exam_service.py` | 新增 `MOCK_SIZE_PRESETS`/`MOCK_MAX_QUESTIONS` 等常量、`resolve_mock_scope()`、`mock_banks()`、`build_mock_spec()`、`preview_mock_paper()`、`_mock_rules_key()`、`_cleanup_stale_mock_defs()`；重写 `start_mock_exam()`；删除 `save_mock_config()`/`get_mock_config_full()` |
| `app/schemas/exam.py` | 新增 `MockPaperIn`/`MockStartIn`；删除 `MockConfigIn` |
| `app/api/exams.py` | 新增 `GET /exams/mock/banks`、`POST /exams/mock/preview`；`POST /exams/mock/start` 改带参；删除两个 `/admin/mock-config` 路由 |
| `app/services/stats_service.py` | 补 `mock_avg_score`/`mock_attempts`；`recent_exams` 增加 `type`/`session_id` |

### 前端

| 文件 | 改动 |
|---|---|
| `components/MockExamSetupDialog.vue` | 新增（题库多选 + 题量档位 + 题型比例 + 仅客观题 + 实时预览 + 一致性校验） |
| `constants/question.ts` | 新增（题型常量与题量档位） |
| `views/user/ExamList.vue` | 接入设置对话框 |
| `views/user/Panel.vue` | 次级卡行展示模拟考试平均分 / 正式考试最高分 |
| `api/exam.ts` | 新增 `mockOptions`/`previewMock`；`startMock` 改带参；删除 mock config 方法 |
| `views/admin/MockConfig.vue` | **删除** |
| `router/index.ts`、`layouts/components/AdminMenu.vue` | 删除模拟考试设置入口 |

### 验证结果

- 后端 `pytest`：**184 passed**（含新增 `tests/test_mock_exam_selfservice.py` 25 项）
- `ruff check` / `ruff format --check`：通过
- 前端 `vue-tsc` / `eslint` / `vite build`：通过；构建产物无 `admin/mock-config` 残留
- 端到端 HTTP 验证 8 项：全部通过（可选题库不漏关闭库、预览与实考题型一致、手填不一致 400、仅客观题无简答、关闭库被剔除、题量超限 400、后台配置已下线）

### 与方案的偏差

1. `allocate_quota` 的 `allocation` 参数只接受 `proportional`/`even`；手填路径由 `manual_quota` 非空触发，不再传入 `"manual"` 作为策略值（实现时发现的口径混淆）。
2. `TypeQuotaEditor.vue` **未改造**：它仍被管理端「试卷模板」「正式考试」使用（依赖 admin 的 `typeStats`），故保留原样；模拟考试对话框使用自己的题型表格。
3. `docs/ui_ux_specifications.md` 的「模拟考试设置」整节从管理端移至用户端 §3.4。
