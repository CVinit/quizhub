<template>
  <div class="review-page" v-loading="loading">
    <div class="toolbar">
      <span class="title">简答复核</span>
      <div class="filters">
        <el-input
          v-model="filters.keyword"
          placeholder="考试/考生/题干"
          clearable
          style="width: 220px"
          @keyup.enter="load"
        />
        <!-- 复核状态筛选：默认待复核，可查看已复核历史 -->
        <el-select v-model="filters.verdict" placeholder="状态" style="width: 140px" @change="load">
          <el-option label="待复核" value="pending" />
          <el-option label="已通过" value="pass" />
          <el-option label="部分得分" value="partial" />
          <el-option label="未通过" value="fail" />
          <el-option label="全部已复核" value="done" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
      </div>
    </div>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
      v-if="isPendingView && pending.length"
    >
      共 {{ pending.length }} 条待复核。全部复核完成后，需到「正式考试管理」点击该考试的「公布成绩」，
      考生才能看到得分。
    </el-alert>

    <el-empty
      v-if="!loading && filtered.length === 0"
      :description="isPendingView ? '暂无待复核的简答题' : '暂无记录'"
      :image-size="100"
    />

    <div class="review-list">
      <div class="review-card" v-for="r in filtered" :key="r.id">
        <div class="card-head">
          <el-tag size="small">{{ r.exam_name }}</el-tag>
          <span class="user">{{ r.user }}</span>
          <el-tag type="info" size="small">题目 #{{ r.question_id }}</el-tag>
          <el-tag v-if="r.verdict" :type="verdictTag(r.verdict)" size="small">{{
            verdictLabel(r.verdict, r.partial_score)
          }}</el-tag>
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
        <!-- 已复核项只读展示结论，避免重复复核（后端也会拒绝） -->
        <div class="review-actions" v-if="!r.verdict">
          <el-input-number
            v-model="partialMap[r.id]"
            :min="0"
            :max="r.score ?? 0"
            :step="0.5"
            size="small"
            placeholder="部分得分"
            aria-label="部分得分"
            controls-position="right"
            style="width: 130px"
          />
          <el-button size="small" type="success" :loading="busy[r.id]" @click="doReview(r, 'pass')">通过</el-button>
          <el-button size="small" type="warning" :loading="busy[r.id]" @click="doReview(r, 'partial')"
            >部分得分</el-button
          >
          <el-button size="small" type="danger" :loading="busy[r.id]" @click="doReview(r, 'fail')">不通过</el-button>
        </div>
        <div class="review-done" v-else>已于 {{ r.reviewed_at ? formatDateTime(r.reviewed_at) : '—' }} 完成复核</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { examApi, type ReviewItem } from '@/api/exam'
import { formatDateTime } from '@/utils/format'
import type { TagType } from '@/constants/ui'

const loading = ref(false)
const pending = ref<ReviewItem[]>([])
const filters = reactive({ keyword: '', verdict: 'pending' as string })
const partialMap = reactive<Record<number, number>>({})
const busy = reactive<Record<number, boolean>>({})

const filtered = computed(() => {
  if (!filters.keyword) return pending.value
  const k = filters.keyword
  return pending.value.filter(
    (r) => (r.exam_name || '').includes(k) || (r.user || '').includes(k) || (r.question || '').includes(k),
  )
})

const verdictLabel = (v: string, score?: number | null) =>
  ({ pass: '已通过', fail: '未通过', partial: `部分得分${score != null ? ` ${score}` : ''}` })[v] || v
const VERDICT_TAGS: Record<string, TagType> = { pass: 'success', fail: 'danger', partial: 'warning' }
const verdictTag = (v: string): TagType => VERDICT_TAGS[v] || 'info'
const isPendingView = computed(() => filters.verdict === 'pending')

/** 列表加载序号：快速切换筛选时先发的慢响应可能后到并覆盖新结果。 */
let loadSeq = 0

const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    // 状态筛选下推到后端（排除默认的 pending 即「待复核」）
    const data = await examApi.listPendingReviews(filters.verdict)
    if (seq !== loadSeq) return
    pending.value = data
  } catch {
    // 加载失败：http 拦截器已提示；保留当前列表，避免把失败误显示为“暂无待复核”
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

const doReview = async (r: ReviewItem, verdict: string) => {
  if (verdict === 'partial' && partialMap[r.id] == null) {
    ElMessage.warning('请填写部分得分')
    return
  }
  busy[r.id] = true
  try {
    await examApi.doReview(r.id, verdict, verdict === 'partial' ? partialMap[r.id] : undefined)
    ElMessage.success('已复核')
    await load()
  } catch {
    // http 拦截器已提示；保留当前列表，允许重试
  } finally {
    busy[r.id] = false
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
.filters {
  display: flex;
  gap: 8px;
}
.review-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.review-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 20px;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.card-head .user {
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.q-stem {
  font-size: 15px;
  line-height: 1.6;
  margin-bottom: 16px;
  padding: 12px;
  background: var(--el-color-info-light-9);
  border-radius: 6px;
  word-break: break-word;
}
.block {
  margin-bottom: 12px;
}
.block-title {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.block-body {
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.block-body.ref {
  padding: 10px 12px;
  background: var(--el-color-warning-light-9);
  border-radius: 6px;
}
.review-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
  flex-wrap: wrap;
}
.review-done {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
@media (max-width: 767px) {
  .review-card {
    padding: 14px;
  }
}
</style>
