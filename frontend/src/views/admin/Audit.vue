<template>
  <div class="audit-page" v-loading="loading">
    <div class="toolbar">
      <span class="title">审计日志</span>
      <div class="filters">
        <el-select v-model="filters.action" placeholder="操作类型" clearable filterable style="width: 180px" @change="load">
          <el-option v-for="a in actions" :key="a" :label="a" :value="a" />
        </el-select>
        <el-select v-model="filters.target_type" placeholder="目标类型" clearable style="width: 140px" @change="load">
          <el-option v-for="t in targetTypes" :key="t" :label="t" :value="t" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
      </div>
    </div>

    <el-table :data="rows" border v-loading="loading" class="mobile-table-hidden">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="created_at" label="时间" width="180">
        <template #default="{ row }">{{ fmt(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="actor" label="操作人" width="160" />
      <el-table-column prop="action" label="操作" width="180" />
      <el-table-column prop="target_type" label="目标类型" width="120" />
      <el-table-column prop="target_id" label="目标ID" width="80" />
      <el-table-column label="详情" min-width="240">
        <template #default="{ row }">
          <span v-if="row.detail">{{ JSON.stringify(row.detail) }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="ip" label="IP" width="120" />
    </el-table>

    <!-- 手机端：审计日志卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title">#{{ row.id }} · {{ row.action }}</div>
        <div class="mc-row"><span class="mc-label">时间</span>{{ fmt(row.created_at) }}</div>
        <div class="mc-row"><span class="mc-label">操作人</span>{{ row.actor }}</div>
        <div class="mc-row"><span class="mc-label">目标</span>{{ row.target_type }} #{{ row.target_id }}</div>
        <div class="mc-row" v-if="row.detail"><span class="mc-label">详情</span>{{ JSON.stringify(row.detail) }}</div>
        <div class="mc-row" v-if="row.ip"><span class="mc-label">IP</span>{{ row.ip }}</div>
      </div>
    </div>

    <el-pagination
      v-model:current-page="page" :page-size="pageSize" :total="total"
      layout="total, prev, pager, next" :small="isMobile"
      style="margin-top: 16px; justify-content: flex-end; display: flex"
      @current-change="load" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '@/api/http'
import { useResponsive } from '@/composables/useResponsive'

const { isMobile } = useResponsive()

const loading = ref(false)
const rows = ref<any[]>([])
const page = ref(1)
const pageSize = 20
const total = ref(0)
const filters = reactive({ action: '', target_type: '' })
const actions = [
  'group.create', 'group.update', 'group.delete',
  'question.create', 'question.update', 'question.delete', 'question.import',
  'question_bank.create',
  'user.approve', 'user.enable', 'user.disable', 'user.reset_password', 'user.update', 'user.assign_groups',
  'exam.create', 'exam.update', 'exam.publish',
  'exam_template.create',
  'review.submit', 'review.publish_results',
  'settings.update',
]
const targetTypes = ['group', 'question', 'questions', 'question_bank', 'user', 'exam', 'paper_template', 'short_answer_review', 'setting']

const fmt = (iso: string) => { if (!iso) return ''; const d = new Date(iso); return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('zh-CN', { hour12: false }) }

const load = async () => {
  loading.value = true
  try {
    const params: Record<string, any> = { page: page.value, page_size: pageSize }
    if (filters.action) params.action = filters.action
    if (filters.target_type) params.target_type = filters.target_type
    const data = await api.get('/admin/audit-logs', { params })
    rows.value = data.items
    total.value = data.total
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
.muted { color: #c0c4cc; }
</style>