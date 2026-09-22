<template>
  <div class="exam-records" v-loading="loading">
    <div class="toolbar">
      <span class="title">考试记录</span>
      <div class="filters">
        <el-select
          v-model="filters.examId"
          placeholder="选择考试"
          clearable
          filterable
          style="width: 220px"
          @change="load"
        >
          <el-option v-for="e in exams" :key="e.id" :label="e.name" :value="e.id" />
        </el-select>
        <el-input
          v-model="filters.keyword"
          placeholder="考生邮箱/姓名"
          clearable
          style="width: 180px"
          @keyup.enter="load"
        />
        <!-- 结果状态筛选：及格/不及格/待复核/已公布 -->
        <el-select v-model="filters.outcome" placeholder="结果状态" clearable style="width: 140px" @change="load">
          <el-option label="及格" value="passed" />
          <el-option label="不及格" value="failed" />
          <el-option label="待复核" value="pending" />
          <el-option label="已公布" value="published" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
      </div>
    </div>

    <el-empty v-if="!loading && filtered.length === 0" :image-size="100">
      <template #description>
        {{ rows.length === 0 ? '暂无考试记录' : '没有匹配的记录，试试调整关键词' }}
      </template>
    </el-empty>

    <el-table :data="filtered" border v-loading="loading" class="mobile-table-hidden" v-if="!isMobile">
      <el-table-column prop="exam_name" label="考试" min-width="160" />
      <el-table-column prop="user_email" label="考生" min-width="160" />
      <el-table-column label="得分" width="100">
        <template #default="{ row }">{{ row.published ? `${row.score} / ${row.total_score}` : '—' }}</template>
      </el-table-column>
      <el-table-column label="正确数" width="90">
        <template #default="{ row }">{{ row.correct_count }} / {{ row.total_count }}</template>
      </el-table-column>
      <el-table-column label="是否通过" width="100">
        <template #default="{ row }">
          <el-tag v-if="row.published" :type="row.passed ? 'success' : 'danger'">{{
            row.passed ? '通过' : '未通过'
          }}</el-tag>
          <el-tag v-else type="warning">待复核</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="submitted_at" label="交卷时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.submitted_at) }}</template>
      </el-table-column>
    </el-table>

    <!-- 手机端：考试记录卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && filtered.length">
      <div class="mc" v-for="row in filtered" :key="row.id">
        <div class="mc-title">{{ row.exam_name }}</div>
        <div class="mc-row"><span class="mc-label">考生</span>{{ row.user_email }}</div>
        <div class="mc-row">
          <span class="mc-label">得分</span>{{ row.published ? `${row.score} / ${row.total_score}` : '—' }}
        </div>
        <div class="mc-row"><span class="mc-label">正确数</span>{{ row.correct_count }} / {{ row.total_count }}</div>
        <div class="mc-row">
          <span class="mc-label">结果</span>
          <el-tag v-if="row.published" :type="row.passed ? 'success' : 'danger'" size="small">{{
            row.passed ? '通过' : '未通过'
          }}</el-tag>
          <el-tag v-else type="warning" size="small">待复核</el-tag>
        </div>
        <div class="mc-row" v-if="row.submitted_at">
          <span class="mc-label">交卷</span>{{ formatDateTime(row.submitted_at) }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { examApi, type ExamBrief, type ExamResultRow } from '@/api/exam'
import { useResponsive } from '@/composables/useResponsive'
import { formatDateTime } from '@/utils/format'

const { isMobile } = useResponsive()

const route = useRoute()
const loading = ref(false)
const exams = ref<ExamBrief[]>([])
const rows = ref<ExamResultRow[]>([])
const filters = reactive<{ examId: number | ''; keyword: string; outcome: string }>({
  examId: '',
  keyword: '',
  outcome: '',
})

const filtered = computed(() => {
  const k = filters.keyword.trim()
  if (!k) return rows.value
  return rows.value.filter((r) => (r.user_email || '').includes(k) || (r.user_name || '').includes(k))
})

/** 列表加载序号：切换考试/状态筛选会并发多个请求，只接受最新一次的结果。 */
let loadSeq = 0

const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    // 考试下拉与记录列表互不依赖，并行请求避免两次串行往返
    const [examList, resultRows] = await Promise.all([
      examApi.listExams(),
      // outcome 状态筛选下推到后端；关键词仍在前端本地过滤
      examApi.listResults(filters.examId || undefined, filters.outcome || undefined),
    ])
    if (seq !== loadSeq) return
    exams.value = examList
    rows.value = resultRows
  } catch {
    /* 拦截器已提示；保留当前列表 */
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(() => {
  // 支持从「正式考试」页带 ?exam=ID 跳转过来。
  // 只在此处读取 query：若放进 load()，用户清空筛选后会被 query 再次回填，导致筛选无法取消。
  const fromQuery = Number(route.query.exam)
  if (Number.isFinite(fromQuery) && fromQuery > 0) filters.examId = fromQuery
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
.filters {
  display: flex;
  gap: 8px;
}
@media (max-width: 767px) {
  .toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .filters {
    flex-wrap: wrap;
  }
  .filters > * {
    flex: 1 1 40%;
    min-width: 0;
  }
  .filters .el-button {
    flex: 1 1 40%;
  }
}
</style>
