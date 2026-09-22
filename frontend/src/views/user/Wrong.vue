<template>
  <div class="wrong-book" v-loading="loading">
    <div class="toolbar">
      <span class="title">错题本</span>
      <div class="filters">
        <el-select v-model="filters.type" placeholder="题型" clearable aria-label="按题型筛选" style="width: 130px">
          <el-option v-for="t in types" :key="t" :label="t" :value="t" />
        </el-select>
        <el-input
          v-model="filters.keyword"
          placeholder="题干关键词"
          clearable
          aria-label="按题干关键词筛选"
          style="width: 200px"
        />
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
          <el-button size="small" type="primary" @click="startPractice">重做</el-button>
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
import { QUESTION_TYPES } from '@/constants/question'

const router = useRouter()
const loading = ref(false)
const rows = ref<Question[]>([])
const types = QUESTION_TYPES
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
  } catch {
    // 加载失败：http 拦截器已提示；保留当前列表，避免把失败误显示为“错题本是空的”
  } finally {
    loading.value = false
  }
}

// 错题重做与顶部入口一致：都进入 wrong 模式的答题页
const startPractice = () => {
  router.push({ name: 'answer', params: { mode: 'wrong' } })
}

const toggleMark = async (q: Question) => {
  const prev = q.marked
  q.marked = !prev
  try {
    await practiceApi.toggleMark(q.id, q.marked)
  } catch {
    q.marked = prev
    // http 拦截器已提示
    return
  }
  ElMessage.success(q.marked ? '已标记' : '已取消标记')
}

onMounted(load)
</script>

<style scoped>
.wrong-book {
  max-width: 1000px;
}
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
.filters {
  display: flex;
  gap: 8px;
}
.card-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.q-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-left: 3px solid var(--el-color-danger);
  border-radius: 8px;
  padding: 16px 20px;
}
.card-head {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.q-stem {
  font-size: 15px;
  line-height: 1.6;
  margin-bottom: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.card-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
@media (max-width: 767px) {
  .q-card {
    padding: 14px;
  }
  .card-head {
    flex-wrap: wrap;
  }
}
</style>
