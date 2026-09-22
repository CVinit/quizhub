<template>
  <div class="audit-page" v-loading="loading">
    <div class="toolbar">
      <span class="title">审计日志</span>
      <div class="filters">
        <el-select
          v-model="filters.action"
          placeholder="操作类型"
          clearable
          filterable
          style="width: 180px"
          @change="onFilterChange"
        >
          <el-option v-for="a in actions" :key="a" :label="a" :value="a" />
        </el-select>
        <el-select
          v-model="filters.target_type"
          placeholder="目标类型"
          clearable
          style="width: 140px"
          @change="onFilterChange"
        >
          <el-option v-for="t in targetTypes" :key="t" :label="t" :value="t" />
        </el-select>
        <el-button type="primary" @click="onFilterChange">查询</el-button>
      </div>
    </div>

    <el-table :data="displayRows" border v-loading="loading" class="mobile-table-hidden">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="created_at" label="时间" width="180">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column prop="actor" label="操作人" width="160" />
      <el-table-column prop="action" label="操作" width="180" />
      <el-table-column prop="target_type" label="目标类型" width="120" />
      <el-table-column prop="target_id" label="目标ID" width="80" />
      <el-table-column label="详情" min-width="240">
        <template #default="{ row }">
          <el-tooltip v-if="row.detail" :content="row.detailPretty" placement="top" :show-after="200">
            <span class="detail-cell">{{ row.detailText }}</span>
          </el-tooltip>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="ip" label="IP" width="120" />
    </el-table>

    <!-- 手机端：审计日志卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in displayRows" :key="row.id">
        <div class="mc-title">#{{ row.id }} · {{ row.action }}</div>
        <div class="mc-row"><span class="mc-label">时间</span>{{ formatDateTime(row.created_at) }}</div>
        <div class="mc-row"><span class="mc-label">操作人</span>{{ row.actor }}</div>
        <div class="mc-row"><span class="mc-label">目标</span>{{ row.target_type }} #{{ row.target_id }}</div>
        <div class="mc-row" v-if="row.detail">
          <span class="mc-label">详情</span><span class="detail-cell">{{ row.detailText }}</span>
        </div>
        <div class="mc-row" v-if="row.ip"><span class="mc-label">IP</span>{{ row.ip }}</div>
      </div>
    </div>

    <el-pagination
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      :small="isMobile"
      style="margin-top: 16px; justify-content: flex-end; display: flex"
      @current-change="load"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '@/api/http'
import { useResponsive } from '@/composables/useResponsive'
import { formatDateTime } from '@/utils/format'

const { isMobile } = useResponsive()

/** 审计日志行（后端 /admin/audit-logs 返回）。 */
interface AuditLogRow {
  id: number
  created_at: string
  actor: string
  action: string
  target_type: string
  target_id: number | null
  detail?: Record<string, unknown> | null
  ip?: string
}

const loading = ref(false)
const rows = ref<AuditLogRow[]>([])
const page = ref(1)
const pageSize = 20
const total = ref(0)
const filters = reactive({ action: '', target_type: '' })
/**
 * 可筛选的审计动作。
 *
 * 与后端各处 audit_log(...) 的取值保持一致（含归档/删除/重置等低频动作），
 * 否则这些日志只能靠 target_type 间接筛出来。下拉为 filterable 且无 allow-create，
 * 漏掉的值在界面上无法选择。
 */
const actions = [
  'group.create',
  'group.update',
  'group.delete',
  'question.create',
  'question.update',
  'question.delete',
  'question.import',
  'question_bank.create',
  'question_bank.update',
  'question_bank.delete',
  'user.create',
  'user.import',
  'user.approve',
  'user.enable',
  'user.disable',
  'user.delete',
  'user.reset_password',
  'user.update',
  'user.assign_groups',
  'exam.create',
  'exam.update',
  'exam.publish',
  'exam.archive',
  'exam.unarchive',
  'exam.delete',
  'exam.reset_attempts',
  'exam_template.create',
  'exam_template.delete',
  'review.submit',
  'review.publish_results',
  'settings.update',
  'settings.logo_upload',
]
const targetTypes = [
  'group',
  'question',
  'questions',
  'question_bank',
  'user',
  'exam',
  'paper_template',
  'short_answer_review',
  'setting',
]

/** 渲染行：预置序列化后的详情文本，避免在模板中反复 JSON.stringify。 */
interface AuditLogRowView extends AuditLogRow {
  detailText: string
  detailPretty: string
}

const displayRows = computed<AuditLogRowView[]>(() =>
  rows.value.map((r) => ({
    ...r,
    detailText: r.detail ? JSON.stringify(r.detail) : '',
    detailPretty: r.detail ? JSON.stringify(r.detail, null, 2) : '',
  })),
)

/** 列表加载序号：快速切筛选/翻页会并发多个请求，只接受最新一次的结果。 */
let loadSeq = 0

const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    const params: Record<string, string | number> = { page: page.value, page_size: pageSize }
    if (filters.action) params.action = filters.action
    if (filters.target_type) params.target_type = filters.target_type
    const data = await api.get<{ items: AuditLogRow[]; total: number }>('/admin/audit-logs', { params })
    if (seq !== loadSeq) return
    rows.value = data.items
    total.value = data.total
  } catch {
    // 加载失败：http 拦截器已提示；保留当前列表，避免把失败误显示为“暂无日志”
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

/** 筛选条件变化：先回到第 1 页，否则新结果集不足当前页时会出现“空列表”。 */
const onFilterChange = () => {
  page.value = 1
  return load()
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
.muted {
  color: var(--el-text-color-disabled);
}
.detail-cell {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.mobile-card-list .detail-cell {
  max-width: 200px;
}
@media (max-width: 767px) {
  .mobile-card-list .mc-row {
    align-items: flex-start;
  }
  .mobile-card-list .mc-row > span:last-child {
    text-align: right;
    min-width: 0;
  }
}
</style>
