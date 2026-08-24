<template>
  <div class="overview" v-loading="loading">
    <h2>概览面板</h2>

    <div class="stat-cards">
      <div class="stat-card">
        <div class="stat-num">{{ d.total_users }}</div>
        <div class="stat-label">注册用户</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ d.active_users }}</div>
        <div class="stat-label">活跃用户</div>
      </div>
      <div class="stat-card warn" v-if="d.pending_approvals">
        <div class="stat-num">{{ d.pending_approvals }}</div>
        <div class="stat-label">待审批</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ d.today_active }}</div>
        <div class="stat-label">今日活跃</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ d.total_questions }}</div>
        <div class="stat-label">题库题目</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ d.completion_rate }}%</div>
        <div class="stat-label">完成率</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ d.accuracy }}%</div>
        <div class="stat-label">全站正确率</div>
      </div>
      <div class="stat-card warn" v-if="d.pending_reviews">
        <div class="stat-num">{{ d.pending_reviews }}</div>
        <div class="stat-label">待复核简答</div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">待办事项</div>
      <div class="todos">
        <div class="todo" v-if="d.pending_approvals" @click="$router.push('/admin/users?status=pending')">
          <el-badge :value="d.pending_approvals">
            <el-button>待审批用户</el-button>
          </el-badge>
        </div>
        <div class="todo" v-if="d.pending_reviews" @click="$router.push('/admin/review')">
          <el-badge :value="d.pending_reviews">
            <el-button>待复核简答</el-button>
          </el-badge>
        </div>
        <div class="todo" v-if="!d.pending_approvals && !d.pending_reviews">
          <el-tag type="success">暂无待办，一切正常</el-tag>
        </div>
      </div>
    </div>

    <div class="section">
      <div class="section-title">数据刷新</div>
      <div class="refresh-row">
        <span class="tip">手动重算最近若干天的统计预聚合（排行数据来源）</span>
        <el-input-number v-model="days" :min="1" :max="30" size="small" />
        <el-button type="primary" :loading="refreshing" @click="onRefresh">刷新统计</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api/http'

const loading = ref(false)
const refreshing = ref(false)
const days = ref(7)
const d = ref<any>({ total_users: 0, active_users: 0, pending_approvals: 0, today_active: 0, total_questions: 0, completion_rate: 0, accuracy: 0, pending_reviews: 0 })

const load = async () => {
  loading.value = true
  try {
    d.value = await api.get('/admin/panel/overview')
  } finally {
    loading.value = false
  }
}
const onRefresh = async () => {
  refreshing.value = true
  try {
    const res = await api.post('/admin/panel/refresh', null, { params: { days: days.value } })
    ElMessage.success(`已刷新 ${res.refreshed} 条`)
    await load()
  } finally {
    refreshing.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.overview { max-width: 1100px; }
.overview h2 { margin-bottom: 20px; }
.stat-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 14px; margin-bottom: 24px; }
.stat-card { background: #fff; border-radius: 8px; padding: 18px; text-align: center; border: 1px solid #ebeef5; }
.stat-card.warn .stat-num { color: #f56c6c; }
.stat-num { font-size: 26px; font-weight: 700; color: var(--brand-primary); }
.stat-label { color: #909399; font-size: 13px; margin-top: 4px; }
.section { background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; border: 1px solid #ebeef5; }
.section-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; }
.todos { display: flex; gap: 16px; flex-wrap: wrap; }
.refresh-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.tip { color: #909399; font-size: 13px; }
@media (max-width: 767px) {
  .stat-cards { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .stat-card { padding: 14px 8px; }
  .stat-num { font-size: 22px; }
  .section { padding: 14px; }
}
</style>
