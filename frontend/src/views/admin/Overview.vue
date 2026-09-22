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
        <div class="todo" v-if="d.pending_approvals">
          <el-badge :value="d.pending_approvals">
            <el-button @click="$router.push('/admin/users?status=pending')">待审批用户</el-button>
          </el-badge>
        </div>
        <div class="todo" v-if="d.pending_reviews">
          <el-badge :value="d.pending_reviews">
            <el-button @click="$router.push('/admin/review')">待复核简答</el-button>
          </el-badge>
        </div>
        <div class="todo" v-if="loadError">
          <el-tag type="danger">概览加载失败，待办数量未知，请刷新重试</el-tag>
        </div>
        <div class="todo" v-else-if="!d.pending_approvals && !d.pending_reviews">
          <el-tag type="success">暂无待办，一切正常</el-tag>
        </div>
      </div>
    </div>

    <!-- 统计重算仅超级管理员可用（后端 require_super）：对部门管理员隐藏，避免必然 403 -->
    <div class="section" v-if="auth.isSuper">
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
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(false)
/** 首次加载失败时为 true。初始全 0 与「真的没有待办」无法区分，不能据此显示「一切正常」。 */
const loadError = ref(false)
const refreshing = ref(false)
const days = ref(7)

/** 管理端概览指标（/admin/panel/overview）。 */
interface OverviewData {
  total_users: number
  active_users: number
  pending_approvals: number
  today_active: number
  total_questions: number
  completion_rate: number
  accuracy: number
  pending_reviews: number
}

const d = ref<OverviewData>({
  total_users: 0,
  active_users: 0,
  pending_approvals: 0,
  today_active: 0,
  total_questions: 0,
  completion_rate: 0,
  accuracy: 0,
  pending_reviews: 0,
})

const load = async () => {
  loading.value = true
  try {
    d.value = await api.get<OverviewData>('/admin/panel/overview')
    loadError.value = false
  } catch {
    // 加载失败：http 拦截器已提示；保留上一次的指标，但必须显式标记，避免把全 0 显示成「无待办」
    loadError.value = true
  } finally {
    loading.value = false
  }
}
const onRefresh = async () => {
  refreshing.value = true
  try {
    const res = await api.post<{ refreshed: number }>('/admin/panel/refresh', null, { params: { days: days.value } })
    ElMessage.success(`已刷新 ${res.refreshed} 条`)
    await load()
  } catch {
    // http 拦截器已提示
  } finally {
    refreshing.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.overview {
  max-width: 1100px;
}
.overview h2 {
  margin-bottom: 20px;
}
.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 14px;
  margin-bottom: 24px;
}
.stat-card {
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 18px;
  text-align: center;
  border: 1px solid var(--el-border-color-lighter);
}
.stat-card.warn .stat-num {
  color: var(--el-color-danger);
}
.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: var(--brand-primary);
}
.stat-label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-top: 4px;
}
.section {
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 16px 20px;
  margin-bottom: 16px;
  border: 1px solid var(--el-border-color-lighter);
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
}
.todos {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.refresh-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.tip {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
@media (max-width: 767px) {
  .stat-cards {
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  .stat-card {
    padding: 14px 8px;
  }
  .stat-num {
    font-size: 22px;
  }
  .section {
    padding: 14px;
  }
}
</style>
