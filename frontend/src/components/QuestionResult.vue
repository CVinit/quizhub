<template>
  <div class="analysis">
    <el-alert :type="resultType" :closable="false" show-icon>
      <template #title>{{ resultText }}</template>
      <div v-if="questionType !== '简答题'" class="correct-ans">正确答案：{{ displayCorrectAnswer }}</div>
    </el-alert>
    <div class="analysis-text" v-if="analysis"><strong>解析：</strong>{{ analysis }}</div>
    <!-- 简答无客观判定，交卷后由用户自评是否掌握 -->
    <div v-if="questionType === '简答题'" class="self-eval">
      <div class="ref-ans"><strong>参考答案：</strong>{{ referenceAnswer || '（无）' }}</div>
      <el-button type="success" @click="emit('mastered', true)">掌握</el-button>
      <el-button type="warning" @click="emit('mastered', false)">需复习</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 提交后的判分回显：结果提示 + 正确答案 + 解析（+ 简答自评）。 */
import { computed } from 'vue'
import type { QuestionAnswer } from '@/api/practice'

const props = defineProps<{
  questionType: string
  /** null = 无客观判定（简答） */
  isCorrect: boolean | null
  correctAnswer: QuestionAnswer
  analysis: string
  referenceAnswer: string
}>()

const emit = defineEmits<{ mastered: [value: boolean] }>()

const resultType = computed(() => (props.isCorrect === true ? 'success' : props.isCorrect === false ? 'error' : 'info'))
const resultText = computed(() =>
  props.isCorrect === true ? '回答正确' : props.isCorrect === false ? '回答错误' : '已提交',
)

/** 正确答案的多态展示：填空为二维数组、拖拽为映射、其余为字符串。 */
const displayCorrectAnswer = computed(() => {
  const a = props.correctAnswer
  if (Array.isArray(a)) return a.map((b) => (Array.isArray(b) ? b.join(' / ') : b)).join(' ｜ ')
  if (a && typeof a === 'object')
    return Object.entries(a)
      .map(([k, v]) => `${k}→${v}`)
      .join('，')
  return a ?? ''
})
</script>

<style scoped>
.analysis {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.correct-ans {
  margin-top: 8px;
}
.analysis-text {
  margin-top: 12px;
  padding: 12px;
  background: var(--el-color-info-light-9);
  border-radius: 6px;
  line-height: 1.6;
}
.self-eval {
  margin-top: 12px;
}
.ref-ans {
  margin-bottom: 12px;
  padding: 12px;
  background: var(--el-color-warning-light-9);
  border-radius: 6px;
}
</style>
