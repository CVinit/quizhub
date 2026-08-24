<template>
  <div class="wrong-book" v-loading="loading">
    <div class="toolbar">
      <span class="title">错题本</span>
      <div class="filters">
        <el-select v-model="filters.type" placeholder="题型" clearable style="width: 130px" @change="load">
          <el-option v-for="t in types" :key="t" :label="t" :value="t" />
        </el-select>
        <el-input v-model="filters.keyword" placeholder="题干关键词" clearable style="width: 200px" @keyup.enter="load" />
        <el-button type="primary" @click="load">查询</el-button>
        <el-button @click="startPractice">错题重做</el-button>
      </div>
    </div>

    <el-empty v-if="!loading && rows.length === 0" description="错题本空空如也，继续加油！" :image-size="120" />

    <div class="card-list">
      <div class="q-card" v-for="q in filtered" :key="q.id">
        <div class="card-head">
          <el-tag size="small">{{ q.type }}</el-tag>
          <el-tag type="info" size="small">难度 {{ q.difficulty }}</el-tag>
          <el-tag type="danger" size="small">错题</el-tag>
        </div>
        <div class="q-stem">{{ q.question }}</div>
        <div class="card-actions">
          <el-button size="small" type="primary" @click="redo(q)">重做</el-button>
          <el-button size="small" @click="toggleMark(q)">{{ q.marked ? '取消标记' : '标记' }}</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { practiceApi, type Question } from '@/api/practice'
import { api } from '@/api/http'

const router = useRouter()
const loading = ref(false)
const rows = ref<(Question & { marked?: boolean })[]>([])
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
    // 用 wrong 模式拉取全部错题
    rows.value = await practiceApi.start('wrong')
    // 取标记状态
    const marks = await api.get('/records/practice/progress').catch(() => null)
  } finally {
    loading.value = false
  }
}

const redo = (q: Question) => {
  router.push({ name: 'answer', params: { mode: 'wrong' } })
}

const startPractice = () => {
  router.push({ name: 'answer', params: { mode: 'wrong' } })
}

const toggleMark = async (q: any) => {
  q.marked = !q.marked
  await practiceApi.toggleMark(q.id, q.marked)
  ElMessage.success(q.marked ? '已标记' : '已取消标记')
}

onMounted(load)
</script>

<style scoped>
.wrong-book { max-width: 1000px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.filters { display: flex; gap: 8px; }
.card-list { display: flex; flex-direction: column; gap: 12px; }
.q-card { background: #fff; border: 1px solid #ebeef5; border-left: 3px solid #f56c6c; border-radius: 8px; padding: 16px 20px; }
.card-head { display: flex; gap: 8px; margin-bottom: 10px; }
.q-stem { font-size: 15px; line-height: 1.6; margin-bottom: 12px; white-space: pre-wrap; word-break: break-word; }
.card-actions { display: flex; gap: 8px; flex-wrap: wrap; }
@media (max-width: 767px) {
  .q-card { padding: 14px; }
  .card-head { flex-wrap: wrap; }
}
</style>
