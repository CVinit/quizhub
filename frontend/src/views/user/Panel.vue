<template>
  <div class="panel" v-loading="loading">
    <div class="hello">你好，{{ auth.user?.name || auth.user?.email }}</div>

    <div class="stat-cards">
      <div class="stat-card">
        <div class="stat-num">{{ stats.total }}</div>
        <div class="stat-label">题库总题数</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ stats.practiced }}</div>
        <div class="stat-label">已练习</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ accuracy }}%</div>
        <div class="stat-label">正确率</div>
      </div>
      <div class="stat-card warn">
        <div class="stat-num">{{ stats.wrong }}</div>
        <div class="stat-label">错题数</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ stats.marked }}</div>
        <div class="stat-label">已标记</div>
      </div>
    </div>

    <div class="progress-box" v-if="stats.total">
      <div class="progress-text">学习进度 {{ stats.practiced }} / {{ stats.total }}</div>
      <el-progress :percentage="progressPct" :stroke-width="10" />
    </div>

    <div class="section-title">快捷入口</div>
    <div class="shortcuts">
      <div class="shortcut" @click="$router.push('/answer')">
        <el-icon><Document /></el-icon><span>顺序练习</span>
      </div>
      <div class="shortcut" @click="$router.push('/answer/random')">
        <el-icon><Refresh /></el-icon><span>随机抽题</span>
      </div>
      <div class="shortcut" @click="$router.push('/exam')">
        <el-icon><Files /></el-icon><span>模拟考试</span>
      </div>
      <div class="shortcut" @click="$router.push('/wrong')">
        <el-icon><Warning /></el-icon><span>错题本</span>
      </div>
      <div class="shortcut" @click="$router.push('/marks')">
        <el-icon><Collection /></el-icon><span>我的标记</span>
      </div>
    </div>

    <div class="recent-tables">
      <div class="section-title">最近考试</div>
      <el-table :data="recentExams" border v-if="recentExams.length" size="small">
        <el-table-column prop="name" label="考试" min-width="160" />
        <el-table-column label="得分" width="120">
          <template #default="{ row }">{{ row.published ? `${row.score} / ${row.total_score}` : '待复核' }}</template>
        </el-table-column>
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.published" :type="row.passed ? 'success' : 'danger'" size="small">{{ row.passed ? '通过' : '未通过' }}</el-tag>
            <el-tag v-else type="warning" size="small">待复核</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <div class="section-title">最近练习</div>
      <el-table :data="recent" border v-if="recent.length" size="small">
        <el-table-column prop="question" label="题目" min-width="320" show-overflow-tooltip>
          <template #default="{ row }">{{ row.question }}</template>
        </el-table-column>
        <el-table-column prop="type" label="题型" width="90" />
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.is_correct === true" type="success" size="small">正确</el-tag>
            <el-tag v-else-if="row.is_correct === false" type="danger" size="small">错误</el-tag>
            <el-tag v-else type="info" size="small">待自评</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="answered_at" label="时间" width="160" />
      </el-table>
      <el-empty v-else description="还没有练习记录，去答题吧" :image-size="80" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { practiceApi } from '@/api/practice'
import { Document, Refresh, Files, Warning, Collection } from '@/utils/icons'
import { api } from '@/api/http'

const auth = useAuthStore()
const loading = ref(false)
const stats = ref({ total: 0, practiced: 0, wrong: 0, marked: 0, type_dist: {} as Record<string, number> })
const recent = ref<any[]>([])
const recentExams = ref<any[]>([])

const accuracy = computed(() => {
  if (!stats.value.practiced) return 0
  return Math.round(((stats.value.practiced - stats.value.wrong) / stats.value.practiced) * 100)
})
const progressPct = computed(() =>
  stats.value.total ? Math.round((stats.value.practiced / stats.value.total) * 100) : 0,
)

const load = async () => {
  loading.value = true
  try {
    // 面板聚合数据用 /panel/me；modes 用于快捷入口计数
    const [panelData, modeData] = await Promise.all([
      api.get('/panel/me').catch(() => null),
      practiceApi.modes(),
    ])
    if (panelData) {
      stats.value = {
        total: panelData.total, practiced: panelData.practiced,
        wrong: panelData.wrong, marked: panelData.marked,
        type_dist: modeData.type_dist || {},
      }
      recentExams.value = panelData.recent_exams || []
    } else {
      stats.value = modeData
    }
    recent.value = await api.get('/records/practice/recent')
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.panel { max-width: 960px; }
.hello { font-size: 20px; font-weight: 600; margin-bottom: 20px; }
.stat-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 16px; margin-bottom: 24px; }
.stat-card { background: #fff; border-radius: 8px; padding: 20px; text-align: center; border: 1px solid #ebeef5; }
.stat-card.warn .stat-num { color: #f56c6c; }
.stat-num { font-size: 28px; font-weight: 700; color: var(--brand-primary); }
.stat-label { color: #909399; font-size: 13px; margin-top: 4px; }
.progress-box { background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 24px; border: 1px solid #ebeef5; }
.progress-text { margin-bottom: 8px; font-size: 14px; color: #606266; }
.section-title { font-size: 16px; font-weight: 600; margin: 24px 0 12px; }
.shortcuts { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
.shortcut { background: #fff; border: 1px solid #ebeef5; border-radius: 8px; padding: 18px; display: flex; flex-direction: column; align-items: center; gap: 8px; cursor: pointer; transition: all .2s; }
.shortcut:hover { box-shadow: 0 4px 12px rgba(0,0,0,.08); transform: translateY(-2px); }
.shortcut .el-icon { font-size: 26px; color: var(--brand-primary); }
/* 手机端：统计卡片/快捷入口单列，表格横向滚动 */
@media (max-width: 767px) {
  .stat-cards { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .stat-card { padding: 14px 8px; }
  .stat-num { font-size: 22px; }
  .shortcuts { grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .shortcut { padding: 14px 6px; }
  .shortcut .el-icon { font-size: 22px; }
  .shortcut span { font-size: 12px; }
  .recent-tables .el-table { font-size: 12px; }
}
</style>
