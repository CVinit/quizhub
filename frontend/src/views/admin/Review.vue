<template>
  <div class="review-page" v-loading="loading">
    <div class="toolbar">
      <span class="title">简答复核</span>
      <div class="filters">
        <el-input v-model="filters.keyword" placeholder="考试/考生/题干" clearable style="width: 220px" @keyup.enter="load" />
        <el-button type="primary" @click="load">查询</el-button>
      </div>
    </div>

    <el-alert type="warning" :closable="false" show-icon style="margin-bottom: 16px" v-if="pending.length">
      共 {{ pending.length }} 条待复核。复核完成后需到「正式考试」列表点击「复核」入口公布成绩。
    </el-alert>

    <el-empty v-if="!loading && filtered.length === 0" description="暂无待复核的简答题" :image-size="100" />

    <div class="review-list">
      <div class="review-card" v-for="r in filtered" :key="r.id">
        <div class="card-head">
          <el-tag size="small">{{ r.exam_name }}</el-tag>
          <span class="user">{{ r.user }}</span>
          <el-tag type="info" size="small">题目 #{{ r.question_id }}</el-tag>
        </div>
        <div class="q-stem">{{ r.question }}</div>
        <div class="block">
          <div class="block-title">考生作答</div>
          <div class="block-body">{{ r.user_answer || '（未作答）' }}</div>
        </div>
        <div class="block">
          <div class="block-title">参考答案</div>
          <div class="block-body ref">{{ r.reference_answer || '（无）' }}</div>
        </div>
        <div class="review-actions">
          <el-input-number v-model="partialMap[r.id]" :min="0" :max="10" :step="0.5" size="small" :disabled="false" placeholder="部分得分" controls-position="right" style="width: 130px" />
          <el-button size="small" type="success" :loading="busy[r.id]" @click="doReview(r, 'pass')">通过</el-button>
          <el-button size="small" type="warning" :loading="busy[r.id]" @click="doReview(r, 'partial')">部分得分</el-button>
          <el-button size="small" type="danger" :loading="busy[r.id]" @click="doReview(r, 'fail')">不通过</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { examApi } from '@/api/exam'

const loading = ref(false)
const pending = ref<any[]>([])
const filters = reactive({ keyword: '' })
const partialMap = reactive<Record<number, number>>({})
const busy = reactive<Record<number, boolean>>({})

const filtered = computed(() => {
  if (!filters.keyword) return pending.value
  const k = filters.keyword
  return pending.value.filter((r) => (r.exam_name || '').includes(k) || (r.user || '').includes(k) || (r.question || '').includes(k))
})

const load = async () => {
  loading.value = true
  try {
    pending.value = await examApi.listPendingReviews()
  } finally {
    loading.value = false
  }
}

const doReview = async (r: any, verdict: string) => {
  if (verdict === 'partial' && partialMap[r.id] == null) {
    ElMessage.warning('请填写部分得分'); return
  }
  busy[r.id] = true
  try {
    await examApi.doReview(r.id, verdict, verdict === 'partial' ? partialMap[r.id] : undefined)
    ElMessage.success('已复核')
    await load()
  } finally {
    busy[r.id] = false
  }
}
onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.filters { display: flex; gap: 8px; }
.review-list { display: flex; flex-direction: column; gap: 16px; }
.review-card { background: #fff; border: 1px solid #ebeef5; border-radius: 8px; padding: 20px; }
.card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.card-head .user { color: #606266; font-size: 14px; }
.q-stem { font-size: 15px; line-height: 1.6; margin-bottom: 16px; padding: 12px; background: #f4f4f5; border-radius: 6px; word-break: break-word; }
.block { margin-bottom: 12px; }
.block-title { font-size: 13px; color: #909399; margin-bottom: 4px; }
.block-body { line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.block-body.ref { padding: 10px 12px; background: #fdf6ec; border-radius: 6px; }
.review-actions { display: flex; align-items: center; gap: 8px; margin-top: 16px; padding-top: 12px; border-top: 1px solid #f0f0f0; flex-wrap: wrap; }
@media (max-width: 767px) {
  .review-card { padding: 14px; }
}
</style>
