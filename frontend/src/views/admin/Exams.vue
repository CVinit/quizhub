<template>
  <div class="exams" v-loading="loading">
    <div class="toolbar">
      <span class="title">正式考试管理</span>
      <el-button type="primary" @click="openCreate">发布考试</el-button>
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
          {{ row.start_at ? fmt(row.start_at) : '不限' }} ~ {{ row.end_at ? fmt(row.end_at) : '不限' }}
        </template>
      </el-table-column>
      <el-table-column prop="duration_min" label="限时" width="80" />
      <el-table-column prop="pass_score" label="及格线" width="80" />
      <el-table-column label="题量" width="80">
        <template #default="{ row }">{{ row.total_questions }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" type="success" v-if="row.status === 'draft'" @click="onPublish(row)">发布</el-button>
          <el-button size="small" @click="$router.push(`/admin/exam-records?exam=${row.id}`)">成绩</el-button>
          <el-button size="small" type="warning" @click="$router.push(`/admin/review?exam=${row.id}`)">复核</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：考试卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title">{{ row.name }} <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag></div>
        <div class="mc-row"><span class="mc-label">时段</span>{{ row.start_at ? fmt(row.start_at) : '不限' }} ~ {{ row.end_at ? fmt(row.end_at) : '不限' }}</div>
        <div class="mc-row"><span class="mc-label">限时/及格</span>{{ row.duration_min }}分钟 · {{ row.pass_score }}分 · {{ row.total_questions }}题</div>
        <div class="mc-actions">
          <el-button size="small" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" type="success" v-if="row.status === 'draft'" @click="onPublish(row)">发布</el-button>
          <el-button size="small" @click="$router.push(`/admin/exam-records?exam=${row.id}`)">成绩</el-button>
          <el-button size="small" type="warning" @click="$router.push(`/admin/review?exam=${row.id}`)">复核</el-button>
        </div>
      </div>
    </div>

    <!-- 发布/编辑 -->
    <el-dialog v-model="dlg" :title="editing.id ? '编辑考试' : '发布考试'" width="680px">
      <el-form :model="editing" label-width="110px">
        <el-form-item label="考试名称"><el-input v-model="editing.name" /></el-form-item>
        <el-form-item label="组卷方式">
          <el-radio-group v-model="editing.source">
            <el-radio label="rule">规则组卷</el-radio>
            <el-radio label="template">套用模板</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="试卷模板" v-if="editing.source === 'template'">
          <el-select v-model="editing.paper_template_id" placeholder="选择模板" style="width: 100%">
            <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <template v-if="editing.source === 'rule'">
          <el-form-item label="题型配比">
            <div class="quota-grid">
              <div v-for="t in types" :key="t" class="quota-row">
                <span class="q-name">{{ t }}</span>
                <el-input-number v-model="editing.rules.type_quota[t]" :min="0" :max="100" size="small" />
              </div>
            </div>
          </el-form-item>
          <el-form-item label="来源题库">
            <el-select v-model="editing.rules.bank_ids" multiple filterable clearable placeholder="留空则不限（全部题库）" style="width: 100%">
              <el-option v-for="b in banks" :key="b.id" :label="`${b.name}（${b.question_count} 题）`" :value="b.id" />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item label="指派分组">
          <el-tree ref="groupTreeRef" :data="groupTree" node-key="id" :props="{ label: 'name', children: 'children' }"
            show-checkbox :default-checked-keys="editing.group_ids || []" @check="onGroupCheck" />
        </el-form-item>
        <el-form-item label="开放时段">
          <el-date-picker v-model="editing.start_at" type="datetime" placeholder="开始时间（留空不限）" format="YYYY-MM-DD HH:mm" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 48%" />
          <el-date-picker v-model="editing.end_at" type="datetime" placeholder="结束时间（留空不限）" format="YYYY-MM-DD HH:mm" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 48%; margin-left: 4%" />
        </el-form-item>
        <el-form-item label="限时(分钟)"><el-input-number v-model="editing.duration_min" :min="1" :max="600" /></el-form-item>
        <el-form-item label="及格线"><el-input-number v-model="editing.pass_score" :min="0" :max="100" /></el-form-item>
        <el-form-item label="最大尝试次数"><el-input-number v-model="editing.max_attempts" :min="0" :max="10" /> <span class="tip">0 = 不限</span></el-form-item>
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
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { examApi } from '@/api/exam'
import { groupApi, type GroupNode } from '@/api/group'
import { questionApi, type QuestionBank } from '@/api/question'
import { useResponsive } from '@/composables/useResponsive'

const { isMobile } = useResponsive()

const types = ['单选题', '多选题', '判断题', '填空题', '简答题', '拖拽题']
const loading = ref(false)
const saving = ref(false)
const rows = ref<any[]>([])
const templates = ref<any[]>([])
const groupTree = ref<GroupNode[]>([])
const banks = ref<QuestionBank[]>([])
const groupTreeRef = ref()
const dlg = ref(false)
const editing = reactive<any>({
  id: 0, name: '', source: 'rule', paper_template_id: null,
  rules: { type_quota: {}, bank_ids: [] }, group_ids: [],
  start_at: null, end_at: null, duration_min: 90, pass_score: 60, max_attempts: 0,
  need_review: false, show_score_immediately: true, show_analysis: false,
})

const statusLabel = (s: string) => ({ draft: '草稿', published: '已发布', ongoing: '进行中', ended: '已结束', reviewing: '复核中' }[s] || s)
const statusTag = (s: string) => ({ draft: 'info', published: 'success', ongoing: 'success', ended: 'info', reviewing: 'warning' }[s] || 'info')
const fmt = (iso: string) => { if (!iso) return ''; const d = new Date(iso); return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { hour12: false }) }

const onGroupCheck = () => { editing.group_ids = groupTreeRef.value?.getCheckedKeys(false) || [] }

const load = async () => {
  loading.value = true
  try {
    const [exs, tps, gt, bks] = await Promise.all([examApi.listExams(), examApi.listTemplates(), groupApi.tree(), questionApi.listBanks()])
    rows.value = exs
    templates.value = tps
    groupTree.value = gt
    banks.value = bks
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  Object.assign(editing, {
    id: 0, name: '', source: 'rule', paper_template_id: null,
    rules: { type_quota: {}, bank_ids: [] }, group_ids: [],
    start_at: null, end_at: null, duration_min: 90, pass_score: 60, max_attempts: 0,
    need_review: false, show_score_immediately: true, show_analysis: false,
  })
  dlg.value = true
}

const onEdit = (row: any) => {
  const r = row.rules?.type_quota ? JSON.parse(JSON.stringify(row.rules)) : { type_quota: {} }
  if (!r.bank_ids) r.bank_ids = []
  Object.assign(editing, {
    id: row.id, name: row.name, source: row.paper_template_id ? 'template' : 'rule',
    paper_template_id: row.paper_template_id || null,
    rules: r,
    group_ids: row.group_ids || [],
    start_at: row.start_at, end_at: row.end_at,
    duration_min: row.duration_min, pass_score: row.pass_score, max_attempts: row.max_attempts,
    need_review: row.need_review, show_score_immediately: row.show_score_immediately, show_analysis: row.show_analysis,
  })
  dlg.value = true
}

const save = async () => {
  if (!editing.name) { ElMessage.warning('请填写考试名称'); return }
  saving.value = true
  try {
    const payload: any = {
      name: editing.name, type: 'formal',
      rules: editing.source === 'rule' ? editing.rules : {},
      paper_template_id: editing.source === 'template' ? editing.paper_template_id : null,
      group_ids: editing.group_ids, start_at: editing.start_at, end_at: editing.end_at,
      duration_min: editing.duration_min, pass_score: editing.pass_score,
      max_attempts: editing.max_attempts, need_review: editing.need_review,
      show_score_immediately: editing.show_score_immediately, show_analysis: editing.show_analysis,
    }
    if (editing.id) {
      await examApi.updateExam(editing.id, payload)
      ElMessage.success('已保存')
    } else {
      await examApi.createExam(payload)
      ElMessage.success('已创建，可发布')
    }
    dlg.value = false
    await load()
  } finally {
    saving.value = false
  }
}

const onPublish = async (row: any) => {
  await ElMessageBox.confirm(`确认发布考试「${row.name}」？发布后对指派分组可见。`, '发布考试', { type: 'warning' })
  await examApi.publishExam(row.id)
  ElMessage.success('已发布')
  await load()
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.quota-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; }
.quota-row { display: flex; align-items: center; justify-content: space-between; }
.q-name { font-size: 14px; }
.tip { color: #909399; font-size: 12px; margin-left: 8px; }
</style>
