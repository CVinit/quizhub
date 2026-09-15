<template>
  <div class="tqe">
    <div class="tqe-grid">
      <div v-for="t in types" :key="t" class="tqe-row" :class="{ 'tqe-row-over': isOver(t) }">
        <span class="tqe-name">{{ t }}</span>
        <el-input-number
          :model-value="modelValue[t] || 0" :min="0" :max="maxPerType" size="small"
          controls-position="right"
          @update:model-value="(v: number | undefined) => update(t, v)" />
        <span class="tqe-avail" :class="{ 'tqe-over': isOver(t) }">
          <template v-if="statsLoading">统计中…</template>
          <template v-else-if="isOver(t)">超出可用 {{ available(t) }} 题</template>
          <template v-else-if="quota(t) > 0">可用 {{ available(t) }} 题</template>
          <template v-else>可用 {{ available(t) }} 题</template>
        </span>
      </div>
    </div>

    <!-- 明确指示：每种题型抽取多少题，合计多少题 -->
    <div class="tqe-summary">
      <div class="tqe-summary-main">
        共抽取 <b>{{ totalQuota }}</b> 题
        <span v-if="pickedTypes.length" class="tqe-detail">
          （{{ pickedTypes.map((t) => `${t} ${quota(t)} 题`).join('、') }}）
        </span>
      </div>
      <div class="tqe-tags">
        <el-tag v-if="overTypes.length" type="danger" size="small">
          {{ overTypes.join('、') }} 超出可用题量，实际抽题将不足配额
        </el-tag>
        <el-tag v-else-if="totalQuota === 0" type="info" size="small">尚未配置任何题型</el-tag>
        <el-tag v-else-if="maxQuestions && totalQuota > maxQuestions" type="warning" size="small">
          已超出最大题数 {{ maxQuestions }}，超出部分不会抽取
        </el-tag>
        <el-tag v-else type="success" size="small">配置有效</el-tag>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { questionApi } from '@/api/question'

const props = withDefaults(
  defineProps<{
    /** 题型 → 配额数量 */
    modelValue: Record<string, number>
    /** 组卷来源筛选条件，变化时重新统计各题型可用题量 */
    sources?: { bank_ids?: number[]; group_ids?: number[]; tags?: string[] }
    /** 上游“最大题数”约束（可选）：超出时给出提示 */
    maxQuestions?: number
  }>(),
  { maxQuestions: 0 },
)

const emit = defineEmits<{ 'update:modelValue': [value: Record<string, number>] }>()

const types = ['单选题', '多选题', '判断题', '填空题', '简答题', '拖拽题']
const maxPerType = 100
const stats = ref<Record<string, number>>({})
const statsLoading = ref(false)

const quota = (t: string) => props.modelValue[t] || 0
const available = (t: string) => stats.value[t] ?? 0
const isOver = (t: string) => quota(t) > 0 && quota(t) > available(t)

const totalQuota = computed(() => types.reduce((s, t) => s + quota(t), 0))
const pickedTypes = computed(() => types.filter((t) => quota(t) > 0))
const overTypes = computed(() => types.filter((t) => isOver(t)))

const update = (t: string, v: number | undefined) => {
  emit('update:modelValue', { ...props.modelValue, [t]: v || 0 })
}

// sources 传的是字面量对象，deep watch + JSON 序列化做去重，避免重复请求
const sourceKey = computed(() =>
  JSON.stringify([
    props.sources?.bank_ids ? [...props.sources.bank_ids].sort((a, b) => a - b) : [],
    props.sources?.group_ids ? [...props.sources.group_ids].sort((a, b) => a - b) : [],
    props.sources?.tags ? [...props.sources.tags].sort() : [],
  ]),
)

watch(
  sourceKey,
  async () => {
    statsLoading.value = true
    try {
      stats.value = await questionApi.typeStats(props.sources || {})
    } finally {
      statsLoading.value = false
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.tqe { width: 100%; }
.tqe-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; }
.tqe-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 2px 6px; border-radius: 4px;
}
.tqe-row-over { background: var(--el-color-danger-light-9); }
.tqe-name { font-size: 14px; flex-shrink: 0; }
.tqe-avail { font-size: 12px; color: #909399; white-space: nowrap; }
.tqe-avail.tqe-over { color: var(--el-color-danger); font-weight: 600; }
.tqe-summary { margin-top: 12px; padding-top: 10px; border-top: 1px solid #ebeef5; font-size: 13px; }
.tqe-summary-main { color: #303133; }
.tqe-summary-main b { color: var(--brand-primary, #e60012); font-size: 15px; }
.tqe-detail { color: #909399; }
.tqe-tags { margin-top: 6px; }
@media (max-width: 767px) {
  .tqe-grid { grid-template-columns: 1fr; gap: 8px; }
}
</style>
