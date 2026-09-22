<template>
  <div class="exam-list" v-loading="loading">
    <h2>考试中心</h2>

    <div class="mock-entry" v-if="!loading">
      <div class="mock-card">
        <div class="mock-info">
          <div class="mock-title">模拟考试</div>
          <div class="mock-desc">自由选择题库与题量，按题型比例即时组卷，交卷即出分</div>
        </div>
        <el-button type="primary" size="large" @click="setupVisible = true">开始模拟考试</el-button>
      </div>
    </div>

    <MockExamSetupDialog v-model="setupVisible" @started="onMockStarted" />

    <el-divider content-position="left">正式考试</el-divider>

    <div v-if="!loading && exams.length === 0" class="empty">
      <el-empty description="暂无可参加的正式考试" :image-size="100" />
    </div>

    <div class="exam-cards">
      <div class="exam-card" v-for="e in exams" :key="e.id" :class="{ disabled: e.state !== 'available' }">
        <div class="card-head">
          <span class="exam-name">{{ e.name }}</span>
          <el-tag :type="stateTag(e.state)" size="small">{{ stateLabel(e.state) }}</el-tag>
        </div>
        <div class="card-meta">
          <span
            ><el-icon><Clock /></el-icon> {{ e.duration_min }} 分钟</span
          >
          <span
            ><el-icon><Trophy /></el-icon> 及格线 {{ e.pass_score }} 分</span
          >
          <span v-if="e.total_questions"
            ><el-icon><Document /></el-icon> {{ e.total_questions }} 题</span
          >
        </div>
        <div class="card-time" v-if="e.start_at || e.end_at">
          {{ e.start_at ? '开始 ' + formatDateTime(e.start_at) : '' }}
          {{ e.end_at ? '· 截止 ' + formatDateTime(e.end_at) : '' }}
        </div>
        <div class="card-time" v-if="e.max_attempts">最多 {{ e.max_attempts }} 次（已参加 {{ e.attempts }} 次）</div>
        <div class="card-actions">
          <el-button v-if="e.state === 'available'" type="primary" @click="startExam(e)"> 进入考试 </el-button>
          <el-button v-else disabled>{{ stateLabel(e.state) }}</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Clock, Trophy, Document } from '@element-plus/icons-vue'
import { examApi, type ExamBrief } from '@/api/exam'
import MockExamSetupDialog from '@/components/MockExamSetupDialog.vue'
import { formatDateTime } from '@/utils/format'
import type { TagType } from '@/constants/ui'

const router = useRouter()
const loading = ref(false)
const setupVisible = ref(false)
const exams = ref<ExamBrief[]>([])

const load = async () => {
  loading.value = true
  try {
    exams.value = await examApi.available()
  } catch {
    // 加载失败：http 拦截器已提示；保留当前列表，避免把失败误显示为“暂无可参加的考试”
  } finally {
    loading.value = false
  }
}

const stateLabel = (s: string) =>
  ({ available: '可参加', not_started: '未开始', ended: '已结束', max_reached: '已达上限' })[s] || s
const STATE_TAGS: Record<string, TagType> = {
  available: 'success',
  not_started: 'info',
  ended: 'info',
  max_reached: 'warning',
}
const stateTag = (s: string): TagType => STATE_TAGS[s] || 'info'

const onMockStarted = (sessionId: number) => {
  router.push({ name: 'exam-taking', params: { id: sessionId } })
}

const startExam = async (e: ExamBrief) => {
  try {
    const session = await examApi.start(e.id)
    router.push({ name: 'exam-taking', params: { id: session.session_id }, query: { examId: String(e.id) } })
  } catch {
    // 接口已统一报错
  }
}

onMounted(load)
</script>

<style scoped>
.exam-list {
  max-width: 900px;
}
.exam-list h2 {
  margin-bottom: 20px;
}
.mock-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: linear-gradient(135deg, var(--brand-primary), var(--brand-primary-light));
  color: var(--el-color-white);
  border-radius: 10px;
  padding: 24px 28px;
}
.mock-title {
  font-size: 18px;
  font-weight: 600;
}
.mock-desc {
  font-size: 13px;
  opacity: 0.9;
  margin-top: 6px;
}
.mock-card :deep(.el-button--primary) {
  background: var(--el-bg-color);
  color: var(--brand-primary);
  border-color: var(--el-color-white);
}
.exam-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}
.exam-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 20px;
}
.exam-card.disabled {
  opacity: 0.7;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.exam-name {
  font-weight: 600;
  font-size: 16px;
}
.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  color: var(--el-text-color-regular);
  font-size: 13px;
  margin-bottom: 8px;
}
.card-meta .el-icon {
  vertical-align: -2px;
  margin-right: 2px;
}
.card-time {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-bottom: 4px;
}
.card-actions {
  margin-top: 12px;
}
@media (max-width: 767px) {
  .mock-card {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
    padding: 18px;
  }
  .mock-card :deep(.el-button) {
    width: 100%;
  }
  .exam-cards {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .exam-card {
    padding: 16px;
  }
  .card-meta {
    gap: 10px;
  }
  .exam-list h2 {
    margin-bottom: 12px;
  }
}
</style>
