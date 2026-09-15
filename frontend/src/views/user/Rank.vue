<template>
  <div class="rank-page" v-loading="loading">
    <h2>排行榜</h2>

    <div class="toolbar">
      <div class="toolbar-row">
        <el-radio-group v-model="dimension" @change="load">
          <el-radio-button value="accuracy">正确率</el-radio-button>
          <el-radio-button value="count">答题数</el-radio-button>
          <el-radio-button value="score">考试均分</el-radio-button>
          <el-radio-button value="streak">连续天数</el-radio-button>
        </el-radio-group>
        <el-select v-model="range" style="width: 120px" @change="load">
          <el-option label="近 7 天" value="7d" />
          <el-option label="近 30 天" value="30d" />
          <el-option label="全部" value="all" />
        </el-select>
      </div>
      <el-radio-group v-model="scope" @change="load">
        <el-radio-button value="self">个人</el-radio-button>
        <el-radio-button value="group">分组</el-radio-button>
      </el-radio-group>
    </div>

    <el-empty v-if="!loading && rows.length === 0" description="暂无排行数据" :image-size="120" />

    <div class="rank-list" v-else>
      <div class="rank-item" v-for="(r, i) in rows" :key="i" :class="{ top: i < 3, me: r.is_me }">
        <div class="rank-no" :class="`no-${i + 1}`">{{ i + 1 }}</div>
        <div class="rank-name">{{ r.name }}</div>
        <div class="rank-value">{{ formatValue(r.value) }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/http'

const router = useRouter()
const loading = ref(false)
const dimension = ref('accuracy')
const scope = ref('self')
const range = ref('7d')
const rows = ref<any[]>([])

const formatValue = (v: number) => {
  if (dimension.value === 'accuracy') return v + '%'
  if (dimension.value === 'score') return v.toFixed(1)
  return v
}

const load = async () => {
  loading.value = true
  try {
    const data = await api.get<any[]>('/rank', { params: { dimension: dimension.value, scope: scope.value, range: range.value } })
    rows.value = data || []
  } catch (err: any) {
    rows.value = []
    // 排行被后台关闭（403）：直接跳回首页，避免停留在空白排行页
    if (err.response?.status === 403) {
      router.replace('/')
    }
  } finally {
    loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.rank-page { max-width: 800px; }
.rank-page h2 { margin-bottom: 20px; }
.toolbar { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; align-items: center; }
.toolbar-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.rank-list { background: #fff; border-radius: 8px; border: 1px solid #ebeef5; overflow: hidden; }
.rank-item { display: flex; align-items: center; gap: 16px; padding: 14px 20px; border-bottom: 1px solid #f5f5f5; }
.rank-item:last-child { border-bottom: none; }
.rank-item.me { background: var(--brand-primary-light-9); }
.rank-item.top .rank-name { font-weight: 600; }
.rank-no { width: 32px; height: 32px; line-height: 32px; text-align: center; border-radius: 50%; background: #f4f4f5; font-weight: 700; color: #909399; flex-shrink: 0; }
.rank-no.no-1 { background: #f56c6c; color: #fff; }
.rank-no.no-2 { background: #e6a23c; color: #fff; }
.rank-no.no-3 { background: #c0c4cc; color: #fff; }
.rank-name { flex: 1; word-break: break-all; }
.rank-value { font-weight: 700; color: var(--brand-primary); font-size: 16px; flex-shrink: 0; }
@media (max-width: 767px) {
  .toolbar { gap: 8px; }
  .toolbar-row { width: 100%; gap: 8px; }
  .toolbar-row .el-radio-group { flex: 1; min-width: 0; }
  .toolbar-row .el-select { flex: 0 0 110px; }
  .rank-item { gap: 12px; padding: 12px 14px; }
  .rank-value { font-size: 14px; }
}
</style>
