<template>
  <div class="answer-page" v-loading="loading">
    <div class="layout">
      <!-- 答题卡 -->
      <div class="side">
        <AnswerCard
          v-model:open="navOpen"
          :title="`${modeTitle} · ${currentIdx + 1}/${questions.length}`"
          :count="questions.length"
          :active-index="currentIdx"
          :cell-class="cellClass"
          :legend="navLegend"
          hint="键盘：A–F 选择 · Enter 提交/下一题 · ←→ 切题"
          @jump="jumpTo"
        />
      </div>

      <!-- 题目区 -->
      <div class="question-area" v-if="current">
        <div class="q-header">
          <el-tag>{{ current.type }}</el-tag>
          <el-tag type="info" size="small">难度 {{ current.difficulty }}</el-tag>
          <el-tag type="warning" size="small" v-if="current.score">{{ current.score }} 分</el-tag>
        </div>
        <div class="q-stem">{{ current.question }}</div>

        <QuestionBody
          :question="current"
          :picked="picked"
          :multi-picked="pickedMulti"
          :blanks="blanks"
          :short-ans="shortAns"
          :drag-map="dragMap"
          :shuffled-left="shuffledLeft"
          :locked="showResult"
          :show-result="showResult"
          :correct-answer="correctAnswer"
          :short-rows="4"
          @pick="onPick"
          @toggle-multi="toggleMulti"
          @update-blank="updateBlank"
          @update:short-ans="setShortAns"
          @drag-start="startDrag"
          @pick-source="onPickSource"
          @drop="dropOn"
          @unassign="unassign"
        />

        <!-- 提交后解析 -->
        <QuestionResult
          v-if="showResult"
          :question-type="current.type"
          :is-correct="resultCorrect"
          :correct-answer="correctAnswer"
          :analysis="current.analysis"
          :reference-answer="referenceAnswer"
          @mastered="onSelfEval"
        />

        <!-- 底部操作 -->
        <div class="actions">
          <el-button :disabled="currentIdx === 0" data-testid="practice-prev" @click="prev">上一题</el-button>
          <el-button :type="marked ? 'warning' : 'default'" data-testid="practice-mark" @click="toggleMark">
            {{ marked ? '取消标记' : '标记' }}
          </el-button>
          <!-- 六种题型在未提交时都有提交入口：文案与拖拽题的「填满才可交」在这里区分 -->
          <el-button
            v-if="showResult === false"
            type="primary"
            :loading="submitting"
            :disabled="current.type === '拖拽题' && !allDragFilled(current)"
            data-testid="practice-submit"
            @click="submit"
            >{{ current.type === '简答题' ? '提交查看参考答案' : '提交答案' }}</el-button
          >
          <el-button v-if="showResult || !needSubmit" type="primary" data-testid="practice-next" @click="next">
            {{ currentIdx === questions.length - 1 ? '完成' : '下一题' }}
          </el-button>
        </div>
      </div>

      <el-empty v-if="!loading && questions.length === 0" description="暂无题目" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { practiceApi, type Question, type QuestionAnswer } from '@/api/practice'
import AnswerCard from '@/components/AnswerCard.vue'
import QuestionBody from '@/components/QuestionBody.vue'
import QuestionResult from '@/components/QuestionResult.vue'
import { useAnswerKeyboard } from '@/composables/useAnswerKeyboard'
import { useAnswerDraft } from '@/composables/useAnswerDraft'
import { useResponsive } from '@/composables/useResponsive'
import { confirmBox } from '@/utils/dialog'

/** 已提交题目的快照：用于答题卡着色与回看解析。 */
interface PracticeHistory {
  answer: QuestionAnswer
  res: { is_correct: boolean | null; correct_answer: QuestionAnswer; reference_answer?: string }
}

const route = useRoute()
const router = useRouter()
const { isMobile } = useResponsive()
const loading = ref(false)
const questions = ref<Question[]>([])
const currentIdx = ref(0)
const current = computed(() => questions.value[currentIdx.value])
// 手机端默认折叠答题卡；桌面端折叠按钮不可见，故切回桌面时强制展开
const navOpen = ref(!isMobile.value)
watch(isMobile, (mobile) => {
  if (!mobile) navOpen.value = true
})

// 作答状态与载荷转换与考试页共用（见 useAnswerDraft），本页只负责逐题判分与回显
const {
  picked,
  multiPicked: pickedMulti,
  blanks,
  shortAns,
  dragMap,
  shuffledLeft,
  applyAnswer,
  buildPayload,
  hasAnswer,
  toggleMulti,
  updateBlank,
  setShortAns,
  pickSource,
  startDrag,
  dropOn,
  unassign,
  allDragFilled,
} = useAnswerDraft()

const showResult = ref(false)
const submitting = ref(false)
const resultCorrect = ref<boolean | null>(null)
const correctAnswer = ref<QuestionAnswer>(null)
const referenceAnswer = ref('')
const marked = ref(false)

// 已提交题目的历史：idx → { 提交的答案, 后端判定 }，用于答题卡着色与回看解析
const history = ref<Record<number, PracticeHistory>>({})

const mode = computed(() => String(route.params.mode || 'sequence'))
const bankId = computed(() => {
  const b = route.query.bank
  return b ? Number(b) : undefined
})
const modeTitle = computed(() => {
  const titles: Record<string, string> = {
    sequence: '顺序练习',
    random: '随机抽题',
    type: '按题型练习',
    wrong: '错题本',
    mark: '我的标记',
  }
  return titles[mode.value] || '练习'
})
const needSubmit = computed(() => ['单选题', '多选题', '判断题'].includes(current.value?.type))
/** 答过题后才展示状态图例（未作答时图例没有意义）。 */
const navLegend = computed(() =>
  answeredCount.value > 0
    ? [
        { text: '答对', className: 'dot-correct' },
        { text: '答错', className: 'dot-wrong' },
        { text: '待自评', className: 'dot-pending' },
      ]
    : [],
)

/** 请求序号：watch 复用同一组件实例，快速切换模式/题库时先发的慢响应可能后到并覆盖新题集。 */
let loadSeq = 0

const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    const type = route.query.type as string | undefined
    const bank = bankId.value
    // 仅有 bankId 且无指定题型时，子模式默认取当前 mode；按题型子模式需选题型
    const reqMode = mode.value
    const data = await practiceApi.start(reqMode, type, undefined, bank)
    if (seq !== loadSeq) return
    questions.value = data
    if (questions.value.length === 0) {
      ElMessage.info('暂无题目')
    } else {
      currentIdx.value = 0
      history.value = {}
      syncCurrent()
    }
  } catch {
    // 加载失败：http 拦截器已提示；保留当前题目，避免把失败误显示为“暂无题目”
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// 题库练习时切换子模式：仅替换 route.query 不触发整页刷新
// （已移除页内子模式切换条，模式由入口页选定；此处保留 watch route 以支持返回后重进）
// 切题后重置作答区；若该题此前已提交过，则恢复当时的答案与解析（回看）
const syncCurrent = () => {
  const saved = history.value[currentIdx.value]
  // 练习页左侧题项每次进入重新打乱（考试页保持卷面顺序）
  applyAnswer(current.value, saved?.answer, { randomizeLeft: true })
  showResult.value = false
  resultCorrect.value = null
  correctAnswer.value = null
  referenceAnswer.value = ''
  // 标记状态来自后端随题返回的 marked（本人维度），否则「取消标记」永远显示不出来
  marked.value = !!current.value?.marked

  if (!saved) return
  showResult.value = true
  resultCorrect.value = saved.res.is_correct
  correctAnswer.value = saved.res.correct_answer
  referenceAnswer.value = saved.res.reference_answer || ''
}

const answeredCount = computed(() => Object.keys(history.value).length)

/** 答题卡题号状态（由 AnswerCard 按题号下标回调）。 */
const cellClass = (i: number) => {
  const cls: string[] = []
  if (i === currentIdx.value) cls.push('current')
  const saved = history.value[i]
  if (saved) {
    if (saved.res.is_correct === true) cls.push('cell-correct')
    else if (saved.res.is_correct === false) cls.push('cell-wrong')
    else cls.push('cell-pending')
  }
  return cls
}

// 作答交互：QuestionBody 只上抛事件，状态与提交时机由本页决定
const onPick = (value: string) => {
  picked.value = value
}
const onPickSource = (item: string) => pickSource(current.value, item)

// 提交前校验：避免误触把空答案记成错题
const validateAnswer = () => {
  if (!current.value) return false
  const type = current.value.type
  if ((type === '单选题' || type === '判断题') && !picked.value) {
    ElMessage.warning('请先选择答案')
    return false
  }
  if (type === '多选题' && pickedMulti.value.length === 0) {
    ElMessage.warning('请先选择答案')
    return false
  }
  if (type === '填空题' && !blanks.value.some((b) => b.trim())) {
    ElMessage.warning('请先填写答案')
    return false
  }
  if (type === '简答题' && !shortAns.value.trim()) {
    ElMessage.warning('请先填写答案')
    return false
  }
  return true
}

const submit = async () => {
  if (!current.value || submitting.value || !validateAnswer()) return
  const q = current.value
  // 快照题号：await 期间用户可能按 ←→ / 点答题卡切题，若用 currentIdx.value 写历史，
  // 判分与解析会记到新题上（新题被标对错、原题变未作答）。
  const idx = currentIdx.value
  const answer = buildPayload(q)

  submitting.value = true
  try {
    const res = await practiceApi.answer(q.id, answer, mode.value)
    history.value[idx] = {
      answer,
      res: {
        is_correct: res.is_correct,
        correct_answer: res.correct_answer,
        reference_answer: res.reference_answer || '',
      },
    }
    // 已切走：只记录历史供答题卡着色，不把本题解析显示到别的题上
    if (idx !== currentIdx.value) return
    showResult.value = true
    resultCorrect.value = res.is_correct
    correctAnswer.value = res.correct_answer
    referenceAnswer.value = res.reference_answer || ''
  } catch {
    // http 拦截器已提示错误；保持未提交状态，允许用户重试
  } finally {
    submitting.value = false
  }
}

const onSelfEval = async (mastered: boolean) => {
  const q = current.value
  const idx = currentIdx.value
  if (!q) return
  try {
    await practiceApi.shortEval(q.id, mastered)
  } catch {
    // http 拦截器已提示；失败时不改动本地状态，允许重试
    return
  }
  const saved = history.value[idx]
  if (saved) saved.res.is_correct = mastered
  // await 期间用户可能已切题：只更新历史（供答题卡着色），不把本题的解析状态改到别的题上
  if (idx !== currentIdx.value) return
  ElMessage.success(mastered ? '已标记掌握' : '已加入错题本')
  showResult.value = false
}

const toggleMark = async () => {
  const q = current.value
  const idx = currentIdx.value
  if (!q) return
  const next = !marked.value
  marked.value = next
  try {
    await practiceApi.toggleMark(q.id, next)
    q.marked = next
    ElMessage.success(next ? '已标记' : '已取消标记')
  } catch {
    // 失败回滚，避免界面与服务端标记状态不一致；切题后不再回滚（marked 已属于新题）
    if (idx === currentIdx.value) marked.value = !next
  }
}

/** 未提交的填空/简答/拖拽只活在本地，切题会 reset 掉。有内容时先确认。 */
const confirmLeaveDraft = async (): Promise<boolean> => {
  if (showResult.value || !current.value) return true
  if (!hasAnswer(buildPayload(current.value))) return true
  return confirmBox('本题尚未提交，离开后已填写的内容会丢失。确定离开吗？', '离开本题', {
    type: 'warning',
    confirmButtonText: '离开',
    cancelButtonText: '继续作答',
  })
}

const prev = async () => {
  if (currentIdx.value <= 0) return
  if (!(await confirmLeaveDraft())) return
  currentIdx.value--
  syncCurrent()
}
const next = async () => {
  if (currentIdx.value < questions.value.length - 1) {
    if (!(await confirmLeaveDraft())) return
    currentIdx.value++
    syncCurrent()
    return
  }
  // 完成全部题目：给出本轮成绩小结
  const total = questions.value.length
  const done = answeredCount.value
  const correct = Object.values(history.value).filter((h) => h.res.is_correct === true).length
  const ok = await confirmBox(`共 ${total} 题，已答 ${done} 题，答对 ${correct} 题。`, '练习完成', {
    confirmButtonText: '返回模式选择',
    cancelButtonText: '留在本页',
    type: 'success',
  })
  if (ok) router.push('/answer')
}
const jumpTo = async (i: number) => {
  if (i === currentIdx.value) return
  if (!(await confirmLeaveDraft())) return
  currentIdx.value = i
  syncCurrent()
}

// 键盘快捷键：A–F 选择、Enter 提交/下一题、←→ 切题（焦点在输入框/按钮上时忽略）
useAnswerKeyboard({
  currentType: () => current.value?.type,
  optionCount: () => (current.value?.options || []).length,
  isLoading: () => loading.value,
  isShowingResult: () => showResult.value,
  needSubmit: () => !!needSubmit.value,
  pick: onPick,
  toggleMulti,
  submit,
  next,
  prev,
})

// 路由参数变化（模式 / 题库范围 / 题型变化）时重新加载题目
watch(
  () => [route.params.mode, route.query.bank, route.query.type],
  () => {
    load()
  },
)
onMounted(load)
</script>

<style scoped>
.answer-page {
  padding: 0;
}
.layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
/* 答题卡容器：宽度/吸顶/移动端排序属于页面布局，不放进展示组件 */
.side {
  width: 240px;
  position: sticky;
  top: 16px;
  flex-shrink: 0;
}
.question-area {
  flex: 1;
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 24px;
  min-height: 400px;
  min-width: 0;
}
.q-header {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.q-stem {
  font-size: 16px;
  line-height: 1.7;
  margin-bottom: 20px;
  white-space: pre-wrap;
  word-break: break-word;
}
.actions {
  margin-top: 24px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
/* 手机端：答题卡折叠后置底，题目优先（order 由容器层级控制） */
@media (max-width: 767px) {
  .layout {
    flex-direction: column;
  }
  .side {
    width: 100%;
    position: static;
    order: 1;
  }
  .question-area {
    padding: 16px;
    min-height: auto;
    order: -1;
  }
}
</style>
