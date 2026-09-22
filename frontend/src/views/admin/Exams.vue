<template>
  <div class="exams" v-loading="loading">
    <div class="toolbar">
      <span class="title">正式考试管理</span>
      <div class="filters">
        <!-- 状态筛选：默认全部（含已归档），便于核对归档考试 -->
        <el-select v-model="statusFilter" placeholder="状态" clearable style="width: 140px" @change="load">
          <el-option label="草稿" value="draft" />
          <el-option label="已发布" value="published" />
          <el-option label="进行中" value="ongoing" />
          <el-option label="已结束" value="ended" />
          <el-option label="复核中" value="reviewing" />
          <el-option label="已归档" value="archived" />
        </el-select>
        <el-button type="primary" @click="openCreate">发布考试</el-button>
      </div>
    </div>

    <el-table :data="rows" border v-loading="loading" class="mobile-table-hidden">
      <el-table-column prop="name" label="考试名称" min-width="160" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时段" min-width="240">
        <template #default="{ row }">
          {{ row.start_at ? formatDateTime(row.start_at) : '不限' }} ~
          {{ row.end_at ? formatDateTime(row.end_at) : '不限' }}
        </template>
      </el-table-column>
      <el-table-column prop="duration_min" label="限时" width="80" />
      <el-table-column prop="pass_score" label="及格线" width="80" />
      <el-table-column label="题量" width="80">
        <template #default="{ row }">{{ row.total_questions }}</template>
      </el-table-column>
      <el-table-column label="操作" width="460" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="onEdit(row as ExamBrief)">编辑</el-button>
          <el-button size="small" type="success" v-if="row.status === 'draft'" @click="onPublish(row as ExamBrief)"
            >发布</el-button
          >
          <el-button size="small" @click="$router.push(`/admin/exam-records?exam=${row.id}`)">成绩</el-button>
          <el-button size="small" type="warning" @click="$router.push('/admin/review')">复核</el-button>
          <el-button size="small" type="success" @click="onPublishResults(row as ExamBrief)">公布成绩</el-button>
          <!-- 归档：对用户隐藏但保留成绩（已有作答记录的考试不能删，只能归档） -->
          <el-button size="small" v-if="row.status === 'archived'" @click="onUnarchive(row as ExamBrief)"
            >取消归档</el-button
          >
          <el-button size="small" v-else @click="onArchive(row as ExamBrief)">归档</el-button>
          <el-button size="small" type="danger" @click="onDelete(row as ExamBrief)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：考试卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title">
          {{ row.name }} <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </div>
        <div class="mc-row">
          <span class="mc-label">时段</span>{{ row.start_at ? formatDateTime(row.start_at) : '不限' }} ~
          {{ row.end_at ? formatDateTime(row.end_at) : '不限' }}
        </div>
        <div class="mc-row">
          <span class="mc-label">限时/及格</span>{{ row.duration_min }}分钟 · {{ row.pass_score }}分 ·
          {{ row.total_questions }}题
        </div>
        <div class="mc-actions">
          <el-button size="small" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" type="success" v-if="row.status === 'draft'" @click="onPublish(row)">发布</el-button>
          <el-button size="small" @click="$router.push(`/admin/exam-records?exam=${row.id}`)">成绩</el-button>
          <el-button size="small" type="warning" @click="$router.push('/admin/review')">复核</el-button>
          <el-button size="small" type="success" @click="onPublishResults(row)">公布成绩</el-button>
          <el-button size="small" v-if="row.status === 'archived'" @click="onUnarchive(row)">取消归档</el-button>
          <el-button size="small" v-else @click="onArchive(row)">归档</el-button>
          <el-button size="small" type="danger" @click="onDelete(row)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- 发布/编辑 -->
    <el-dialog v-model="dlg" :title="editing.id ? '编辑考试' : '发布考试'" width="680px">
      <el-form :model="editing" label-width="110px">
        <el-form-item label="考试名称"><el-input v-model="editing.name" /></el-form-item>
        <el-form-item label="组卷方式">
          <el-radio-group v-model="editing.source">
            <el-radio value="rule">规则组卷</el-radio>
            <el-radio value="template">套用模板</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="试卷模板" v-if="editing.source === 'template'">
          <el-select v-model="editing.paper_template_id" placeholder="选择模板" style="width: 100%">
            <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <template v-if="editing.source === 'rule'">
          <!-- 先定来源范围，再定题型配比：配比编辑器据此显示“每个题型可用多少题” -->
          <el-form-item label="来源题库">
            <el-select
              v-model="editing.rules.bank_ids"
              multiple
              filterable
              clearable
              placeholder="留空则不限（全部题库）"
              style="width: 100%"
            >
              <el-option v-for="b in banks" :key="b.id" :label="bankLabel(b)" :value="b.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="题型配比">
            <!-- 后端单题型配额上限 1000。不传 max-questions 时组件按 100 钳制，
                 打开已有大配额（如单选题 150）的编辑弹窗就会被静默改小并保存丢失。 -->
            <TypeQuotaEditor v-model="editing.rules.type_quota" :sources="examQuotaSources" :max-questions="1000" />
          </el-form-item>
          <el-form-item label="出题顺序">
            <el-select v-model="editing.rules.order_mode" style="width: 100%">
              <el-option v-for="m in ORDER_MODES" :key="m.value" :label="m.label" :value="m.value">
                <span>{{ m.label }}</span>
                <span class="tip" style="float: right">{{ m.hint }}</span>
              </el-option>
            </el-select>
          </el-form-item>
        </template>
        <el-form-item label="指派分组">
          <el-tree
            ref="groupTreeRef"
            :key="`gt-${dlgSeq}`"
            :data="groupTree"
            node-key="id"
            :props="{ label: 'name', children: 'children' }"
            show-checkbox
            :default-checked-keys="editing.group_ids || []"
            @check="onGroupCheck"
          />
        </el-form-item>
        <el-form-item label="开放时段">
          <div class="time-range">
            <el-date-picker
              v-model="editing.start_at"
              type="datetime"
              placeholder="开始时间（留空不限）"
              format="YYYY-MM-DD HH:mm"
              value-format="YYYY-MM-DDTHH:mm:ss"
            />
            <el-date-picker
              v-model="editing.end_at"
              type="datetime"
              placeholder="结束时间（留空不限）"
              format="YYYY-MM-DD HH:mm"
              value-format="YYYY-MM-DDTHH:mm:ss"
            />
          </div>
        </el-form-item>
        <el-form-item label="限时(分钟)"
          ><el-input-number v-model="editing.duration_min" :min="1" :max="600"
        /></el-form-item>
        <el-form-item label="及格线"><el-input-number v-model="editing.pass_score" :min="0" :max="100" /></el-form-item>
        <el-form-item label="最大尝试次数"
          ><el-input-number v-model="editing.max_attempts" :min="0" :max="10" />
          <span class="tip">0 = 不限</span></el-form-item
        >
        <el-form-item label="含简答题">
          <el-switch v-model="editing.need_review" /> <span class="tip">含简答时成绩需复核后公布</span>
        </el-form-item>
        <el-form-item label="即时出分">
          <el-switch v-model="editing.show_score_immediately" /> <span class="tip">关闭则交卷后不立即显示成绩</span>
        </el-form-item>
        <el-form-item label="交卷显示解析">
          <el-switch v-model="editing.show_analysis" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ editing.id ? '保存' : '发布' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, type TreeInstance } from 'element-plus'
import { examApi, type ExamBrief, type ExamTemplate, type ExamWritePayload, type PaperConfig } from '@/api/exam'
import { groupApi, type GroupNode } from '@/api/group'
import { errorDetailOf, httpStatusOf } from '@/api/http'
import { questionApi, type QuestionBank } from '@/api/question'
import { useResponsive } from '@/composables/useResponsive'
import { confirmBox } from '@/utils/dialog'
import { formatDateTime, bankLabel } from '@/utils/format'
import { DEFAULT_ORDER_MODE, ORDER_MODES } from '@/constants/paper'
import type { TagType } from '@/constants/ui'

const { isMobile } = useResponsive()

const loading = ref(false)
const saving = ref(false)
const rows = ref<ExamBrief[]>([])
const templates = ref<ExamTemplate[]>([])
const groupTree = ref<GroupNode[]>([])
const banks = ref<QuestionBank[]>([])
// '' = 全部状态（含已归档）；其余值下推后端 status 参数
const statusFilter = ref<string>('')
const groupTreeRef = ref<TreeInstance>()
const dlg = ref(false)
/** 弹窗序号：新建时 id 恒为 0，用它强制重建分组树，避免连续新建时上一次勾选残留。 */
const dlgSeq = ref(0)

/** 发布/编辑弹窗的表单模型（source 仅前端用：区分规则组卷与套用模板）。 */
interface ExamEditForm {
  id: number
  name: string
  source: 'rule' | 'template'
  paper_template_id: number | null
  rules: PaperConfig
  group_ids: number[]
  start_at: string | null
  end_at: string | null
  duration_min: number
  pass_score: number
  max_attempts: number
  need_review: boolean
  show_score_immediately: boolean
  show_analysis: boolean
}

const emptyExamForm = (): ExamEditForm => ({
  id: 0,
  name: '',
  source: 'rule',
  paper_template_id: null,
  rules: { type_quota: {}, bank_ids: [], order_mode: DEFAULT_ORDER_MODE },
  group_ids: [],
  start_at: null,
  end_at: null,
  duration_min: 90,
  pass_score: 60,
  max_attempts: 0,
  need_review: false,
  show_score_immediately: true,
  show_analysis: false,
})

const editing = reactive<ExamEditForm>(emptyExamForm())

const statusLabel = (s: string) =>
  ({ draft: '草稿', published: '已发布', ongoing: '进行中', ended: '已结束', reviewing: '复核中', archived: '已归档' })[
    s
  ] || s
const STATUS_TAGS: Record<string, TagType> = {
  draft: 'info',
  published: 'success',
  ongoing: 'success',
  ended: 'info',
  reviewing: 'warning',
  archived: 'danger',
}
const statusTag = (s: string): TagType => STATUS_TAGS[s] || 'info'

// 题型配比的来源条件：题库 + 指派分组，供配比编辑器统计“各题型可用题量”
const examQuotaSources = computed(() => ({
  bank_ids: editing.rules.bank_ids || [],
  group_ids: editing.group_ids || [],
}))

const onGroupCheck = () => {
  const keys = groupTreeRef.value?.getCheckedKeys(false) ?? []
  editing.group_ids = keys.map((k) => Number(k)).filter((n) => Number.isInteger(n))
}

/** 加载列表用的静态选项（模板/分组树/题库）：只在挂载时拉一次，不随状态筛选变化。 */
const loadLookups = async () => {
  try {
    // 题库下拉始终取全部（含仅考试使用的题库）
    const [tps, gt, bks] = await Promise.all([examApi.listTemplates(), groupApi.tree(), questionApi.listBanks()])
    templates.value = tps
    groupTree.value = gt
    banks.value = bks
  } catch {
    // 加载失败：http 拦截器已提示；保留当前选项，避免弹窗下拉为空
  }
}

/** 列表加载序号：快速切换状态筛选时先发的慢响应可能后到并覆盖新结果。 */
let loadSeq = 0

const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    // 状态筛选下推到后端
    const data = await examApi.listExams(statusFilter.value || undefined)
    if (seq !== loadSeq) return
    rows.value = data
  } catch {
    // 加载失败：http 拦截器已提示；保留当前列表，避免把失败误显示为“暂无考试”
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

const openCreate = () => {
  Object.assign(editing, emptyExamForm())
  // 新建时 editing.id 恒为 0，连续两次新建不会让 el-tree 重挂载，
  // 上一次勾选的分组会残留显示。用弹窗序号强制重建。
  dlgSeq.value += 1
  dlg.value = true
}

const onEdit = (row: ExamBrief) => {
  // 管理端列表已返回 rules/bank_ids/group_ids 等配置（见后端 _exam_admin_brief）。
  // 深拷贝避免直接改动列表数据；逐项兜底以兼容早期未返回这些字段的响应。
  const src = row.rules && typeof row.rules === 'object' ? row.rules : {}
  const r = JSON.parse(JSON.stringify(src)) as PaperConfig
  if (!r.type_quota || typeof r.type_quota !== 'object') r.type_quota = {}
  if (!Array.isArray(r.bank_ids)) r.bank_ids = []
  // 历史考试未存 order_mode，回填默认值避免选择框空白
  if (!r.order_mode) r.order_mode = DEFAULT_ORDER_MODE
  Object.assign(editing, {
    id: row.id,
    name: row.name ?? '',
    source: row.paper_template_id ? 'template' : 'rule',
    paper_template_id: row.paper_template_id ?? null,
    rules: r,
    group_ids: Array.isArray(row.group_ids) ? [...row.group_ids] : [],
    start_at: row.start_at ?? null,
    end_at: row.end_at ?? null,
    duration_min: row.duration_min ?? 90,
    pass_score: row.pass_score ?? 60,
    max_attempts: row.max_attempts ?? 0,
    need_review: !!row.need_review,
    show_score_immediately: row.show_score_immediately ?? true,
    show_analysis: !!row.show_analysis,
  } satisfies Partial<ExamEditForm>)
  dlgSeq.value += 1
  dlg.value = true
  // 分组树需在弹窗渲染后回填勾选状态
  nextTick(() => groupTreeRef.value?.setCheckedKeys(editing.group_ids, false))
}

const save = async () => {
  if (!editing.name) {
    ElMessage.warning('请填写考试名称')
    return
  }
  if (editing.source === 'template' && !editing.paper_template_id) {
    ElMessage.warning('请选择试卷模板')
    return
  }
  saving.value = true
  try {
    // 更新接口 ExamUpdateIn 为 extra="forbid"，不接收 type 字段（考试类型创建后不可变），
    // 只在创建时携带；否则触发 422 校验错误。
    // 同时 duration_min/pass_score/max_attempts 为 NOT NULL 列，后端拒绝显式 null，
    // 这里用 ?? 兜底为默认值，避免把 null 发出去。
    const buildPayload = (confirmReset: boolean): ExamWritePayload => ({
      name: editing.name,
      rules: editing.source === 'rule' ? editing.rules : {},
      paper_template_id: editing.source === 'template' ? editing.paper_template_id : null,
      group_ids: editing.group_ids,
      start_at: editing.start_at || null,
      end_at: editing.end_at || null,
      duration_min: editing.duration_min ?? 90,
      pass_score: editing.pass_score ?? 60,
      max_attempts: editing.max_attempts ?? 0,
      need_review: !!editing.need_review,
      show_score_immediately: !!editing.show_score_immediately,
      show_analysis: !!editing.show_analysis,
      ...(confirmReset ? { confirm_reset: true } : {}),
    })

    const submit = async (confirmReset: boolean) => {
      const payload = buildPayload(confirmReset)
      if (editing.id) {
        await examApi.updateExam(editing.id, payload)
        ElMessage.success('已保存')
      } else {
        await examApi.createExam({ ...payload, type: 'formal' })
        ElMessage.success('已创建，可发布')
      }
    }

    try {
      await submit(false)
    } catch (err) {
      // 改动了组卷来源且该考试已有作答：后端要求显式确认后才作废旧作答并重新固化。
      // 这是破坏性操作，必须让管理员看到影响面再决定（而不是静默清空或静默失效）。
      const detail = errorDetailOf(err) as { code?: string; msg?: string } | undefined
      if (httpStatusOf(err) !== 409 || detail?.code !== 'exam_reset_required') {
        // 其它失败：http 拦截器已提示，直接结束本次保存（不再向上抛，避免未处理的 rejection）
        return
      }
      const confirmed = await confirmBox(
        `${detail.msg ?? ''}确认后将删除这些作答与成绩，并按新配置重新组卷。`,
        '需要作废已有作答',
        { confirmButtonText: '作废并保存', cancelButtonText: '取消' },
      )
      if (!confirmed) return // 用户取消：保留编辑弹窗与已填内容，不产生任何改动
      await submit(true)
    }
    dlg.value = false
    await load()
  } catch {
    // http 拦截器已提示；保留弹窗与已填内容，允许修正后重试
  } finally {
    saving.value = false
  }
}

const onDelete = async (row: ExamBrief) => {
  const ok = await confirmBox(
    `确认删除考试「${row.name}」？删除后不可恢复。` + `若该考试已有作答记录将无法删除，请改用「归档」。`,
    '删除考试',
    { confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  if (!ok) return
  try {
    await examApi.deleteExam(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    // 409（已有作答记录）等错误已由 http 拦截器提示
  }
}

// 归档：对用户隐藏（不再出现在可用列表），但保留考试定义与成绩记录
const onArchive = async (row: ExamBrief) => {
  const ok = await confirmBox(`确认归档考试「${row.name}」？归档后用户不再看到该考试，已有成绩仍可查询。`, '归档考试', {
    confirmButtonText: '归档',
    cancelButtonText: '取消',
  })
  if (!ok) return
  try {
    await examApi.archiveExam(row.id)
    ElMessage.success('已归档，用户不再可见')
    await load()
  } catch {
    // http 拦截器已提示
  }
}

const onUnarchive = async (row: ExamBrief) => {
  const ok = await confirmBox(`确认取消归档考试「${row.name}」？取消后该考试将重新对指派分组可见。`, '取消归档', {
    confirmButtonText: '取消归档',
    cancelButtonText: '关闭',
  })
  if (!ok) return
  try {
    await examApi.unarchiveExam(row.id)
    ElMessage.success('已取消归档')
    await load()
  } catch {
    // http 拦截器已提示
  }
}

/**
 * 公布成绩。
 *
 * 含简答的考试（复核后）与关闭即时出分的考试，成绩必须先由管理员显式公布，
 * 否则考生端永远显示「待复核」。此前前端只把「复核」按钮链到复核页，
 * publish-results 接口从未被调用，成绩无从发布。
 */
const onPublishResults = async (row: ExamBrief) => {
  const ok = await confirmBox(
    `确认公布「${row.name}」的成绩？公布后考生可见本人得分与是否通过。`,
    '公布成绩',
    { confirmButtonText: '公布', cancelButtonText: '取消', type: 'warning' },
  )
  if (!ok) return
  try {
    await examApi.publishResults(row.id)
    ElMessage.success('已公布')
  } catch {
    // http 拦截器已提示（如仍有未复核的简答）
  }
}

const onPublish = async (row: ExamBrief) => {
  const ok = await confirmBox(`确认发布考试「${row.name}」？发布后对指派分组可见。`, '发布考试')
  if (!ok) return
  try {
    await examApi.publishExam(row.id)
    ElMessage.success('已发布')
    await load()
  } catch {
    // http 拦截器已提示
  }
}

onMounted(() => {
  loadLookups()
  load()
})
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.title {
  font-size: 18px;
  font-weight: 600;
}
.tip {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 8px;
}
.time-range {
  display: flex;
  gap: 4%;
  width: 100%;
}
.time-range .el-date-editor {
  flex: 1;
  min-width: 0;
}
@media (max-width: 767px) {
  .time-range {
    flex-direction: column;
    gap: 8px;
  }
}
</style>
