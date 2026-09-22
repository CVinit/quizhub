<template>
  <div class="panel" v-loading="loading">
    <div class="hello">你好，{{ auth.user?.name || auth.user?.email }}</div>

    <el-alert
      v-if="panelError"
      :title="panelError"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    />

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

    <div class="stat-cards secondary">
      <div class="stat-card">
        <div class="stat-num">{{ mockAvg === null ? '—' : `${mockAvg}` }}</div>
        <div class="stat-label">模拟考试平均分{{ mockAttempts ? `（${mockAttempts} 次）` : '' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-num">{{ formalBest === null ? '—' : `${formalBest}` }}</div>
        <div class="stat-label">正式考试最高分</div>
      </div>
    </div>

    <div class="progress-box" v-if="stats.total">
      <div class="progress-text">学习进度 {{ stats.practiced }} / {{ stats.total }}</div>
      <el-progress :percentage="progressPct" :stroke-width="10" />
    </div>

    <div class="section-title">快捷入口</div>
    <div class="shortcuts">
      <button type="button" class="shortcut" @click="$router.push('/answer')">
        <el-icon><Document /></el-icon><span>顺序练习</span>
      </button>
      <button type="button" class="shortcut" @click="$router.push('/answer/random')">
        <el-icon><Refresh /></el-icon><span>随机抽题</span>
      </button>
      <button type="button" class="shortcut" @click="$router.push('/exam')">
        <el-icon><Files /></el-icon><span>考试中心</span>
      </button>
      <button type="button" class="shortcut" @click="$router.push('/wrong')">
        <el-icon><Warning /></el-icon><span>错题本</span>
      </button>
      <button type="button" class="shortcut" @click="$router.push('/marks')">
        <el-icon><Collection /></el-icon><span>我的标记</span>
      </button>
    </div>

    <div class="recent-tables">
      <div class="section-title">最近考试</div>
      <el-table :data="recentExams" border v-if="recentExams.length && !isMobile" size="small">
        <el-table-column prop="name" label="考试" min-width="160" />
        <el-table-column label="得分" width="120">
          <template #default="{ row }">{{ row.published ? `${row.score} / ${row.total_score}` : '待复核' }}</template>
        </el-table-column>
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.published" :type="row.passed ? 'success' : 'danger'" size="small">{{
              row.passed ? '通过' : '未通过'
            }}</el-tag>
            <el-tag v-else type="warning" size="small">待复核</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <!-- 手机端：考试卡片 -->
      <div class="mobile-card-list" v-if="isMobile && recentExams.length">
        <div class="mc" v-for="row in recentExams" :key="row.session_id ?? row.name">
          <div class="mc-title">{{ row.name }}</div>
          <div class="mc-row">
            <span class="mc-label">结果</span>
            <el-tag v-if="row.published" :type="row.passed ? 'success' : 'danger'" size="small">{{
              row.passed ? '通过' : '未通过'
            }}</el-tag>
            <el-tag v-else type="warning" size="small">待复核</el-tag>
          </div>
          <div class="mc-row" v-if="row.published">
            <span class="mc-label">得分</span>{{ row.score }} / {{ row.total_score }}
          </div>
        </div>
      </div>
      <el-empty v-if="!recentExams.length" description="还没有考试记录" :image-size="80" />

      <div class="section-title">最近练习</div>
      <el-table :data="recent" border v-if="recent.length && !isMobile" size="small">
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
      <!-- 手机端：练习卡片 -->
      <div class="mobile-card-list" v-if="isMobile && recent.length">
        <div class="mc" v-for="row in recent" :key="row.id ?? row.question">
          <div class="mc-row">
            <el-tag v-if="row.is_correct === true" type="success" size="small">正确</el-tag>
            <el-tag v-else-if="row.is_correct === false" type="danger" size="small">错误</el-tag>
            <el-tag v-else type="info" size="small">待自评</el-tag>
            <el-tag type="info" size="small" effect="plain">{{ row.type }}</el-tag>
          </div>
          <div class="mc-title">{{ row.question }}</div>
          <div class="mc-row" v-if="row.answered_at"><span class="mc-label">时间</span>{{ row.answered_at }}</div>
        </div>
      </div>
      <el-empty v-if="!recent.length" description="还没有练习记录，去答题吧" :image-size="80" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { practiceApi } from '@/api/practice'
import { Document, Refresh, Files, Warning, Collection } from '@/utils/icons'
import { api } from '@/api/http'
import { useResponsive } from '@/composables/useResponsive'

const auth = useAuthStore()
const { isMobile } = useResponsive()
const loading = ref(false)
const panelError = ref('')

interface PanelMe {
  total: number
  practiced: number
  wrong: number
  marked: number
  mock_avg_score?: number | null
  mock_attempts?: number
  recent_exams?: RecentExam[]
}
interface RecentExam {
  /** 后端 /panel/me 的 recent_exams 不返回结果行 id，只有会话 id（见 stats/panel.py） */
  session_id: number
  name: string
  type?: string
  published: boolean
  passed: boolean
  score: number
  total_score: number
}
interface RecentPractice {
  id?: number
  question: string
  type: string
  is_correct: boolean | null
  answered_at?: string
}

/** 概览卡片用到的统计字段（type_dist 等页面未展示的字段不再保留，避免无用状态）。 */
interface PanelStats {
  total: number
  practiced: number
  wrong: number
  marked: number
}

const stats = ref<PanelStats>({ total: 0, practiced: 0, wrong: 0, marked: 0 })
const recent = ref<RecentPractice[]>([])
const recentExams = ref<RecentExam[]>([])

const accuracy = computed(() => {
  if (!stats.value.practiced) return 0
  return Math.round(((stats.value.practiced - stats.value.wrong) / stats.value.practiced) * 100)
})
const progressPct = computed(() =>
  stats.value.total ? Math.round((stats.value.practiced / stats.value.total) * 100) : 0,
)

/** 模拟考试平均分（百分制，后端已归一化）；无记录为 null。 */
const mockAvg = ref<number | null>(null)
const mockAttempts = ref(0)
/** 正式考试最高分（百分制），无已发布成绩为 null。 */
const formalBest = computed(() => {
  const scores = recentExams.value
    .filter((e) => e.type !== 'mock' && e.published && e.total_score)
    .map((e) => Math.round((e.score / e.total_score) * 100))
  return scores.length ? Math.max(...scores) : null
})

const load = async () => {
  loading.value = true
  panelError.value = ''
  try {
    // 三块数据相互独立：任一失败不应清掉其余已取到的内容，
    // 也不要让失败静默变成“暂无记录”（用户会误以为确实没有数据）。
    const [panelRes, modeRes, recentRes] = await Promise.allSettled([
      api.get<PanelMe>('/panel/me'),
      practiceApi.modes(),
      api.get<RecentPractice[]>('/records/practice/recent'),
    ])
    if (panelRes.status === 'fulfilled') {
      const panelData = panelRes.value
      stats.value = {
        total: panelData.total,
        practiced: panelData.practiced,
        wrong: panelData.wrong,
        marked: panelData.marked,
      }
      recentExams.value = panelData.recent_exams || []
      mockAvg.value = panelData.mock_avg_score ?? null
      mockAttempts.value = panelData.mock_attempts || 0
    } else if (modeRes.status === 'fulfilled') {
      stats.value = modeRes.value
    }
    if (recentRes.status === 'fulfilled') recent.value = recentRes.value
    if ([panelRes, modeRes, recentRes].some((r) => r.status === 'rejected')) {
      panelError.value = '部分数据加载失败，请稍后重试'
    }
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.panel {
  max-width: 960px;
}
.hello {
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 20px;
}
.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
.stat-card {
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 20px;
  text-align: center;
  border: 1px solid var(--el-border-color-lighter);
}
.stat-card.warn .stat-num {
  color: var(--el-color-danger);
}
/* 次级卡行：模拟考试平均分 / 正式考试最高分，视觉上弱于主卡行 */
.stat-cards.secondary {
  margin-top: -8px;
}
.stat-cards.secondary .stat-card {
  padding: 14px;
}
.stat-cards.secondary .stat-num {
  font-size: 22px;
}
.stat-num {
  font-size: 28px;
  font-weight: 700;
  color: var(--brand-primary);
}
.stat-label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-top: 4px;
}
.progress-box {
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 16px 20px;
  margin-bottom: 24px;
  border: 1px solid var(--el-border-color-lighter);
}
.progress-text {
  margin-bottom: 8px;
  font-size: 14px;
  color: var(--el-text-color-regular);
}
.section-title {
  font-size: 16px;
  font-weight: 600;
  margin: 24px 0 12px;
}
.shortcuts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.shortcut {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  transition: all 0.2s;
  font: inherit;
  color: inherit;
}
.shortcut:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}
.shortcut .el-icon {
  font-size: 26px;
  color: var(--brand-primary);
}
/* 手机端：统计卡片/快捷入口单列收紧；最近考试/练习用卡片列表（模板内按 isMobile 切换） */
@media (max-width: 767px) {
  .panel {
    max-width: none;
  }
  .hello {
    font-size: 18px;
    margin-bottom: 14px;
  }
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
  .shortcuts {
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
  }
  .shortcut {
    padding: 14px 6px;
  }
  .shortcut .el-icon {
    font-size: 22px;
  }
  .shortcut span {
    font-size: 12px;
  }
}
</style>
