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
      <el-table-column prop="created_at" label="创建时间" width="160" />
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="preview(row)">预览组卷</el-button>
          <el-button size="small" type="danger" @click="onDel(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：模板卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title">{{ row.name }} <span style="color:#909399;font-weight:400">{{ row.mode === 'mock' ? '模拟' : '正式' }} · {{ row.question_count }}题</span></div>
        <div class="mc-row"><span class="mc-label">配比</span>{{ quotaText(row.config) }}</div>
        <div class="mc-row" v-if="row.created_at"><span class="mc-label">创建</span>{{ row.created_at }}</div>
        <div class="mc-actions">
          <el-button size="small" @click="preview(row)">预览组卷</el-button>
          <el-button size="small" type="danger" @click="onDel(row)">删除</el-button>
        </div>
      </div>
    </div>

    <!-- 新建/编辑 -->
    <el-dialog v-model="dlg" :title="editing.id ? '编辑模板' : '新建模板'" width="640px">
      <el-form :model="editing" label-width="100px">
        <el-form-item label="模板名称"><el-input v-model="editing.name" /></el-form-item>
        <el-form-item label="类型">
          <el-radio-group v-model="editing.mode">
            <el-radio value="mock">模拟</el-radio>
            <el-radio value="formal">正式</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="题型配比">
          <TypeQuotaEditor v-model="editing.config.type_quota" :sources="quotaSources" :max-questions="editing.config.max_questions" />
        </el-form-item>
        <el-form-item label="来源题库">
          <el-select v-model="editing.config.bank_ids" multiple filterable clearable placeholder="留空则不限（全部题库）" style="width: 100%">
            <el-option v-for="b in banks" :key="b.id" :label="`${b.name}（${b.question_count} 题）`" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源分组">
          <el-tree ref="groupTree" :data="groupTree" node-key="id" :props="{ label: 'name', children: 'children' }" show-checkbox
            :default-checked-keys="editing.config.group_ids || []" @check="onGroupCheck" />
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
        <el-form-item label="允许重复">
          <el-switch v-model="editing.config.allow_duplicate" />
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
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { examApi } from '@/api/exam'
import { groupApi, type GroupNode } from '@/api/group'
import { questionApi, type QuestionBank } from '@/api/question'
import { useResponsive } from '@/composables/useResponsive'
import { DEFAULT_ORDER_MODE, ORDER_MODES } from '@/constants/paper'

const { isMobile } = useResponsive()

const loading = ref(false)
const saving = ref(false)
const rows = ref<any[]>([])
const dlg = ref(false)
const previewDlg = ref(false)
const previewData = ref<any>(null)
const groupTree = ref<GroupNode[]>([])
const groupTreeRef = ref()
const banks = ref<QuestionBank[]>([])
const tagInput = ref('')
const editing = reactive<{ id: number; name: string; mode: string; config: any }>({
  id: 0, name: '', mode: 'mock',
  config: { type_quota: {}, difficulty_dist: {}, group_ids: [], bank_ids: [], tags: [], allow_duplicate: false, max_questions: 100, order_mode: DEFAULT_ORDER_MODE },
})

const quotaText = (config: any) => {
  const q = config?.type_quota || {}
  return Object.entries(q).filter(([, v]: any) => v > 0).map(([k, v]: any) => `${k}×${v}`).join('  ') || '—'
}

// 题型配比编辑器的来源条件（标签输入框变化时组件自动重查统计）
const quotaSources = computed(() => ({
  bank_ids: editing.config.bank_ids || [],
  group_ids: editing.config.group_ids || [],
  tags: tagInput.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
}))

const onGroupCheck = () => {
  const ids = groupTreeRef.value?.getCheckedKeys(false) as number[]
  editing.config.group_ids = ids || []
}

const load = async () => {
  loading.value = true
  try {
    const [tpls, gt, bks] = await Promise.all([examApi.listTemplates(), groupApi.tree(), questionApi.listBanks()])
    rows.value = tpls
    groupTree.value = gt
    banks.value = bks
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  editing.id = 0
  editing.name = ''
  editing.mode = 'mock'
  editing.config = { type_quota: {}, difficulty_dist: {}, group_ids: [], bank_ids: [], tags: [], allow_duplicate: false, max_questions: 100, order_mode: DEFAULT_ORDER_MODE }
  tagInput.value = ''
  dlg.value = true
}

const buildConfig = () => {
  const cfg = JSON.parse(JSON.stringify(editing.config))
  if (tagInput.value.trim()) cfg.tags = tagInput.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
  else cfg.tags = []
  if (editing.config.difficulty_dist_raw) {
    try { cfg.difficulty_dist = JSON.parse(editing.config.difficulty_dist_raw) } catch { cfg.difficulty_dist = {} }
  }
  delete cfg.difficulty_dist_raw
  return cfg
}

const previewConfig = async () => {
  const cfg = buildConfig()
  previewData.value = await examApi.previewPaper(cfg)
  previewDlg.value = true
}

const preview = async (row: any) => {
  previewData.value = await examApi.previewPaper(row.config)
  previewDlg.value = true
}

const save = async () => {
  if (!editing.name) { ElMessage.warning('请填写模板名称'); return }
  saving.value = true
  try {
    const cfg = buildConfig()
    await examApi.createTemplate({ name: editing.name, mode: editing.mode, config: cfg, group_ids: editing.config.group_ids })
    ElMessage.success('已保存')
    dlg.value = false
    await load()
  } finally {
    saving.value = false
  }
}

const onDel = async (row: any) => {
  await ElMessageBox.confirm(`确认删除模板「${row.name}」？被正式考试引用时将无法删除。`, '提示', { type: 'warning' })
  await examApi.deleteTemplate(row.id)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
@media (max-width: 767px) {
  .toolbar { flex-direction: column; align-items: stretch; gap: 12px; }
}
</style>
