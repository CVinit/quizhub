<template>
  <!-- 单选 / 多选 / 判断：选项按钮 -->
  <template v-if="question.type === '单选题'">
    <button
      v-for="(opt, i) in question.options || []"
      :key="i"
      type="button"
      class="option"
      :class="choiceClass(i)"
      :disabled="locked"
      :aria-pressed="picked === letter(i)"
      @click="onPick(letter(i))"
    >
      <span class="opt-letter">{{ letter(i) }}</span>
      <span class="opt-text">{{ opt }}</span>
    </button>
  </template>

  <template v-if="question.type === '多选题'">
    <button
      v-for="(opt, i) in question.options || []"
      :key="i"
      type="button"
      class="option"
      :class="multiClass(i)"
      :disabled="locked"
      :aria-pressed="multiPicked.includes(letter(i))"
      @click="onToggleMulti(letter(i))"
    >
      <span class="opt-letter">{{ letter(i) }}</span>
      <span class="opt-text">{{ opt }}</span>
    </button>
  </template>

  <template v-if="question.type === '判断题'">
    <button
      v-for="opt in JUDGE_OPTIONS"
      :key="opt"
      type="button"
      class="option"
      :class="judgeClass(opt)"
      :disabled="locked"
      :aria-pressed="picked === opt"
      @click="onPick(opt)"
    >
      <span class="opt-letter">{{ opt === '正确' ? '✓' : '✗' }}</span>
      <span class="opt-text">{{ opt }}</span>
    </button>
  </template>

  <!-- 填空：一空一个输入框 -->
  <template v-if="question.type === '填空题'">
    <div v-for="(blank, i) in blanks" :key="i" class="blank-row">
      <span class="blank-label">空{{ i + 1 }}</span>
      <el-input
        :model-value="blank"
        placeholder="请输入答案"
        :disabled="locked"
        :aria-label="`第 ${i + 1} 空答案`"
        @update:model-value="(v: string) => emit('update-blank', i, v)"
        @blur="emit('blur')"
      />
    </div>
  </template>

  <!-- 简答 -->
  <template v-if="question.type === '简答题'">
    <el-input
      :model-value="shortAns"
      type="textarea"
      :rows="shortRows"
      placeholder="请输入答案"
      :disabled="locked"
      aria-label="简答题作答"
      @update:model-value="(v: string) => emit('update:shortAns', v)"
      @blur="emit('blur')"
    />
  </template>

  <!-- 拖拽：点击或拖放把左侧题项放到右侧容器 -->
  <template v-if="question.type === '拖拽题'">
    <div class="drag-area">
      <div class="drag-source">
        <div
          v-for="(item, i) in shuffledLeft"
          :key="`${i}-${item}`"
          class="drag-item"
          :class="{ 'drag-picked': dragSource === item }"
          :draggable="!locked"
          role="button"
          :tabindex="locked ? -1 : 0"
          :aria-disabled="locked"
          :aria-pressed="dragSource === item"
          :aria-label="`选择「${item}」，再在右侧容器上按 Enter 放入`"
          @dragstart="onDragStart(item)"
          @keydown.enter.prevent="onSelectSource(item)"
          @keydown.space.prevent="onSelectSource(item)"
          @click="onSelectSource(item)"
        >
          {{ item }}
        </div>
      </div>
      <div class="drag-target">
        <div
          v-for="right in question.right_items || []"
          :key="right"
          class="drop-zone"
          role="button"
          :tabindex="locked ? -1 : 0"
          :aria-disabled="locked"
          :aria-label="dropZoneLabel(right)"
          @dragover.prevent
          @drop="onDrop(right)"
          @keydown.enter.prevent="onZoneActivate(right)"
          @keydown.space.prevent="onZoneActivate(right)"
          @click="onZoneActivate(right)"
        >
          <span class="zone-label">{{ right }}</span>
          <span class="zone-value">{{ dragMap[right] || '—' }}</span>
        </div>
      </div>
    </div>
  </template>
</template>

<script setup lang="ts">
/**
 * 题目作答区（按题型渲染交互控件）。
 *
 * 只负责渲染与事件上抛，不持有答案状态、不调用接口：
 * 练习页（Answer）与考试页（ExamTaking）的提交时机、判分回显差别很大，
 * 这里统一的是「六种题型的交互表面」，答案状态仍由各自页面持有。
 */
import { ref } from 'vue'
import type { QuestionAnswer } from '@/api/practice'

/** 判断题固定选项（DB 中 options 为 null）。 */
const JUDGE_OPTIONS = ['正确', '错误'] as const

/** 渲染所需的题目字段（练习题与考试题的交集）。 */
export interface QuestionLike {
  id: number
  type: string
  question: string
  options?: string[] | null
  left_items?: string[] | null
  right_items?: string[] | null
}

const props = withDefaults(
  defineProps<{
    question: QuestionLike
    /** 单选/判断答案 */
    picked: string
    /** 多选答案（字母数组） */
    multiPicked: string[]
    /** 填空题每空的答案 */
    blanks: string[]
    /** 简答题答案 */
    shortAns: string
    /** 拖拽题：右侧容器 → 左侧题项 */
    dragMap: Record<string, string>
    /** 拖拽题左侧题项（已打乱） */
    shuffledLeft: string[]
    /** 锁定交互（练习页提交后） */
    locked?: boolean
    /** 是否按正确答案着色（练习页提交后） */
    showResult?: boolean
    /** 提交后的正确答案（用于着色） */
    correctAnswer?: QuestionAnswer
    /** 简答题文本框行数 */
    shortRows?: number
  }>(),
  { locked: false, showResult: false, correctAnswer: null, shortRows: 4 },
)

const emit = defineEmits<{
  pick: [value: string]
  'toggle-multi': [letter: string]
  'update-blank': [index: number, value: string]
  'update:shortAns': [value: string]
  /** 文本输入失焦（考试页据此保存，避免逐字符请求） */
  blur: []
  'drag-start': [item: string]
  'pick-source': [item: string]
  drop: [right: string]
  unassign: [right: string]
}>()

const letter = (i: number) => String.fromCharCode(65 + i)

/** 键盘放置：先在左侧选中题项，再在目标容器上按 Enter 放入（不依赖拖放）。 */
const dragSource = ref('')

/** 已提交时把正确项标绿、错选项标红；未提交时只标出当前选择。 */
const decorate = (isPicked: boolean, isCorrect: boolean) => {
  if (!props.showResult) return { picked: isPicked }
  return { 'opt-correct': isCorrect, 'opt-wrong': isPicked && !isCorrect }
}

const choiceClass = (i: number) => {
  const l = letter(i)
  return decorate(props.picked === l, String(props.correctAnswer ?? '').includes(l))
}

const multiClass = (i: number) => {
  const l = letter(i)
  return decorate(props.multiPicked.includes(l), String(props.correctAnswer ?? '').includes(l))
}

const judgeClass = (opt: string) => {
  const correct = String(props.correctAnswer ?? '')
  return decorate(props.picked === opt, correct === opt)
}

const onPick = (value: string) => {
  if (props.locked) return
  emit('pick', value)
}

const onToggleMulti = (l: string) => {
  if (props.locked) return
  emit('toggle-multi', l)
}

// 拖拽题同样受 locked 约束：练习页判分后若仍可点击清空/重填，界面映射会与已判分的
// 快照不一致（切回该题时 applyAnswer 又按快照还原，编辑被静默丢弃）。
const onDragStart = (item: string) => {
  if (props.locked) return
  emit('drag-start', item)
}

const onSelectSource = (item: string) => {
  if (props.locked) return
  dragSource.value = dragSource.value === item ? '' : item
  emit('drag-start', item)
}

const dropZoneLabel = (right: string) => {
  if (dragSource.value) return `将「${dragSource.value}」放入「${right}」`
  return props.dragMap[right] ? `移除「${right}」上的选项` : `「${right}」为空`
}

const onZoneActivate = (right: string) => {
  if (props.locked) return
  if (dragSource.value) {
    emit('drop', right)
    dragSource.value = ''
    return
  }
  emit('unassign', right)
}

const onDrop = (right: string) => {
  if (props.locked) return
  emit('drop', right)
  dragSource.value = ''
}
</script>

<style scoped>
.option {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  margin-bottom: 10px;
  cursor: pointer;
  width: 100%;
  background: var(--el-bg-color);
  font: inherit;
  color: inherit;
  text-align: left;
  transition: all 0.15s;
}
.option:hover:not(:disabled) {
  border-color: var(--brand-primary);
}
.option:disabled {
  cursor: default;
}
.option.picked {
  border-color: var(--brand-primary);
  background: var(--brand-primary-light-9);
}
.option.opt-correct {
  border-color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}
.option.opt-wrong {
  border-color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}
.opt-letter {
  width: 28px;
  height: 28px;
  line-height: 28px;
  text-align: center;
  border-radius: 50%;
  background: var(--el-color-info-light-9);
  font-weight: 600;
  flex-shrink: 0;
}
.opt-text {
  word-break: break-word;
}
.blank-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.blank-label {
  width: 40px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}
.drag-area {
  display: flex;
  gap: 24px;
}
.drag-source {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}
.drag-item {
  padding: 10px 16px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  cursor: grab;
  background: var(--el-fill-color-lighter);
}
.drag-item:active {
  cursor: grabbing;
}
.drag-item.drag-picked {
  border-color: var(--brand-primary);
  background: var(--brand-primary-light-9);
}
.drag-target {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}
.drop-zone {
  display: flex;
  justify-content: space-between;
  padding: 10px 16px;
  border: 1px dashed var(--el-text-color-disabled);
  border-radius: 6px;
  min-height: 44px;
  align-items: center;
  cursor: pointer;
}
.zone-label {
  color: var(--el-text-color-regular);
}
.zone-value {
  font-weight: 600;
  color: var(--brand-primary);
}
@media (max-width: 767px) {
  .drag-area {
    flex-direction: column;
    gap: 12px;
  }
}
</style>
