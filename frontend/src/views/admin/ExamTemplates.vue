<template>
  <div class="exam-templates" v-loading="loading">
    <div class="toolbar">
      <span class="title">试卷模板</span>
      <el-button type="primary" @click="openCreate">新建模板</el-button>
    </div>

    <el-table :data="rows" border v-loading="loading" class="mobile-table-hidden">
      <el-table-column prop="name" label="模板名称" min-width="160" />
      <el-table-column label="类型" width="100">
        <template #default="{ row }">{{ row.mode === 'mock' ? '模拟' : '正式' }}</template>
      </el-table-column>
      <el-table-column label="题量" width="80">
        <template #default="{ row }">{{ row.question_count }}</template>
      </el-table-column>
      <el-table-column label="题型配比" min-width="200">
        <template #default="{ row }">{{ quotaText(row.config) }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="preview(row as ExamTemplate)">预览组卷</el-button>
          <el-button size="small" type="danger" @click="onDel(row as ExamTemplate)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：模板卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title">
          {{ row.name }}
          <span style="color: var(--el-text-color-secondary); font-weight: 400"
            >{{ row.mode === 'mock' ? '模拟' : '正式' }} · {{ row.question_count }}题</span
          >
        </div>
        <div class="mc-row"><span class="mc-label">配比</span>{{ quotaText(row.config) }}</div>
        <div class="mc-row" v-if="row.created_at">
          <span class="mc-label">创建</span>{{ formatDateTime(row.created_at) }}
        </div>
        <div class="mc-actions">
          <el-button size="small" @click="preview(row)">预览组卷</el-button>
          <el-button size="small" type="danger" @click="onDel(row)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- 新建/编辑 -->
    <el-dialog v-model="dlg" title="新建模板" width="640px">
      <el-form :model="editing" label-width="100px">
        <el-form-item label="模板名称"><el-input v-model="editing.name" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="editing.mode">
            <el-radio value="mock">模拟</el-radio>
            <el-radio value="formal">正式</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="题型配比">
          <TypeQuotaEditor
            v-model="editing.config.type_quota"
            :sources="quotaSources"
            :max-questions="editing.config.max_questions"
          />
        </el-form-item>
        <el-form-item label="来源题库">
          <el-select
            v-model="editing.config.bank_ids"
            multiple
            filterable
            clearable
            placeholder="留空则不限（全部题库）"
            style="width: 100%"
          >
            <el-option v-for="b in banks" :key="b.id" :label="bankLabel(b)" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源分组">
          <!-- key 跟随弹窗打开次数：el-tree 只在挂载时按 default-checked-keys 初始化，
               prop 变化不会取消旧勾选；复用实例会让上次勾选的分组残留并被提交 -->
          <el-tree
            ref="groupTreeRef"
            :key="`gt-${dlgSeq}`"
            :data="groupTreeData"
            node-key="id"
            :props="{ label: 'name', children: 'children' }"
            show-checkbox
            :default-checked-keys="editing.config.group_ids || []"
            @check="onGroupCheck"
          />
        </el-form-item>
        <el-form-item label="来源标签">
          <el-input v-model="tagInput" placeholder="逗号分隔，如：网络,协议" />
        </el-form-item>
        <el-form-item label="难度比例">
          <el-input v-model="editing.config.difficulty_dist_raw" placeholder='{"1":0.3,"2":0.5,"3":0.2} 留空则不限' />
        </el-form-item>
        <el-form-item label="最大题数">
          <el-input-number v-model="editing.config.max_questions" :min="1" :max="500" />
        </el-form-item>
        <el-form-item label="出题顺序">
          <el-select v-model="editing.config.order_mode" style="width: 100%">
            <el-option v-for="m in ORDER_MODES" :key="m.value" :label="m.label" :value="m.value">
              <span>{{ m.label }}</span>
              <span class="tip" style="float: right">{{ m.hint }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <!-- 后端组卷不支持重复抽题（allow_duplicate 为 true 时直接 400），
             开关只会让预览通过而保存失败，故去掉该选项并强制为 false。 -->
        <el-form-item label="出题说明">
          <span class="tip">每种题型按配额从候选题中抽取，不会重复使用同一题目</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button @click="previewConfig">预览</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 预览结果 -->
    <el-dialog v-model="previewDlg" title="组卷预览" width="640px">
      <el-descriptions :column="2" border v-if="previewData">
        <el-descriptions-item label="总题数">{{ previewData.count }}</el-descriptions-item>
        <el-descriptions-item label="总分">{{ previewData.total_score }}</el-descriptions-item>
      </el-descriptions>
      <el-table :data="previewData?.questions || []" size="small" border style="margin-top: 12px">
        <el-table-column type="index" width="50" />
        <el-table-column prop="type" label="题型" width="90" />
        <el-table-column prop="question" label="题干" show-overflow-tooltip />
        <el-table-column prop="difficulty" label="难度" width="70" />
        <el-table-column prop="score" label="分值" width="70" />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, type TreeInstance } from 'element-plus'
import { examApi, type ExamTemplate, type PaperConfig, type PaperPreview } from '@/api/exam'
import { groupApi, type GroupNode } from '@/api/group'
import { questionApi, type QuestionBank } from '@/api/question'
import { useResponsive } from '@/composables/useResponsive'
import { confirmBox } from '@/utils/dialog'
import { formatDateTime, bankLabel } from '@/utils/format'
import { DEFAULT_ORDER_MODE, ORDER_MODES } from '@/constants/paper'

const { isMobile } = useResponsive()

/** 模板编辑表单：config 与后端 PaperConfig 对齐（difficulty_dist_raw 仅前端表单使用）。 */
interface TemplateEditForm {
  name: string
  mode: string
  config: PaperConfig
}

const emptyTemplateForm = (): TemplateEditForm => ({
  name: '',
  mode: 'mock',
  config: {
    type_quota: {},
    difficulty_dist: {},
    group_ids: [],
    bank_ids: [],
    tags: [],
    allow_duplicate: false,
    max_questions: 100,
    order_mode: DEFAULT_ORDER_MODE,
  },
})

const loading = ref(false)
const saving = ref(false)
const rows = ref<ExamTemplate[]>([])
const dlg = ref(false)
/** 弹窗打开次数：作为 el-tree 的 key，强制重建以清空上一次的勾选状态。 */
const dlgSeq = ref(0)
const previewDlg = ref(false)
const previewData = ref<PaperPreview | null>(null)
const groupTreeData = ref<GroupNode[]>([])
const groupTreeRef = ref<TreeInstance>()
const banks = ref<QuestionBank[]>([])
const tagInput = ref('')
/**
 * 防抖后的标签文本，仅用于喂给 TypeQuotaEditor 的来源条件。
 *
 * TypeQuotaEditor 的 sourceKey 每次变化都会请求一次各题型可用题量，直接绑定 tagInput
 * 会让每敲一个字都发一次请求（输入 4 个字 = 4 次）。
 */
const debouncedTags = ref('')
let tagTimer: ReturnType<typeof setTimeout> | undefined
watch(tagInput, (v) => {
  clearTimeout(tagTimer)
  tagTimer = setTimeout(() => {
    debouncedTags.value = v
  }, 300)
})
onBeforeUnmount(() => clearTimeout(tagTimer))
const editing = reactive<TemplateEditForm>(emptyTemplateForm())

const quotaText = (config?: PaperConfig) => {
  const q = config?.type_quota || {}
  return (
    Object.entries(q)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${k}×${v}`)
      .join('  ') || '—'
  )
}

// 题型配比编辑器的来源条件（标签输入框变化时组件自动重查统计）
const quotaSources = computed(() => ({
  bank_ids: editing.config.bank_ids || [],
  group_ids: editing.config.group_ids || [],
  tags: debouncedTags.value
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean),
}))

const onGroupCheck = () => {
  const keys = groupTreeRef.value?.getCheckedKeys(false) ?? []
  editing.config.group_ids = keys.map((k) => Number(k)).filter((n) => Number.isInteger(n))
}

const load = async () => {
  loading.value = true
  try {
    const [tpls, gt, bks] = await Promise.all([examApi.listTemplates(), groupApi.tree(), questionApi.listBanks()])
    rows.value = tpls
    groupTreeData.value = gt
    banks.value = bks
  } catch {
    // 加载失败：http 拦截器已提示；保留当前列表，避免把失败误显示为“暂无模板”
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  Object.assign(editing, emptyTemplateForm())
  tagInput.value = ''
  // 同步清空防抖值，避免上一次弹窗的标签条件继续参与可用题量统计
  clearTimeout(tagTimer)
  debouncedTags.value = ''
  // 自增后 el-tree 的 :key 变化 → 重建实例，default-checked-keys 才会真正生效（清空旧勾选）
  dlgSeq.value++
  dlg.value = true
}

/**
 * 组装提交配置。
 *
 * difficulty_dist_raw 是前端表单里的 JSON 文本：解析失败时返回 null 并提示，
 * 而不是静默丢弃——否则管理员以为比例已生效，实际组卷按“不限难度”执行。
 */
const buildConfig = (): PaperConfig | null => {
  const cfg = JSON.parse(JSON.stringify(editing.config)) as PaperConfig
  delete cfg.difficulty_dist_raw
  // 后端 generate_paper 在 allow_duplicate 为真时直接 400，模板一律按不重复保存
  cfg.allow_duplicate = false
  if (tagInput.value.trim())
    cfg.tags = tagInput.value
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean)
  else cfg.tags = []
  const raw = editing.config.difficulty_dist_raw?.trim()
  if (raw) {
    try {
      cfg.difficulty_dist = JSON.parse(raw) as Record<string, number>
    } catch {
      ElMessage.warning('难度比例不是合法 JSON，请检查后重试')
      return null
    }
  }
  return cfg
}

const previewConfig = async () => {
  const cfg = buildConfig()
  if (!cfg) return
  try {
    previewData.value = await examApi.previewPaper(cfg)
    previewDlg.value = true
  } catch {
    // http 拦截器已提示；不打开空预览弹窗，避免看起来"点了没反应"
  }
}

const preview = async (row: ExamTemplate) => {
  try {
    previewData.value = await examApi.previewPaper(row.config)
    previewDlg.value = true
  } catch {
    // http 拦截器已提示
  }
}

const save = async () => {
  if (!editing.name) {
    ElMessage.warning('请填写模板名称')
    return
  }
  const cfg = buildConfig()
  if (!cfg) return
  saving.value = true
  try {
    await examApi.createTemplate({
      name: editing.name,
      mode: editing.mode,
      config: cfg,
      group_ids: editing.config.group_ids || [],
    })
    ElMessage.success('已保存')
    dlg.value = false
    await load()
  } catch {
    // http 拦截器已提示；保留弹窗与已填内容，允许修正后重试
  } finally {
    saving.value = false
  }
}

const onDel = async (row: ExamTemplate) => {
  const ok = await confirmBox(`确认删除模板「${row.name}」？被正式考试引用时将无法删除。`, '提示')
  if (!ok) return
  try {
    await examApi.deleteTemplate(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    // http 拦截器已提示
  }
}

onMounted(load)
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
@media (max-width: 767px) {
  .toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
}
</style>
