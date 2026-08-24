<template>
  <div class="exam-records" v-loading="loading">
    <div class="toolbar">
      <span class="title">考试记录</span>
      <div class="filters">
        <el-select v-model="filters.examId" placeholder="选择考试" clearable filterable style="width: 220px" @change="load">
          <el-option v-for="e in exams" :key="e.id" :label="e.name" :value="e.id" />
        </el-select>
        <el-input v-model="filters.keyword" placeholder="考生邮箱/姓名" clearable style="width: 180px" @keyup.enter="load" />
        <el-button type="primary" @click="load">查询</el-button>
      </div>
    </div>

    <el-empty v-if="!loading && rows.length === 0" description="暂无考试记录" :image-size="100" />

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
          <el-tag v-if="row.published" :type="row.passed ? 'success' : 'danger'">{{ row.passed ? '通过' : '未通过' }}</el-tag>
          <el-tag v-else type="warning">待复核</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="submitted_at" label="交卷时间" width="160" />
    </el-table>

    <!-- 手机端：考试记录卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && filtered.length">
      <div class="mc" v-for="row in filtered" :key="row.id">
        <div class="mc-title">{{ row.exam_name }}</div>
        <div class="mc-row"><span class="mc-label">考生</span>{{ row.user_email }}</div>
        <div class="mc-row"><span class="mc-label">得分</span>{{ row.published ? `${row.score} / ${row.total_score}` : '—' }}</div>
        <div class="mc-row"><span class="mc-label">正确数</span>{{ row.correct_count }} / {{ row.total_count }}</div>
        <div class="mc-row">
          <span class="mc-label">结果</span>
          <el-tag v-if="row.published" :type="row.passed ? 'success' : 'danger'" size="small">{{ row.passed ? '通过' : '未通过' }}</el-tag>
          <el-tag v-else type="warning" size="small">待复核</el-tag>
        </div>
        <div class="mc-row" v-if="row.submitted_at"><span class="mc-label">交卷</span>{{ row.submitted_at }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { examApi } from '@/api/exam'
import { useResponsive } from '@/composables/useResponsive'

const { isMobile } = useResponsive()

const route = useRoute()
const loading = ref(false)
const exams = ref<any[]>([])
const rows = ref<any[]>([])
const filters = reactive({ examId: '' as any, keyword: '' })

const filtered = computed(() => {
  if (!filters.keyword) return rows.value
  return rows.value.filter((r) => (r.user_email || '').includes(filters.keyword) || (r.user_name || '').includes(filters.keyword))
})

const load = async () => {
  loading.value = true
  try {
    exams.value = await examApi.listExams()
    if (route.query.exam && !filters.examId) filters.examId = Number(route.query.exam)
    rows.value = await examApi.listResults(filters.examId || undefined)
  } catch (e) {
    rows.value = []
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.filters { display: flex; gap: 8px; }
</style>
