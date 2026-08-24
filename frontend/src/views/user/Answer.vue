<template>
  <div class="answer-page" v-loading="loading">
    <div class="layout">
      <!-- 导航网格 -->
      <div class="nav-grid">
        <div class="grid-title-row">
          <span class="grid-title">{{ modeTitle }} · {{ currentIdx + 1 }}/{{ questions.length }}</span>
          <!-- 手机端折叠答题卡 -->
          <el-icon class="grid-toggle" @click="navOpen = !navOpen"><ArrowDown v-if="!navOpen" /><ArrowUp v-else /></el-icon>
        </div>
        <div class="grid-box" v-show="navOpen">
          <div
            v-for="(q, i) in questions" :key="q.id"
            class="grid-cell" :class="cellClass(q, i)"
            @click="jumpTo(i)">
            {{ i + 1 }}
          </div>
        </div>
      </div>

      <!-- 题目区 -->
      <div class="question-area" v-if="current">
        <div class="q-header">
          <el-tag>{{ current.type }}</el-tag>
          <el-tag type="info" size="small">难度 {{ current.difficulty }}</el-tag>
          <el-tag type="warning" size="small" v-if="current.score">{{ current.score }} 分</el-tag>
        </div>
        <div class="q-stem">{{ current.question }}</div>

        <!-- 单选 -->
        <template v-if="current.type === '单选题'">
          <div
            v-for="(opt, i) in current.options || []" :key="i"
            class="option" :class="optionClass(i)" @click="pickChoice(i)">
            <span class="opt-letter">{{ String.fromCharCode(65 + i) }}</span>
            <span class="opt-text">{{ opt }}</span>
          </div>
        </template>

        <!-- 判断 -->
        <template v-if="current.type === '判断题'">
          <div
            v-for="opt in judgeOptions" :key="opt"
            class="option" :class="optionClassJudge(opt)" @click="pickJudge(opt)">
            <span class="opt-letter">{{ opt === '正确' ? '✓' : '✗' }}</span>
            <span class="opt-text">{{ opt }}</span>
          </div>
        </template>

        <!-- 多选 -->
        <template v-if="current.type === '多选题'">
          <div
            v-for="(opt, i) in current.options || []" :key="i"
            class="option" :class="optionClassMulti(i)" @click="pickMulti(i)">
            <span class="opt-letter">{{ String.fromCharCode(65 + i) }}</span>
            <span class="opt-text">{{ opt }}</span>
          </div>
        </template>

        <!-- 填空 -->
        <template v-if="current.type === '填空题'">
          <div v-for="(blank, i) in blanks" :key="i" class="blank-row">
            <span class="blank-label">空{{ i + 1 }}</span>
            <el-input v-model="blanks[i]" placeholder="请输入答案" :disabled="showResult" />
          </div>
        </template>

        <!-- 简答 -->
        <template v-if="current.type === '简答题'">
          <el-input v-model="shortAns" type="textarea" :rows="4" placeholder="请输入答案" :disabled="showResult" />
        </template>

        <!-- 拖拽 -->
        <template v-if="current.type === '拖拽题'">
          <div class="drag-area">
            <div class="drag-source">
              <div
                v-for="item in shuffledLeft" :key="item"
                class="drag-item" draggable="true"
                @dragstart="onDragStart(item)"
                @click="onPickSource(item)">{{ item }}</div>
            </div>
            <div class="drag-target">
              <div v-for="right in (current.right_items || [])" :key="right" class="drop-zone"
                @dragover.prevent @drop="onDrop(right)" @click="onUnassign(right)">
                <span class="zone-label">{{ right }}</span>
                <span class="zone-value">{{ dragMap[right] || '—' }}</span>
              </div>
            </div>
          </div>
        </template>

        <!-- 提交后解析 -->
        <div class="analysis" v-if="showResult">
          <el-alert :type="resultType" :closable="false" show-icon>
            <template #title>{{ resultText }}</template>
            <div v-if="current.type !== '简答题'" class="correct-ans">
              正确答案：{{ displayCorrectAnswer }}
            </div>
          </el-alert>
          <div class="analysis-text" v-if="current.analysis">
            <strong>解析：</strong>{{ current.analysis }}
          </div>
          <!-- 简答自评 -->
          <div v-if="current.type === '简答题' && showResult" class="self-eval">
            <div class="ref-ans"><strong>参考答案：</strong>{{ referenceAnswer }}</div>
            <el-button type="success" @click="onSelfEval(true)">掌握</el-button>
            <el-button type="warning" @click="onSelfEval(false)">需复习</el-button>
          </div>
        </div>

        <!-- 底部操作 -->
        <div class="actions">
          <el-button :disabled="currentIdx === 0" @click="prev">上一题</el-button>
          <el-button :type="marked ? 'warning' : 'default'" @click="toggleMark">
            {{ marked ? '取消标记' : '标记' }}
          </el-button>
          <el-button
            v-if="!showResult && !['简答题'].includes(current.type) && needSubmit"
            type="primary" @click="submit">提交答案</el-button>
          <el-button
            v-if="!showResult && current.type === '填空题'"
            type="primary" @click="submit">提交答案</el-button>
          <el-button
            v-if="!showResult && current.type === '简答题'"
            type="primary" @click="submit">提交查看参考答案</el-button>
          <el-button
            v-if="!showResult && current.type === '拖拽题'"
            type="primary" @click="submitDrag" :disabled="!allDragFilled">提交答案</el-button>
          <el-button v-if="showResult || !needSubmit" type="primary" @click="next">
            {{ currentIdx === questions.length - 1 ? '完成' : '下一题' }}
          </el-button>
        </div>
      </div>

      <el-empty v-if="!loading && questions.length === 0" description="暂无题目" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import { practiceApi, type Question } from '@/api/practice'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const questions = ref<Question[]>([])
const currentIdx = ref(0)
const current = computed(() => questions.value[currentIdx.value])
const navOpen = ref(true) // 手机端答题卡折叠状态

const picked = ref<string>('') // 单选/判断答案
const pickedMulti = ref<string[]>([]) // 多选答案
const blanks = ref<string[]>([]) // 填空
const shortAns = ref('')
const dragMap = reactive<Record<string, string>>({}) // right->left
const shuffledLeft = ref<string[]>([])
const draggingItem = ref('')
const judgeOptions = ['正确', '错误'] // 判断题固定选项（DB 中 options 为 null）

const showResult = ref(false)
const resultCorrect = ref<boolean | null>(null)
const correctAnswer = ref<any>(null)
const referenceAnswer = ref('')
const marked = ref(false)

const mode = computed(() => String(route.params.mode || 'sequence'))
const bankId = computed(() => {
  const b = route.query.bank
  return b ? Number(b) : undefined
})
const types = ['单选题', '多选题', '判断题', '填空题', '简答题', '拖拽题']
const modeTitle = computed(() => {
  const titles: Record<string, string> = { sequence: '顺序练习', random: '随机抽题', type: '按题型练习', wrong: '错题本', mark: '我的标记' }
  return titles[mode.value] || '练习'
})
const needSubmit = computed(() => ['单选题', '多选题', '判断题'].includes(current.value?.type))
const resultType = computed(() => resultCorrect.value === true ? 'success' : resultCorrect.value === false ? 'error' : 'info')
const resultText = computed(() => resultCorrect.value === true ? '回答正确' : resultCorrect.value === false ? '回答错误' : '已提交')
const displayCorrectAnswer = computed(() => {
  const a = correctAnswer.value
  if (Array.isArray(a)) return a.map((b: any) => Array.isArray(b) ? b.join(' / ') : b).join(' ｜ ')
  if (a && typeof a === 'object') return Object.entries(a).map(([k, v]) => `${k}→${v}`).join('，')
  return a
})
const allDragFilled = computed(() => {
  const rights = current.value?.right_items || []
  return rights.every((r: string) => dragMap[r])
})

const load = async () => {
  loading.value = true
  try {
    const type = route.query.type as string | undefined
    const bank = bankId.value
    // 仅有 bankId 且无指定题型时，子模式默认取当前 mode；按题型子模式需选题型
    const reqMode = mode.value
    questions.value = await practiceApi.start(reqMode, type, undefined, bank)
    if (questions.value.length === 0) {
      ElMessage.info('暂无题目')
    } else {
      currentIdx.value = 0
      resetAnswer()
    }
  } finally {
    loading.value = false
  }
}

// 题库练习时切换子模式：仅替换 route.query 不触发整页刷新
// （已移除页内子模式切换条，模式由入口页选定；此处保留 watch route 以支持返回后重进）
const resetAnswer = () => {
  picked.value = ''
  pickedMulti.value = []
  blanks.value = (current.value?.question.match(/_{2,}/g) || []).map(() => '')
  if (current.value?.type === '填空题' && blanks.value.length === 0) blanks.value = ['']
  shortAns.value = ''
  Object.keys(dragMap).forEach((k) => delete dragMap[k])
  shuffledLeft.value = [...(current.value?.left_items || [])].sort(() => Math.random() - 0.5)
  showResult.value = false
  resultCorrect.value = null
  marked.value = false
}

const cellClass = (q: Question, i: number) => {
  const cls: string[] = []
  if (i === currentIdx.value) cls.push('current')
  if (q.type === '简答题') return cls
  // 练习模式：从后端状态推断（简化：提交后着色由本地 showResult 控制）
  return cls
}

const optionClass = (i: number) => {
  if (!showResult.value) return { picked: picked.value === String.fromCharCode(65 + i) }
  const letter = String.fromCharCode(65 + i)
  const correct = String(correctAnswer.value)
  const isCorrect = correct.includes(letter)
  const isPicked = picked.value === letter
  return { 'opt-correct': isCorrect, 'opt-wrong': isPicked && !isCorrect }
}

const optionClassMulti = (i: number) => {
  const letter = String.fromCharCode(65 + i)
  const inPicked = pickedMulti.value.includes(letter)
  if (!showResult.value) return { picked: inPicked }
  const correct = String(correctAnswer.value || '')
  const isCorrect = correct.includes(letter)
  return { 'opt-correct': isCorrect, 'opt-wrong': inPicked && !isCorrect }
}

const optionClassJudge = (opt: string) => {
  if (!showResult.value) return { picked: picked.value === opt }
  const correct = String(correctAnswer.value)
  const isCorrect = correct === opt
  const isPicked = picked.value === opt
  return { 'opt-correct': isCorrect, 'opt-wrong': isPicked && !isCorrect }
}

const pickChoice = (i: number) => {
  if (showResult.value) return
  picked.value = String.fromCharCode(65 + i)
}
const pickJudge = (opt: string) => {
  if (showResult.value) return
  picked.value = opt
}
const pickMulti = (i: number) => {
  if (showResult.value) return
  const letter = String.fromCharCode(65 + i)
  pickedMulti.value = pickedMulti.value.includes(letter)
    ? pickedMulti.value.filter((l) => l !== letter)
    : [...pickedMulti.value, letter]
}

// 拖拽
const onDragStart = (item: string) => { draggingItem.value = item }
const onPickSource = (item: string) => {
  // 点击：放到第一个未填的区
  const rights = current.value?.right_items || []
  const empty = rights.find((r: string) => !dragMap[r])
  if (empty) dragMap[empty] = item
}
const onDrop = (right: string) => {
  if (draggingItem.value) {
    // 移除该 item 在其他区的占用
    Object.keys(dragMap).forEach((k) => { if (dragMap[k] === draggingItem.value) delete dragMap[k] })
    dragMap[right] = draggingItem.value
    draggingItem.value = ''
  }
}
const onUnassign = (right: string) => { delete dragMap[right] }

const submit = async () => {
  if (!current.value) return
  let answer: any = null
  if (['单选题', '判断题'].includes(current.value.type)) answer = picked.value
  else if (current.value.type === '多选题') answer = pickedMulti.value.sort().join('')
  else if (current.value.type === '填空题') answer = blanks.value
  else if (current.value.type === '简答题') answer = shortAns.value

  const res = await practiceApi.answer(current.value.id, answer, mode.value)
  showResult.value = true
  resultCorrect.value = res.is_correct
  correctAnswer.value = res.correct_answer
  referenceAnswer.value = res.reference_answer || ''
}

const submitDrag = async () => {
  // right->left 转成 left->right
  const mapping: Record<string, string> = {}
  Object.entries(dragMap).forEach(([right, left]) => { mapping[left] = right })
  const res = await practiceApi.answer(current.value!.id, mapping, mode.value)
  showResult.value = true
  resultCorrect.value = res.is_correct
  correctAnswer.value = res.correct_answer
}

const onSelfEval = async (mastered: boolean) => {
  await practiceApi.shortEval(current.value!.id, mastered)
  ElMessage.success(mastered ? '已标记掌握' : '已加入错题本')
  showResult.value = false
}

const toggleMark = async () => {
  marked.value = !marked.value
  await practiceApi.toggleMark(current.value!.id, marked.value)
  ElMessage.success(marked.value ? '已标记' : '已取消标记')
}

const prev = () => { if (currentIdx.value > 0) { currentIdx.value--; resetAnswer() } }
const next = () => {
  if (currentIdx.value < questions.value.length - 1) {
    currentIdx.value++
    resetAnswer()
  } else {
    ElMessage.success('已完成全部题目')
    router.push('/answer')
  }
}
const jumpTo = (i: number) => { currentIdx.value = i; resetAnswer() }

watch(currentIdx, () => { /* reactivity via current computed */ })
// 路由参数变化（模式 / 题库范围 / 题型变化）时重新加载题目
watch(() => [route.params.mode, route.query.bank, route.query.type], () => { load() })
onMounted(load)
</script>

<style scoped>
.answer-page { padding: 0; }
.layout { display: flex; gap: 16px; align-items: flex-start; }
.nav-grid { width: 240px; background: #fff; border-radius: 8px; padding: 16px; position: sticky; top: 16px; flex-shrink: 0; }
.grid-title-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.grid-title { font-weight: 600; }
.grid-toggle { display: none; cursor: pointer; font-size: 16px; color: #909399; }
.bank-switch { margin-bottom: 14px; }
.grid-box { display: grid; grid-template-columns: repeat(auto-fill, minmax(36px, 1fr)); gap: 6px; }
.grid-cell { height: 36px; line-height: 36px; text-align: center; border-radius: 4px; background: #f4f4f5; cursor: pointer; font-size: 13px; }
.grid-cell.current { border: 2px solid var(--brand-primary); }
.question-area { flex: 1; background: #fff; border-radius: 8px; padding: 24px; min-height: 400px; min-width: 0; }
.q-header { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.q-stem { font-size: 16px; line-height: 1.7; margin-bottom: 20px; white-space: pre-wrap; word-break: break-word; }
.option { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border: 1px solid #ebeef5; border-radius: 6px; margin-bottom: 10px; cursor: pointer; transition: all .15s; }
.option:hover { border-color: var(--brand-primary); }
.option.picked { border-color: var(--brand-primary); background: var(--brand-primary-light-9); }
.option.opt-correct { border-color: #67c23a; background: #f0f9eb; }
.option.opt-wrong { border-color: #f56c6c; background: #fef0f0; }
.opt-letter { width: 28px; height: 28px; line-height: 28px; text-align: center; border-radius: 50%; background: #f4f4f5; font-weight: 600; flex-shrink: 0; }
.opt-text { word-break: break-word; }
.blank-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.blank-label { width: 40px; color: #909399; flex-shrink: 0; }
.drag-area { display: flex; gap: 24px; }
.drag-source { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.drag-item { padding: 10px 16px; border: 1px solid #dcdfe6; border-radius: 6px; cursor: grab; background: #fafafa; }
.drag-item:active { cursor: grabbing; }
.drag-target { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.drop-zone { display: flex; justify-content: space-between; padding: 10px 16px; border: 1px dashed #c0c4cc; border-radius: 6px; min-height: 44px; align-items: center; cursor: pointer; }
.zone-label { color: #606266; }
.zone-value { font-weight: 600; color: var(--brand-primary); }
.analysis { margin-top: 20px; padding-top: 16px; border-top: 1px solid #ebeef5; }
.correct-ans { margin-top: 8px; }
.analysis-text { margin-top: 12px; padding: 12px; background: #f4f4f5; border-radius: 6px; line-height: 1.6; }
.self-eval { margin-top: 12px; }
.ref-ans { margin-bottom: 12px; padding: 12px; background: #fdf6ec; border-radius: 6px; }
.actions { margin-top: 24px; display: flex; gap: 8px; flex-wrap: wrap; }
/* 手机端：导航网格变全宽可折叠，题目区堆叠在下方 */
@media (max-width: 767px) {
  .layout { flex-direction: column; }
  .nav-grid { width: 100%; position: static; }
  .grid-toggle { display: inline-flex; }
  .question-area { padding: 16px; min-height: auto; order: -1; }
  /* 手机端默认折叠答题卡，题目优先 */
  .nav-grid { order: 1; }
  .drag-area { flex-direction: column; gap: 12px; }
}
</style>
