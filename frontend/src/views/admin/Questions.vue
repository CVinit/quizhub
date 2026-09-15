<template>
  <div>
    <div class="toolbar">
      <span class="title">题目列表</span>
      <div class="filters">
        <el-input v-model="filters.keyword" placeholder="题干关键词" clearable style="width: 160px" @keyup.enter="onFilterChange" />
        <el-select v-model="filters.bank_id" placeholder="题库" clearable filterable style="width: 220px" @change="onFilterChange">
          <el-option v-for="b in banks" :key="b.id" :label="bankLabel(b)" :value="b.id" />
        </el-select>
        <el-select v-model="filters.type" placeholder="题型" clearable style="width: 120px" @change="onFilterChange">
          <el-option v-for="t in types" :key="t" :label="t" :value="t" />
        </el-select>
        <el-select v-model="filters.difficulty" placeholder="难度" clearable style="width: 100px" @change="onFilterChange">
          <el-option label="易" :value="1" />
          <el-option label="中" :value="2" />
          <el-option label="难" :value="3" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
        <el-button type="success" @click="onAdd">新增题目</el-button>
      </div>
    </div>

    <el-table :data="rows" v-loading="loading" border class="mobile-table-hidden">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="type" label="题型" width="90">
        <template #default="{ row }"><el-tag size="small">{{ row.type }}</el-tag></template>
      </el-table-column>
      <el-table-column label="题干" min-width="320">
        <template #default="{ row }"><div class="q-text">{{ row.question }}</div></template>
      </el-table-column>
      <el-table-column label="难度" width="70">
        <template #default="{ row }">{{ diffLabel(row.difficulty) }}</template>
      </el-table-column>
      <el-table-column label="标签" width="160">
        <template #default="{ row }">
          <el-tag v-for="t in (row.tags || [])" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="score" label="分值" width="70" />
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：卡片列表 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title"><el-tag size="small">{{ row.type }}</el-tag> #{{ row.id }} · {{ diffLabel(row.difficulty) }} · {{ row.score }}分</div>
        <div class="mc-row"><span class="mc-label">题干</span><span>{{ row.question }}</span></div>
        <div class="mc-row" v-if="row.tags && row.tags.length"><span class="mc-label">标签</span><span>{{ (row.tags || []).join('、') }}</span></div>
        <div class="mc-actions">
          <el-button size="small" type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="onDelete(row)">删除</el-button>
        </div>
      </div>
    </div>
    <el-empty v-if="isMobile && !loading && rows.length === 0" description="暂无题目" :image-size="80" />

    <el-pagination
      v-model:current-page="page" :page-size="pageSize" :total="total"
      layout="total, prev, pager, next" :small="isMobile"
      style="margin-top: 16px; justify-content: flex-end; display: flex"
      @current-change="load" />

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑题目' : '新增题目'" width="680px" top="5vh">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="题型" prop="type">
          <el-select v-model="form.type" style="width: 200px">
            <el-option v-for="t in types" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="题库来源">
          <el-select v-model="form.bank_id" clearable style="width: 240px">
            <el-option v-for="b in banks" :key="b.id" :label="b.name" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属分组">
          <el-tree-select
            v-model="form.group_id" :data="groupTree" node-key="id"
            :props="{ label: 'name', children: 'children' }" clearable check-strictly
            style="width: 240px" />
        </el-form-item>
        <el-form-item label="题干" prop="question">
          <el-input v-model="form.question" type="textarea" :rows="3" />
        </el-form-item>

        <template v-if="['单选题', '多选题'].includes(form.type)">
          <el-form-item label="选项">
            <el-input v-model="form.optionsText" type="textarea" :rows="5"
              placeholder="每行一个选项，形如 A.选项1&#10;B.选项2" />
          </el-form-item>
          <el-form-item label="答案">
            <el-input v-model="form.answerText" :placeholder="form.type === '单选题' ? '如 A' : '如 ABC'" />
          </el-form-item>
        </template>

        <template v-if="form.type === '判断题'">
          <el-form-item label="答案">
            <el-radio-group v-model="form.answerText">
              <el-radio value="正确">正确</el-radio>
              <el-radio value="错误">错误</el-radio>
            </el-radio-group>
          </el-form-item>
        </template>

        <template v-if="form.type === '填空题'">
          <el-form-item label="答案">
            <el-input v-model="form.answerText" type="textarea" :rows="2"
              placeholder="按空位顺序，空与空之间用 | 分隔，每空多等价答案用 / 分隔。如 答案1/答案1b|答案2" />
          </el-form-item>
        </template>

        <template v-if="form.type === '简答题'">
          <el-form-item label="参考答案">
            <el-input v-model="form.answerText" type="textarea" :rows="4" />
          </el-form-item>
        </template>

        <template v-if="form.type === '拖拽题'">
          <el-form-item label="题项与容器">
            <el-input v-model="form.answerText" type="textarea" :rows="5"
              placeholder="每行一对，格式 题项:正确容器，如&#10;HTTP:80&#10;HTTPS:443" />
          </el-form-item>
        </template>

        <el-form-item label="解析">
          <el-input v-model="form.analysis" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="难度">
          <el-radio-group v-model="form.difficulty">
            <el-radio :value="1">易</el-radio>
            <el-radio :value="2">中</el-radio>
            <el-radio :value="3">难</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="知识点标签">
          <el-input v-model="form.tagsText" placeholder="多个标签用英文逗号分隔" />
        </el-form-item>
        <el-form-item label="分值">
          <el-input-number v-model="form.score" :min="0.5" :step="0.5" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox, type FormInstance } from 'element-plus'
import { questionApi, uploadApi, type QuestionBank, type QuestionItem } from '@/api/question'
import { groupApi, type GroupNode } from '@/api/group'
import { useResponsive } from '@/composables/useResponsive'

const route = useRoute()
const { isMobile } = useResponsive()

const types = ['单选题', '多选题', '判断题', '填空题', '简答题', '拖拽题']
const rows = ref<QuestionItem[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 20
const total = ref(0)
const filters = reactive({ keyword: '', type: '', bank_id: undefined as number | undefined, difficulty: undefined as number | undefined })
const banks = ref<QuestionBank[]>([])
const groupTree = ref<GroupNode[]>([])

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref<number | null>(null)
const formRef = ref<FormInstance>()
const form = reactive({
  type: '单选题', bank_id: null as number | null, group_id: null as number | null,
  question: '', optionsText: '', answerText: '', analysis: '', difficulty: 2,
  tagsText: '', score: 2,
})
const rules = {
  type: [{ required: true, message: '请选择题型', trigger: 'change' }],
  question: [{ required: true, message: '请输入题干', trigger: 'blur' }],
}

const load = async () => {
  loading.value = true
  try {
    const params: Record<string, any> = { page: page.value, page_size: pageSize }
    if (filters.keyword) params.keyword = filters.keyword
    if (filters.bank_id) params.bank_id = filters.bank_id
    if (filters.type) params.type = filters.type
    if (filters.difficulty) params.difficulty = filters.difficulty
    const data = await questionApi.list(params)
    rows.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

/**
 * 筛选条件变化：先回到第 1 页再查询。
 * 否则在第 N 页切换题库/题型时，新结果集不足 N 页会显示空列表，看起来像“筛选无效”。
 */
const onFilterChange = () => {
  page.value = 1
  return load()
}

const diffLabel = (d: number) => ({ 1: '易', 2: '中', 3: '难' }[d] || '中')
// 题库选项附带题量，便于筛选前判断该库规模（question_count 后端可能不返回）
const bankLabel = (b: QuestionBank) =>
  typeof b.question_count === 'number' ? `${b.name}（${b.question_count} 题）` : b.name

const onAdd = () => {
  editingId.value = null
  Object.assign(form, { type: '单选题', bank_id: null, group_id: null, question: '', optionsText: '', answerText: '', analysis: '', difficulty: 2, tagsText: '', score: 2 })
  dialogVisible.value = true
}

const onEdit = (row: QuestionItem) => {
  editingId.value = row.id
  form.type = row.type
  form.bank_id = row.bank_id
  form.group_id = row.group_id
  form.question = row.question
  form.analysis = row.analysis
  form.difficulty = row.difficulty
  form.score = row.score
  form.tagsText = (row.tags || []).join(',')
  // 反填选项/答案
  if (['单选题', '多选题'].includes(row.type)) {
    form.optionsText = (row.options || []).map((o: string, i: number) => `${String.fromCharCode(65 + i)}.${o}`).join('\n')
    form.answerText = row.answer || ''
  } else if (row.type === '判断题') {
    form.answerText = row.answer || '正确'
  } else if (row.type === '填空题') {
    form.answerText = Array.isArray(row.answer) ? (row.answer as string[][]).map((b) => b.join('/')).join('|') : ''
  } else if (row.type === '简答题') {
    form.answerText = row.answer || ''
  } else if (row.type === '拖拽题') {
    const m = row.answer || {}
    form.answerText = Object.entries(m).map(([k, v]) => `${k}:${v}`).join('\n')
  }
  dialogVisible.value = true
}

const buildPayload = () => {
  const tags = form.tagsText ? form.tagsText.replace('，', ',').split(',').map((t) => t.trim()).filter(Boolean) : null
  const payload: any = {
    type: form.type, bank_id: form.bank_id, group_id: form.group_id,
    question: form.question, analysis: form.analysis, difficulty: form.difficulty,
    tags, score: form.score,
  }
  if (['单选题', '多选题'].includes(form.type)) {
    payload.options = form.optionsText.split('\n').map((s) => s.replace(/^[A-Z][.、:：]/, '').trim()).filter(Boolean)
    const ans = (form.answerText || '').toUpperCase().replace(/\s/g, '')
    payload.answer = form.type === '多选题' ? ans.split('').sort().join('') : ans
  } else if (form.type === '判断题') {
    payload.answer = form.answerText
  } else if (form.type === '填空题') {
    payload.answer = (form.answerText || '').split('|').map((b) => b.split('/').map((s) => s.trim()).filter(Boolean))
  } else if (form.type === '简答题') {
    payload.answer = form.answerText
  } else if (form.type === '拖拽题') {
    const mapping: Record<string, string> = {}
    form.answerText.split('\n').forEach((line) => {
      const [k, v] = line.split(':')
      if (k && v) mapping[k.trim()] = v.trim()
    })
    payload.left_items = Object.keys(mapping)
    payload.right_items = Object.values(mapping)
    payload.answer = mapping
  }
  return payload
}

const onSave = async () => {
  await formRef.value?.validate()
  saving.value = true
  try {
    const payload = buildPayload()
    if (editingId.value) {
      await questionApi.update(editingId.value, payload)
    } else {
      await questionApi.create(payload)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

const onDelete = async (row: QuestionItem) => {
  await ElMessageBox.confirm(`确认删除题目 #${row.id}？`, '提示', { type: 'warning' })
  await questionApi.remove(row.id)
  ElMessage.success('已删除')
  await load()
}

// 题库/分组选项与首批数据并行加载：题库下拉必须在用户可操作前就绪，
// 否则筛选框一度无可选项，表现为“无法按题库筛选”。
onMounted(async () => {
  // 支持从「题库管理」页带 ?bank=ID 跳转过来，直接按该题库筛选
  const bankFromQuery = Number(route.query.bank)
  if (Number.isFinite(bankFromQuery) && bankFromQuery > 0) {
    filters.bank_id = bankFromQuery
  }
  const [banksRes, treeRes] = await Promise.all([
    questionApi.listBanks(),
    groupApi.tree(),
    load(),
  ])
  banks.value = banksRes
  groupTree.value = treeRes
})
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.filters { display: flex; gap: 8px; }
.q-text { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
</style>