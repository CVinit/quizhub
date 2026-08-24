<template>
  <div class="marks-page" v-loading="loading">
    <div class="toolbar">
      <span class="title">我的标记</span>
      <div class="filters">
        <el-select v-model="filters.type" placeholder="题型" clearable style="width: 130px" @change="load">
          <el-option v-for="t in types" :key="t" :label="t" :value="t" />
        </el-select>
        <el-input v-model="filters.keyword" placeholder="题干关键词" clearable style="width: 200px" @keyup.enter="load" />
        <el-button type="primary" @click="load">查询</el-button>
      </div>
    </div>

    <el-empty v-if="!loading && rows.length === 0" description="还没有标记任何题目" :image-size="120" />

    <div class="card-list">
      <div class="q-card" v-for="q in filtered" :key="q.id">
        <div class="card-head">
          <el-tag size="small">{{ q.type }}</el-tag>
          <el-tag type="info" size="small">难度 {{ q.difficulty }}</el-tag>
          <el-tag type="warning" size="small">已标记</el-tag>
          <span class="note" v-if="q.marked_note">备注：{{ q.marked_note }}</span>
        </div>
        <div class="q-stem">{{ q.question }}</div>
        <div class="card-actions">
          <el-button size="small" type="primary" @click="redo">去做题</el-button>
          <el-button size="small" @click="addNote(q)">{{ q.marked_note ? '改备注' : '加备注' }}</el-button>
          <el-button size="small" type="danger" @click="toggleMark(q)">取消标记</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { practiceApi, type Question } from '@/api/practice'

const router = useRouter()
const loading = ref(false)
const rows = ref<(Question & { marked_note?: string })[]>([])
const types = ['单选题', '多选题', '判断题', '填空题', '简答题', '拖拽题']
const filters = reactive({ type: '', keyword: '' })

const filtered = computed(() => {
  let r = rows.value
  if (filters.type) r = r.filter((q) => q.type === filters.type)
  if (filters.keyword) r = r.filter((q) => q.question.includes(filters.keyword))
  return r
})

const load = async () => {
  loading.value = true
  try {
    rows.value = await practiceApi.start('mark')
  } finally {
    loading.value = false
  }
}

const redo = () => {
  router.push({ name: 'answer', params: { mode: 'mark' } })
}

const toggleMark = async (q: any) => {
  await practiceApi.toggleMark(q.id, false)
  ElMessage.success('已取消标记')
  await load()
}

const addNote = async (q: any) => {
  const { value } = await ElMessageBox.prompt('请输入备注', '标记备注', {
    inputValue: q.marked_note || '', inputPlaceholder: '可选的解题笔记',
  })
  await practiceApi.toggleMark(q.id, true, value || '')
  q.marked_note = value || ''
  ElMessage.success('已保存')
}

onMounted(load)
</script>

<style scoped>
.marks-page { max-width: 1000px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.filters { display: flex; gap: 8px; }
.card-list { display: flex; flex-direction: column; gap: 12px; }
.q-card { background: #fff; border: 1px solid #ebeef5; border-left: 3px solid #e6a23c; border-radius: 8px; padding: 16px 20px; }
.card-head { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.note { color: #909399; font-size: 13px; word-break: break-all; }
.q-stem { font-size: 15px; line-height: 1.6; margin-bottom: 12px; white-space: pre-wrap; word-break: break-word; }
.card-actions { display: flex; gap: 8px; flex-wrap: wrap; }
@media (max-width: 767px) {
  .q-card { padding: 14px; }
}
</style>
