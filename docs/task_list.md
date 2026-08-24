# 培训考试平台 开发任务清单 (task_list.md)

> 状态：待开发
> 依据：requirement.md、ui_ux_specifications.md、architecture_design_document.md
> 标记：`[ ]` 待开发 / `[x]` 已开发 / `[~]` 跳过(说明原因)

> 实施顺序按依赖链组织：基础设施 → 认证 → 组织 → 题库 → 练习 → 考试 → 面板/排行 → 系统管理 → 复核/审计 → 打磨。

---

## M1：项目骨架与基础设施
- [x] M1.1 后端骨架：`pyproject.toml`(uv)、目录结构、`main.py`、`database.py`(SQLAlchemy engine + WAL)、`config.py`(settings 读取)
- [x] M1.2 前端骨架：`package.json`(npm)、Vite、Vue3+TS、Element Plus、Pinia、axios 封装、router（角色分流基座）
- [x] M1.3 `scripts/init_db.py`：建表、默认 settings、超管账号
- [x] M1.4 `start.sh` 一键启动（构建前端 + 起后端 + 静态托管）
- [x] M1.5 跨域 + `/api` 优先路由 + 前端 SPA fallback

## M2：认证模块
- [x] M2.1 `users`/`email_verifications` 模型、schema
- [x] M2.2 `core/security.py`：bcrypt、JWT(HS256)、验证码生成
- [x] M2.3 `core/deps.py`：角色权限依赖、部门数据范围过滤
- [x] M2.4 `auth_service.py` + `api/auth.py`：register→发验证码(BackgroundTasks)、verify激活、login、me、resend、change-password
- [x] M2.5 前端：登录/注册/验证码激活页，token 存储，路由守卫

## M3：组织与分组
- [x] M3.1 `groups`/`user_groups` 模型、schema
- [x] M3.2 `api/groups.py`：树形查询、增删改（父子层级、type）
- [x] M3.3 前端：分组管理树形页

## M4：用户管理 (管理端)
- [x] M4.1 `api/users.py`：用户列表(筛选/分页)、审批、禁用/启用、重置密码、分配分组
- [x] M4.2 前端：用户列表页、审批/禁用对话框、分组分配

## M5：题库管理
- [x] M5.1 `question_banks`/`questions`/`question_tags` 模型、schema（answer JSON 多态）
- [x] M5.2 `utils/excel.py`：模板生成 + 导入解析（6 类题型 + 说明 Sheet + 转换 Prompt）
- [x] M5.3 `import_service.py`：preview（前 20 题预览 + 统计 + 错误报告）、import（落库 + 进度）、error-report 下载
- [x] M5.4 `api/questions.py` + `upload.py`：题目 CRUD/筛选、模板下载、preview、import
- [x] M5.5 前端：题目列表(筛选)、题目编辑表单、上传题库步骤式（下载/选分组银行/上传预览/错误报告/确认导入）

## M6：判分与规则组卷
- [x] M6.1 `services/grading.py`：6 类题型判分（多选/拖拽无部分分、填空大小写不敏感、简答返回 None）
- [x] M6.2 `services/paper_service.py`：规则组卷（题型/难度配比、来源筛选、种子、重复抽题）、preview-paper、保存 paper_templates

## M7：练习模式 (用户端)
- [x] M7.1 `practice_records`/`question_states` 模型、schema
- [x] M7.2 `api/records.py`：modes 统计、start(sequence/random/type/wrong/mark)、answer(实时判分+落库)、progress、recent、toggle-mark、short-eval(掌握自动移出错题本)
- [x] M7.3 前端答题页：三栏布局、导航网格(颜色编码)、6 类题型交互、标记、底部操作栏、简答自评；AnswerModes 模式选择；Panel 面板

## M8：考试模块
- [x] M8.1 `exam_definitions`/`exam_sessions`/`exam_results`/`exam_questions` 模型、schema
- [x] M8.2 `exam_service.py`：start(建会话+固化题)、answer(乐观锁 version)、submit(结算客观题 + need_review + published 流程)、session_detail(断点续答)、list_results
- [x] M8.3 `api/exams.py`：available、start、answer、submit、result、session detail；管理端 exams CRUD/publish、mock-config、exam-results
- [x] M8.4 前端：ExamList 考试卡片列表+模拟入口、ExamTaking 倒计时(变红/自动交卷)/答题卡/乐观锁、ExamTemplates 试卷模板、MockConfig、Exams 正式考试管理、ExamRecords 成绩列表

## M9：简答复核
- [x] M9.1 `short_answer_reviews` 模型、schema
- [x] M9.2 `review_service.py` + `api/exams.py`：pending 列表、单题复核(pass/fail/partial)、publish-results
- [x] M9.3 前端：Review.vue 复核列表页、复核操作(pass/partial/fail)、公布成绩

## M10：面板与排行
- [x] M10.1 `stats_service.py`：每日预聚合(stats_user_daily)、手动刷新、启动触发
- [x] M10.2 `api/panel.py`：用户端/管理端面板指标 + 待办 + 手动刷新
- [x] M10.3 `api/panel.py` `rank`：四维度(accuracy/count/score/streak)×scope(self/group)×时间范围，命中预聚合表 + Top10 + is_me
- [x] M10.4 前端：用户端 Panel(统计卡/进度/最近考试/快捷)、管理端 Overview(指标卡/待办/刷新)、Rank(维度+范围切换)

## M11：系统管理
- [x] M11.1 `settings` 模型、schema、SMTP 密码 Fernet 加密（已有）
- [x] M11.2 `services/mail_service.py`：注册验证码/考试通知/复核通知(模板)、SMTP 测试邮件（已有）
- [x] M11.3 `api/system.py`：settings CRUD(分类)、SMTP 测试、注册与审批设置；categories 元信息
- [x] M11.4 前端：基础设置/SMTP配置+测试/注册审批/邮件模板 Tab 式设置页

## M12：审计日志与草稿
- [x] M12.1 `audit_logs`/`drafts` 模型、schema（已有）
- [x] M12.2 `services/audit_service.py`：管理员写操作自动记录（user/group/question/exam/review/settings 全覆盖）
- [x] M12.3 `api/audit.py`：审计日志查询(筛选/分页) + 草稿接口(get/put/delete)；前端 Audit.vue 审计日志页(筛选)
- [x] M12.4 前端 `composables/useDraft.ts`：长表单草稿自动保存(localStorage 10s + 后端持久化 + 离开提示)

## M13：体验打磨与收尾
- [x] M13.1 空状态、404/403/500 错误页（NotFound 完善 + 各页 el-empty 空状态）
- [x] M13.2 表单校验与反馈规范统一落实（ElMessage + el-form rules）
- [x] M13.3 主题色 CSS 变量动态注入（useTheme composable + /system/site 公开接口）
- [x] M13.4 `deployment.md` 部署文档
- [x] M13.5 后端单元测试(grading 18 / paper 6 / excel 6，共 30 用例全部通过)

## 优先级映射
- P0：M1–M11（核心全链路）
- P1：M12 草稿、M13 测试与打磨、密码策略/会话超时/备份导出

## M14：移动端响应式适配（2026-08-24 增量）
- [x] M14.1 响应式基础设施：`composables/useResponsive.ts`（matchMedia 断点 isMobile/isTablet/isDesktop，与 CSS @media 一致）；`assets/main.css` 追加全局手机端规则（el-dialog/el-message-box 自适应、分页器只留 prev/next、触控目标、工具栏 wrap、卡片化公共类 `.mobile-card-list`）
- [x] M14.2 布局改造：UserLayout 手机端隐藏水平菜单改汉堡+el-drawer 抽屉；AdminLayout 手机端隐藏固定侧栏改抽屉式；header/main 内边距随断点收紧
- [x] M14.3 答题/考试分栏页堆叠：Answer.vue、ExamTaking.vue 手机端 `.layout` 上下堆叠，导航网格/答题卡可折叠、题目区优先
- [x] M14.4 管理端列表页卡片化：Questions/Users/Groups/ExamRecords/Audit/Exams/ExamTemplates 手机端渲染 `.mobile-card-list` 卡片视图替代表格，分页器 `:small`
- [x] M14.5 用户端其余页面适配：Panel(统计卡/快捷入口单/双列)、Wrong/Marks(卡片折行)、Rank(工具栏 wrap)、ExamList(模拟卡/考试卡单列)、AnswerModes(模式卡双列)、登录/注册/验证(卡片 max-width 自适应)、Overview/Settings/MockConfig/Review/Upload 收紧
- [x] M14.6 构建验证：`vue-tsc --noEmit` 通过(0 错误)、`vite build` 通过(8.94s)，无回归
- [x] M14.7 排行可见性开关：`system_service.DEFAULT_SETTINGS` 新增 `rank_visible`(general, bool, 默认 true)；`/system/site` 公开接口返回 `rank_visible`；`/rank` 接口对普通用户在关闭时 403；前端 site store 同步 `rank_visible`，UserLayout 菜单/抽屉按 `v-if` 隐藏排行入口，router.beforeEach 拦截直链访问；后台「基础设置」出现「排行榜对用户可见」开关
- [x] M14.8 手机端导航重设计：新增底部固定导航栏（首页/答题/考试/错题/我的），比汉堡按钮更直观可见；汉堡+抽屉保留为次级入口（标记/排行/管理后台等）；main 底部留 72px 避让导航栏

## M15：用户手动新增与批量导入（2026-08-25 增量）
- [x] M15.1 重置密码修复：`api/user.ts` `resetPassword` 改为传 `{}` body（后端 `ResetPasswordIn` 为必填 body，无 body 触发 422）；`Users.vue` 重置确认文案由「重置为 123456」改为「生成随机密码」并用 `ElMessageBox.alert` 展示新密码
- [x] M15.2 后端用户创建：`user_service.create_user`（邮箱唯一校验、角色/状态白名单、分组合法性校验、随机强密码、email_verified 直通、审计）；`api/users.py` 新增 `UserCreateIn` schema 与 `POST /admin/users`（创建管理员账号限超级管理员）
- [x] M15.3 后端 Excel 批量导入：`utils/user_excel.py` 模板生成（邮箱/姓名/角色/初始密码/状态/分组ID + 说明 Sheet）+ 解析预览（中英文角色/状态归一化、分组ID逗号分隔、单行错误收集、token 暂存复用 import_service 模式、防 IDOR）；`api/users.py` 新增 `GET /admin/users/import/template`、`POST /admin/users/import/preview`、`POST /admin/users/import`
- [x] M15.4 后端导入落库：`user_service.import_users` 批量插入（单行失败不中断、文件内邮箱去重、分组关联回填、返回成功用户明文密码便于管理员告知）
- [x] M15.5 前端：`api/user.ts` 新增 create / importTemplate / importPreview / doImport 与类型；`Users.vue` 工具栏新增「新增用户」「批量导入」按钮，新增用户对话框（邮箱/姓名/角色/初始密码/状态/分组树多选），批量导入步骤式对话框（下载模板→上传预览→确认导入，含错误清单与生成密码清单）
- [x] M15.6 端到端验证：新增用户 201（随机密码返回）/ 重复邮箱 400 / 创建管理员权限校验；重置密码 `{}` body 200；模板下载；预览解析（含错误行）；确认导入成功+失败统计+生成密码清单；库校验分组与 email_verified；`vue-tsc --noEmit` 通过、`vite build` 通过（9.22s），无回归
