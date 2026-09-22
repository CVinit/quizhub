<template>
  <el-dialog
    :model-value="modelValue"
    title="模拟考试设置"
    width="620px"
    :close-on-click-modal="false"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
    @open="onOpen"
  >
    <div v-loading="loading">
      <!-- 题库范围 -->
      <div class="sec">
        <div class="sec-head">
          <span class="sec-title">题库范围</span>
          <el-button link type="primary" size="small" @click="toggleAll">
            {{ allSelected ? '取消全选' : '全选' }}
          </el-button>
        </div>
        <el-checkbox-group v-model="selectedBanks" class="bank-list">
          <el-checkbox v-for="b in banks" :key="b.id" :value="b.id" class="bank-item">
            <span class="bank-name">{{ b.name }}</span>
            <span class="bank-count">{{ b.question_count }} 题</span>
          </el-checkbox>
        </el-checkbox-group>
        <div class="hint">
          已选 {{ selectedBanks.length }} 个题库，共 {{ availTotal }} 题
          <span v-if="selectedBanks.length === 0" class="warn">（未选择时按全部题库出题）</span>
        </div>
      </div>

      <!-- 题量 -->
      <div class="sec">
        <div class="sec-head"><span class="sec-title">题量</span></div>
        <div class="size-row">
          <el-button
            v-for="s in sizePresets"
            :key="s"
            :type="size === s ? 'primary' : 'default'"
            size="small"
            @click="setSize(s)"
          >
            {{ s }}
          </el-button>
          <el-input-number
            v-model="size"
            :min="1"
            :max="maxQuestions"
            size="small"
            controls-position="right"
            class="size-custom"
            @change="onSizeChange"
          />
        </div>
        <div v-if="sizeDowngraded" class="hint warn">该范围共 {{ availTotal }} 题，题量已自动调整为 {{ size }} 题</div>
      </div>

      <!-- 题型比例 -->
      <div class="sec">
        <div class="sec-head">
          <span class="sec-title">题型比例</span>
          <div class="sec-actions">
            <el-checkbox v-model="objectiveOnly" @change="onObjectiveOnlyChange">仅客观题</el-checkbox>
            <el-button link type="primary" size="small" @click="autoAllocate">按可用题量自动分配</el-button>
          </div>
        </div>
        <div class="quota-grid">
          <div v-for="t in types" :key="t" class="quota-row" :class="{ over: isOver(t), disabled: available(t) === 0 }">
            <span class="quota-name">{{ t }}</span>
            <el-input-number
              v-model="quota[t]"
              :min="0"
              :max="available(t)"
              size="small"
              controls-position="right"
              :disabled="available(t) === 0"
              @change="onQuotaChange"
            />
            <span class="quota-avail" :class="{ 'quota-over': isOver(t) }">
              {{ isOver(t) ? `超出可用 ${available(t)} 题` : `可用 ${available(t)} 题` }}
            </span>
          </div>
        </div>
        <div class="sum" :class="{ bad: !quotaValid }">
          <template v-if="quotaValid">合计 {{ quotaSum }} 题 ✓ 与设定题量一致</template>
          <template v-else>
            合计 {{ quotaSum }} 题 ⚠ 与设定题量 {{ size }} 不一致，{{
              quotaSum < size ? `还差 ${size - quotaSum} 题` : `超出 ${quotaSum - size} 题`
            }}
          </template>
        </div>
      </div>

      <!-- 其他 -->
      <div class="sec">
        <el-checkbox v-model="showAnalysis">交卷后回显答案解析</el-checkbox>
      </div>

      <div v-if="preview" class="preview">预计：{{ preview.count }} 题 / {{ preview.total_score }} 分</div>
      <div v-else-if="previewError" class="preview warn">{{ previewError }}</div>
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="starting" :disabled="!canStart" @click="onStart"> 开始考试 </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { examApi, type MockBank, type MockPaperSpec, type MockPreview } from '@/api/exam'
import { extractErrorDetail, errorDetailOf } from '@/api/http'
import { MOCK_DEFAULT_SIZE, MOCK_SIZE_PRESETS, QUESTION_TYPES } from '@/constants/question'
import { confirmBox } from '@/utils/dialog'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  started: [sessionId: number]
}>()

const types = QUESTION_TYPES
const loading = ref(false)
const starting = ref(false)
const banks = ref<MockBank[]>([])
const sizePresets = ref<number[]>([...MOCK_SIZE_PRESETS])
const maxQuestions = ref(100)
const selectedBanks = ref<number[]>([])
const size = ref(MOCK_DEFAULT_SIZE)
const quota = reactive<Record<string, number>>({})
const objectiveOnly = ref(false)
const showAnalysis = ref(true)
const preview = ref<MockPreview | null>(null)
const previewError = ref('')
const sizeDowngraded = ref(false)

const allSelected = computed(() => banks.value.length > 0 && selectedBanks.value.length === banks.value.length)

/**
 * 已选题库范围内各题型可用题量（未选任何题库时按全部题库合计）。
 *
 * 勾选「仅客观题」时把简答题清零：后端会丢弃简答配额（mock.py: if objective_only and manual:
 * manual.pop("简答题")），若这里仍按原值可用，界面会显示「合计 N 题 ✓ 与设定题量一致」
 * 而后端预览报 400「各题型数量合计 X 题，与设定题量 Y 题不一致」，用户无法自解。
 */
const availByType = computed<Record<string, number>>(() => {
  const pool = selectedBanks.value.length ? banks.value.filter((b) => selectedBanks.value.includes(b.id)) : banks.value
  const out: Record<string, number> = {}
  for (const t of types) out[t] = pool.reduce((s, b) => s + (b.type_stats?.[t] || 0), 0)
  if (objectiveOnly.value) out['简答题'] = 0
  return out
})

const availTotal = computed(() => types.reduce((s, t) => s + available(t), 0))
const available = (t: string) => availByType.value[t] || 0
const isOver = (t: string) => (quota[t] || 0) > available(t)
const quotaSum = computed(() => types.reduce((s, t) => s + (quota[t] || 0), 0))
const quotaValid = computed(() => quotaSum.value === size.value && !types.some((t) => isOver(t)))
const canStart = computed(() => quotaValid.value && !previewError.value && availTotal.value > 0)

/** 按可用题量比例分配（最大余数法），与后端 allocate_quota 口径一致。 */
const autoAllocate = () => {
  const avail = { ...availByType.value }
  for (const t of types) quota[t] = 0
  const entries = types.filter((t) => avail[t] > 0)
  const totalWeight = entries.reduce((s, t) => s + avail[t], 0)
  if (!entries.length || totalWeight <= 0) return
  const exact = entries.map((t) => ({ t, v: (size.value * avail[t]) / totalWeight }))
  let allocated = 0
  for (const e of exact) {
    const f = Math.floor(e.v)
    quota[e.t] = f
    allocated += f
  }
  // 余数从大到小补足；并列时按题型顺序靠前者优先（与后端稳定排序一致）
  const rest = exact
    .map((e, i) => ({ t: e.t, rem: e.v - Math.floor(e.v), i }))
    .sort((a, b) => b.rem - a.rem || a.i - b.i)
  for (let k = 0; k < size.value - allocated; k++) quota[rest[k % rest.length].t] += 1
  // 逐题型钳制到可用量
  for (const t of types) quota[t] = Math.min(quota[t] || 0, avail[t])
}

/** 预览请求序号：防抖不会取消在途请求，只接受最新一次的结果，避免旧响应覆盖新设置。 */
let previewSeq = 0

/** 请求组卷预览（设置变化后防抖调用）。 */
const refreshPreview = async () => {
  const seq = ++previewSeq
  previewError.value = ''
  preview.value = null
  if (!canStart.value) return
  try {
    const res = await examApi.previewMock(buildSpec())
    if (seq !== previewSeq) return
    preview.value = res
    sizeDowngraded.value = res.size_downgraded
  } catch (e) {
    if (seq !== previewSeq) return
    previewError.value = extractErrorDetail(errorDetailOf(e))
  }
}

/**
 * 组卷设置（预览与开考共用）。
 *
 * 注意：后端 /mock/preview 的入参是 MockPaperIn（extra="forbid" 且不含 show_analysis），
 * /mock/start 是 MockStartIn（多一个 show_analysis）。因此这里只构造两者共有的字段，
 * show_analysis 由 onStart 单独附加，避免预览接口因多余字段报 422。
 */
const buildSpec = (): MockPaperSpec => ({
  bank_ids: selectedBanks.value.length ? [...selectedBanks.value] : null,
  size: size.value,
  type_quota: Object.fromEntries(types.filter((t) => (quota[t] || 0) > 0).map((t) => [t, quota[t]])),
  allocation: 'auto',
  objective_only: objectiveOnly.value,
})

// 题库范围变化：可用量变了，需按“不足则下调 + 重算配额”统一处理（与题量变化同口径）
watch(selectedBanks, () => {
  onSizeChange()
})

const setSize = (s: number) => {
  size.value = s
  onSizeChange()
}

/**
 * 题量/范围变化：仅在“总题量不足”时自动下调题量并提示。
 * 不重算各题型配额——重算会就地覆盖管理员手填的题型比例（此前每次改题量、改题库
 * 甚至切回前台都会把手工配比冲掉）。只把每项钳制到可用量，差额由 quotaValid 提示。
 */
const onSizeChange = () => {
  if (size.value > availTotal.value && availTotal.value > 0) {
    size.value = availTotal.value
    sizeDowngraded.value = true
  } else {
    sizeDowngraded.value = false
  }
  // 可用量变化后，手填值可能已超出该题型上限，逐项收窄（不是重新分配）
  for (const t of types) {
    const cap = available(t)
    if ((quota[t] || 0) > cap) quota[t] = cap
  }
}

const onQuotaChange = () => {
  sizeDowngraded.value = false
}

const onObjectiveOnlyChange = () => {
  // 勾选会把简答可用量清零。先按下调后的总量收窄，再按可用题量重分配：
  // 否则简答配额被钳成 0 后合计对不上题量，「开始考试」会一直不可用。
  // 这是用户主动改约束，不是题量/题库变化，不保留上一份手填比例。
  onSizeChange()
  autoAllocate()
}

/** 全选/取消全选。取消全选后范围为空，交由后端按"全部开放题库"处理。 */
const toggleAll = () => {
  selectedBanks.value = allSelected.value ? [] : banks.value.map((b) => b.id)
}

/**
 * 打开弹窗的序号。
 *
 * 重开弹窗时上一次的 mockOptions 可能仍在途：迟到的成功会覆盖新一次的选项与全选结果，
 * 迟到的失败会把刚刚加载好的题库清空（canStart 变假、用户无法开考）。用序号丢弃过期响应。
 */
let openSeq = 0

const onOpen = async () => {
  const seq = ++openSeq
  loading.value = true
  // 清掉上一次打开残留的预览与错误：否则重开时会先显示上一次的「预计 N 题」或旧报错
  previewSeq++ // 作废仍在途的预览响应，避免其写回旧设置的结果
  preview.value = null
  previewError.value = ''
  try {
    const opts = await examApi.mockOptions()
    if (seq !== openSeq) return
    banks.value = opts.banks || []
    sizePresets.value = opts.size_presets?.length ? opts.size_presets : [...MOCK_SIZE_PRESETS]
    maxQuestions.value = opts.max_questions || 100
    size.value = opts.default_size || MOCK_DEFAULT_SIZE
    // 默认全选所有开放题库
    selectedBanks.value = banks.value.map((b) => b.id)
    objectiveOnly.value = false
    showAnalysis.value = true
    sizeDowngraded.value = false
    onSizeChange()
    // 首次配置按可用题量给一份默认配比；之后的手工修改不再被自动覆盖
    autoAllocate()
  } catch {
    if (seq !== openSeq) return
    // http 拦截器已提示。选项拉取失败时必须清空题库：否则上一次的 banks/selectedBanks/size
    // 仍在，canStart 可能为真，用户会用与服务端不一致的陈旧设置开考。
    banks.value = []
    selectedBanks.value = []
    preview.value = null
  } finally {
    if (seq === openSeq) loading.value = false
  }
}

const onStart = async () => {
  const ok = await confirmBox('模拟考试将按您的设置组卷并开始计时，确认开始？', '模拟考试', { type: 'info' })
  if (!ok) return
  starting.value = true
  try {
    // show_analysis 仅 /mock/start（MockStartIn）接受，预览接口不接受，故此处附加
    const session = await examApi.startMock({ ...buildSpec(), show_analysis: showAnalysis.value })
    emit('update:modelValue', false)
    emit('started', session.session_id)
  } catch {
    // 拦截器已统一弹出错误提示（含 422 的字段级信息）
  } finally {
    starting.value = false
  }
}

// 预览防抖：题量/配额/范围变化后 300ms 请求一次
let timer: ReturnType<typeof setTimeout> | undefined
watch(
  () => [size.value, objectiveOnly.value, JSON.stringify(quota), JSON.stringify(selectedBanks.value)],
  () => {
    clearTimeout(timer)
    timer = setTimeout(refreshPreview, 300)
  },
)

// 组件卸载时清掉未触发的防抖，避免弹窗销毁后仍发起预览请求
onBeforeUnmount(() => clearTimeout(timer))
</script>

<style scoped>
.sec {
  margin-bottom: 18px;
}
.sec-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.sec-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.sec-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.bank-list {
  max-height: 168px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.bank-item {
  height: 28px;
}
.bank-name {
  font-size: 13px;
}
.bank-count {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 6px;
}
.hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 6px;
}
.hint.warn,
.warn {
  color: var(--el-color-warning);
}
.size-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.size-custom {
  width: 110px;
}
.quota-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 20px;
}
.quota-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px;
  border-radius: 4px;
}
.quota-row.over {
  background: var(--el-color-danger-light-9);
}
.quota-row.disabled {
  opacity: 0.5;
}
.quota-name {
  font-size: 13px;
  width: 56px;
  flex-shrink: 0;
}
.quota-avail {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}
.quota-avail.quota-over {
  color: var(--el-color-danger);
  font-weight: 600;
}
.sum {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
  color: var(--el-color-success);
}
.sum.bad {
  color: var(--el-color-danger);
}
.preview {
  margin-top: 4px;
  padding: 8px 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-size: 13px;
  color: var(--el-text-color-primary);
}
@media (max-width: 767px) {
  .quota-grid {
    grid-template-columns: 1fr;
  }
}
</style>
