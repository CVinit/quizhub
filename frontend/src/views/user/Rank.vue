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
        <el-select v-model="range" style="width: 120px" aria-label="统计范围" @change="load">
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
      <div class="rank-item" v-for="(r, i) in rows" :key="r.user_id ?? r.name" :class="{ top: i < 3, me: r.is_me }">
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
import { api, httpStatusOf } from '@/api/http'

/** 排行条目（/rank）。 */
interface RankRow {
  /** 个人榜有；分组榜无（只有 name） */
  user_id?: number
  name: string
  value: number
  is_me?: boolean
}

const router = useRouter()
const loading = ref(false)
const dimension = ref('accuracy')
const scope = ref('self')
const range = ref('7d')
const rows = ref<RankRow[]>([])

const formatValue = (v: number) => {
  if (dimension.value === 'accuracy') return v + '%'
  if (dimension.value === 'score') return v.toFixed(1)
  return v
}

/** 请求序号：只接受最后一次发起请求的结果，避免快速切换维度/范围时旧响应覆盖新响应。 */
let reqSeq = 0

const load = async () => {
  const seq = ++reqSeq
  loading.value = true
  try {
    const data = await api.get<RankRow[]>('/rank', {
      params: { dimension: dimension.value, scope: scope.value, range: range.value },
    })
    if (seq !== reqSeq) return
    rows.value = data || []
  } catch (err) {
    if (seq !== reqSeq) return
    rows.value = []
    // 排行被后台关闭（403）：直接跳回首页，避免停留在空白排行页
    if (httpStatusOf(err) === 403) {
      router.replace('/')
    }
  } finally {
    if (seq === reqSeq) loading.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.rank-page {
  max-width: 800px;
}
.rank-page h2 {
  margin-bottom: 20px;
}
.toolbar {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
  flex-wrap: wrap;
  align-items: center;
}
.toolbar-row {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}
.rank-list {
  background: var(--el-bg-color);
  border-radius: 8px;
  border: 1px solid var(--el-border-color-lighter);
  overflow: hidden;
}
.rank-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.rank-item:last-child {
  border-bottom: none;
}
.rank-item.me {
  background: var(--brand-primary-light-9);
}
.rank-item.top .rank-name {
  font-weight: 600;
}
.rank-no {
  width: 32px;
  height: 32px;
  line-height: 32px;
  text-align: center;
  border-radius: 50%;
  background: var(--el-color-info-light-9);
  font-weight: 700;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}
.rank-no.no-1 {
  background: var(--el-color-danger);
  color: var(--el-color-white);
}
.rank-no.no-2 {
  background: var(--el-color-warning);
  color: var(--el-color-white);
}
.rank-no.no-3 {
  background: var(--el-text-color-disabled);
  color: var(--el-color-white);
}
.rank-name {
  flex: 1;
  word-break: break-all;
}
.rank-value {
  font-weight: 700;
  color: var(--brand-primary);
  font-size: 16px;
  flex-shrink: 0;
}
@media (max-width: 767px) {
  .toolbar {
    gap: 8px;
  }
  .toolbar-row {
    width: 100%;
    gap: 8px;
  }
  .toolbar-row .el-radio-group {
    flex: 1;
    min-width: 0;
  }
  .toolbar-row .el-select {
    flex: 0 0 110px;
  }
  .rank-item {
    gap: 12px;
    padding: 12px 14px;
  }
  .rank-value {
    font-size: 14px;
  }
}
</style>
