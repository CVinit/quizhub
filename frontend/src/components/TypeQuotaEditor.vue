<template>
  <div class="tqe">
    <div class="tqe-grid">
      <div v-for="t in types" :key="t" class="tqe-row" :class="{ 'tqe-row-over': isOver(t) }">
        <span class="tqe-name">{{ t }}</span>
        <el-input-number
          :model-value="modelValue[t] || 0"
          :min="0"
          :max="maxPerType"
          size="small"
          controls-position="right"
          @update:model-value="(v: number | undefined) => update(t, v)"
        />
        <span class="tqe-avail" :class="{ 'tqe-over': isOver(t) }">
          <template v-if="statsLoading">统计中…</template>
          <template v-else-if="statsFailed">可用题量获取失败</template>
          <template v-else-if="isOver(t)">超出可用 {{ available(t) }} 题</template>
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
import { QUESTION_TYPES } from '@/constants/question'

const props = withDefaults(
  defineProps<{
    /** 题型 → 配额数量（未配置时为空对象） */
    modelValue?: Record<string, number>
    /** 组卷来源筛选条件，变化时重新统计各题型可用题量 */
    sources?: { bank_ids?: number[]; group_ids?: number[]; tags?: string[] }
    /** 上游“最大题数”约束（可选）：超出时给出提示 */
    maxQuestions?: number
  }>(),
  { modelValue: () => ({}), maxQuestions: 0 },
)

const emit = defineEmits<{ 'update:modelValue': [value: Record<string, number>] }>()

const types = QUESTION_TYPES
/** 单一题型的配比兜底上限：组卷总量上限由 sources/后端另行约束，这里只防止手滑填出离谱数字。 */
const MAX_PER_TYPE = 100
/**
 * 实际上限：不得低于调用方的「最大题数」。
 *
 * el-input-number 会在挂载时按 :max 校验 modelValue 并**静默回写**被钳制的值，
 * 因此固定 100 会让已存的大配额（如 单选题 150 + max_questions 200）仅仅因为
 * 打开编辑弹窗就被改小，保存后持久化丢失。后端对单题型无上限，故以调用方约束为准。
 */
const maxPerType = computed(() => Math.max(MAX_PER_TYPE, props.maxQuestions || 0))
const stats = ref<Record<string, number>>({})
const statsLoading = ref(false)
const statsFailed = ref(false)

const quota = (t: string) => props.modelValue[t] || 0
const available = (t: string) => stats.value[t] ?? 0
// 统计失败时 stats 为空，"可用 0 题"会被误判成"超出可用题量"（整行飘红 + 危险标签）。
// 失败时一律不判定超额，交由 statsFailed 的显式文案说明情况。
const isOver = (t: string) => !statsFailed.value && quota(t) > 0 && quota(t) > available(t)

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

// 请求序号：来源条件可在请求在途时变化，乱序返回会把上一个条件的题量当成当前条件的
// （配额校验依据的就是这些数字）。只接受最新一次请求的结果。
let statsSeq = 0

watch(
  sourceKey,
  async () => {
    const seq = ++statsSeq
    statsLoading.value = true
    statsFailed.value = false
    try {
      const res = await questionApi.typeStats(props.sources || {})
      if (seq !== statsSeq) return
      stats.value = res
    } catch {
      if (seq !== statsSeq) return
      // 统计失败时保留组件可用，但必须显式告知：否则“可用 0 题”会被误读为范围内没有题
      stats.value = {}
      statsFailed.value = true
    } finally {
      if (seq === statsSeq) statsLoading.value = false
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.tqe {
  width: 100%;
}
.tqe-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 24px;
}
.tqe-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 6px;
  border-radius: 4px;
}
.tqe-row-over {
  background: var(--el-color-danger-light-9);
}
.tqe-name {
  font-size: 14px;
  flex-shrink: 0;
}
.tqe-avail {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}
.tqe-avail.tqe-over {
  color: var(--el-color-danger);
  font-weight: 600;
}
.tqe-summary {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
}
.tqe-summary-main {
  color: var(--el-text-color-primary);
}
.tqe-summary-main b {
  color: var(--brand-primary);
  font-size: 15px;
}
.tqe-detail {
  color: var(--el-text-color-secondary);
}
.tqe-tags {
  margin-top: 6px;
}
@media (max-width: 767px) {
  .tqe-grid {
    grid-template-columns: 1fr;
    gap: 8px;
  }
}
</style>
