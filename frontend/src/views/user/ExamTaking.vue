<template>
  <div class="exam-taking" v-loading="loading">
    <template v-if="session">
      <!-- 顶部信息条 -->
      <div class="topbar">
        <div class="exam-title">{{ session.exam_name }}</div>
        <div class="countdown" :class="{ urgent: urgent }" role="timer" :aria-label="`剩余时间 ${countdown}`">
          <el-icon><Clock /></el-icon>
          <span aria-hidden="true">{{ countdown }}</span>
          <span class="hint" v-if="urgent" aria-live="polite">即将交卷！</span>
        </div>
      </div>

      <div class="layout">
        <!-- 答题卡 -->
        <div class="side">
          <AnswerCard
            v-model:open="cardOpen"
            title="答题卡"
            :count="session.questions.length"
            :active-index="currentIndex"
            :cell-class="cellClass"
            :legend="cardLegend"
            @jump="onJump"
          >
            <el-button
              type="danger"
              class="submit-btn"
              data-testid="exam-submit"
              @click="confirmSubmit"
              :loading="submitting"
            >
              交卷
            </el-button>
          </AnswerCard>
        </div>

        <!-- 题目区 -->
        <div class="question-area" v-if="current">
          <div class="q-header">
            <el-tag>{{ current.type }}</el-tag>
            <el-tag type="info" size="small">第 {{ current.seq + 1 }} 题</el-tag>
            <el-tag type="warning" size="small">{{ current.score }} 分</el-tag>
          </div>
          <div class="q-stem">{{ current.question }}</div>

          <QuestionBody
            :question="current"
            :picked="picked"
            :multi-picked="multiPicked"
            :blanks="blanks"
            :short-ans="shortAns"
            :drag-map="dragMap"
            :shuffled-left="shuffledLeft"
            :short-rows="6"
            @pick="onPick"
            @toggle-multi="onToggleMulti"
            @update-blank="updateBlank"
            @update:short-ans="setShortAns"
            @blur="saveOnBlur"
            @drag-start="startDrag"
            @pick-source="onPickSource"
            @drop="onDropTarget"
            @unassign="onUnassign"
          />

          <div class="actions">
            <el-button :disabled="current.seq === 0" data-testid="exam-prev" @click="prev">上一题</el-button>
            <el-button
              type="primary"
              data-testid="exam-next"
              :disabled="currentIndex >= session.questions.length - 1"
              @click="next"
            >
              下一题
            </el-button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Clock } from '@element-plus/icons-vue'
import { examApi, type ExamSession, type ExamSessionDetail } from '@/api/exam'
import type { QuestionAnswer } from '@/api/practice'
import { httpStatusOf } from '@/api/http'
import AnswerCard from '@/components/AnswerCard.vue'
import QuestionBody from '@/components/QuestionBody.vue'
import { useAnswerDraft } from '@/composables/useAnswerDraft'
import { useCountdown } from '@/composables/useCountdown'
import { useResponsive } from '@/composables/useResponsive'
import { alertBox, confirmBox } from '@/utils/dialog'

const route = useRoute()
const router = useRouter()
const { isMobile } = useResponsive()
const loading = ref(true)
const session = ref<ExamSession | null>(null)
const version = ref(1)
const currentSeq = ref(0)
const submitting = ref(false)
const cardOpen = ref(!isMobile.value) // 手机端答题卡默认折叠，桌面端展开
const submitted = ref(false)
// 桌面端折叠按钮不可见，切回桌面时强制展开，避免卡片被永久折叠
watch(isMobile, (mobile) => {
  if (!mobile) cardOpen.value = true
})

// 作答状态与载荷转换与练习页共用（见 useAnswerDraft），本页只负责保存时机与交卷
const {
  picked,
  multiPicked,
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
} = useAnswerDraft()

/** 截止时间驱动的倒计时：后台标签页被节流也不会多给时间。 */
const {
  seconds: remaining,
  start: startTimer,
  stop: stopTimer,
  setOnVisible,
} = useCountdown(() => {
  ElMessage.warning('考试时间已到，自动交卷')
  void doSubmit()
})

const countdown = computed(() => {
  const s = remaining.value
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(sec).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`
})
const urgent = computed(() => remaining.value <= 300)

const current = computed(() => {
  const qs = session.value?.questions || []
  return qs.find((q) => q.seq === currentSeq.value) || qs[0]
})

/** 当前题在卷面中的下标（答题卡按题号下标工作）。 */
const currentIndex = computed(() => {
  const qs = session.value?.questions || []
  const i = qs.findIndex((q) => q.seq === currentSeq.value)
  return i >= 0 ? i : 0
})

const onJump = (index: number) => {
  const q = session.value?.questions[index]
  if (q) jumpTo(q.seq)
}

/** 答题卡图例：考试页只区分已答/未答/当前（不显示对错，避免泄题）。 */
const cardLegend = [
  { text: '已答', className: 'dot-answered' },
  { text: '未答', className: 'dot' },
  { text: '当前', className: 'dot-current' },
]

/** 答题卡题号状态（由 AnswerCard 按题号下标回调）。 */
const cellClass = (index: number) => {
  const cls: string[] = []
  if (index === currentIndex.value) cls.push('current')
  const q = session.value?.questions[index]
  if (q && hasAnswer(session.value?.answers?.[String(q.id)]?.answer)) cls.push('cell-answered')
  return cls
}

/** 用后端答案恢复当前题的交互状态；考试页左侧题项保持卷面顺序，避免两次进入顺序不同。 */
const syncFromAnswers = () => {
  if (!current.value) return
  // 当前题有未确认落库的作答（队列中的最新一次，或此前保存失败的）时不要用服务端副本
  // 覆盖交互状态：reload() 的整表替换会丢掉用户正在输入的内容（简答/填空半截文本）。
  const pending = unsaved.get(current.value.id)
  if (pending !== undefined) {
    applyAnswer(current.value, pending, { randomizeLeft: false })
    return
  }
  applyAnswer(current.value, session.value?.answers?.[String(current.value.id)]?.answer, { randomizeLeft: false })
}

const jumpTo = (seq: number) => {
  currentSeq.value = seq
  syncFromAnswers()
}

const prev = () => {
  if (currentSeq.value > 0) {
    currentSeq.value--
    syncFromAnswers()
  }
}
const next = () => {
  const total = session.value?.questions.length || 0
  if (currentSeq.value < total - 1) {
    currentSeq.value++
    syncFromAnswers()
  }
}

/**
 * 作答请求必须串行：服务端以 `version` 做乐观锁（UPDATE ... WHERE version=?），
 * 并发提交（连点多选、拖拽连放、连续失焦）会让后到的请求 409，而 409 的恢复路径
 * reload() 用服务端副本整体覆盖本地，用户最新的一次选择会被静默丢掉。
 * 串行后每个请求都能拿到上一个请求返回的 version，自冲突消失。
 */
let saveQueue: Promise<void> = Promise.resolve()

/**
 * 未确认落库的答案（题目 id → 载荷）。
 *
 * 非 409 的保存失败（网络中断/5xx/422）必须把答案留在这里：答题卡着色与交卷统计都基于本地
 * `session.answers`，直接丢弃会让用户看到「已作答」而服务端没有这条答案，交卷后才发现丢题。
 * 交卷前会补发一轮，仍失败则明确询问用户是否继续。
 */
const unsaved = new Map<number, QuestionAnswer>()
const unsavedCount = ref(0)

const syncUnsavedCount = () => {
  unsavedCount.value = unsaved.size
}

/** 入队一次保存（队列自身永不 reject，保证后续保存不会被一次异常卡死）。 */
const enqueue = (sid: number, qid: number, payload: QuestionAnswer) => {
  saveQueue = saveQueue.then(() => sendAnswer(sid, qid, payload)).catch(() => {})
  return saveQueue
}

const sendAnswer = async (sid: number, qid: number, payload: QuestionAnswer) => {
  try {
    const res = await examApi.answer(sid, qid, payload, version.value, { quiet: true })
    version.value = res.version
    // 仅当期间没有产生更新的作答时才移出待保存集合（引用比较）
    if (unsaved.get(qid) === payload) {
      unsaved.delete(qid)
      syncUnsavedCount()
    }
    // 把已落库的载荷合并回本地副本：reload() 会用服务端副本整体替换 session，
    // 若本地副本里缺了这些答案，答题卡会涂成「未答」、交卷统计也会少算已答题。
    if (session.value) {
      session.value.answers = { ...session.value.answers, [String(qid)]: { answer: payload } }
    }
  } catch (err) {
    if (httpStatusOf(err) === 409) {
      // 串行后仍冲突 = 其它端/会话已推进版本，只能重新拉取
      ElMessage.error('数据版本冲突，正在刷新')
      await reload()
      return
    }
    // 其它失败：保留在 unsaved 中由交卷前补发，并提示用户，避免误以为已保存
    ElMessage.error('答案暂未保存，将在交卷前自动重试')
  }
}

/** 保存当前题：载荷由 useAnswerDraft 按题型归一化，本页只负责入队与本地状态。 */
const save = () => {
  if (!current.value || !session.value) return
  const q = current.value
  const sid = session.value.session_id
  const payload = buildPayload(q)

  // 本地先落状态（答题卡着色、回看答案），请求排队异步发出
  const ans = { ...session.value.answers }
  ans[String(q.id)] = { answer: payload }
  session.value.answers = ans

  unsaved.set(q.id, payload)
  syncUnsavedCount()
  enqueue(sid, q.id, payload)
}

// 交互事件：先改本地状态，再触发一次保存
const onPick = (value: string) => {
  picked.value = value
  save()
}
const onToggleMulti = (letter: string) => {
  toggleMulti(letter)
  save()
}
const onPickSource = (item: string) => {
  pickSource(current.value, item)
  save()
}
const onDropTarget = (right: string) => {
  dropOn(right)
  save()
}
const onUnassign = (right: string) => {
  unassign(right)
  save()
}

// 文本题只在失焦时保存：逐字符保存会把每次输入变成一次带乐观锁的写请求
const saveOnBlur = () => save()

/** 服务端已结束：放行离开守卫并停表，避免「已交卷」之后又被「尚未交卷」拦住停在空白页。 */
const leaveFinished = async (message: string) => {
  submitted.value = true
  stopTimer()
  ElMessage.warning(message)
  await router.replace('/exam')
}

const reload = async () => {
  // 只按会话 id 拉详情。再调 start 会在已交卷后另开一场（max_attempts 未用尽时）。
  try {
    const s: ExamSessionDetail = await examApi.sessionDetail(Number(route.params.id))
    if (s.finished) {
      await leaveFinished('该考试已交卷')
      return
    }
    session.value = s
    version.value = s.version
    syncFromAnswers()
  } catch {
    submitted.value = true
    stopTimer()
    await router.replace('/exam')
  }
}

const confirmSubmit = async () => {
  if (submitting.value || submitted.value) return
  const total = session.value?.questions.length || 0
  const answers = Object.values(session.value?.answers || {})
  const answered = answers.filter((x) => hasAnswer(x?.answer)).length
  const ok = await confirmBox(`共 ${total} 题，已作答 ${answered} 题。确认交卷？`, '交卷确认')
  // 确认框打开期间倒计时可能已经自动交卷，不能再打一次 /submit
  if (!ok || submitted.value) return
  await doSubmit()
}

/** 交卷前把「只在失焦保存」的文本题补交一次，并等排队中的保存全部落库，避免丢答案。 */
const flushCurrentAnswer = async () => {
  const type = current.value?.type
  if (type === '填空题' || type === '简答题') save()
  await saveQueue
  // 之前失败过的答案在这里补发一轮（仍失败会重新留在 unsaved 中）
  const sid = session.value?.session_id
  if (sid == null || unsaved.size === 0) return
  for (const [qid, payload] of [...unsaved]) {
    await enqueue(sid, qid, payload)
  }
}

const doSubmit = async () => {
  if (submitting.value || submitted.value) return
  // 自动交卷时关掉仍开着的「确认交卷」，避免确认回调在 submitting 复位后再交一次
  ElMessageBox.close()
  submitting.value = true
  // stopTimer() 会把剩余秒数清零（见 useCountdown.stop），先记录下来，
  // 交卷失败或用户选择返回检查时才能原样恢复倒计时。
  const leftBeforeStop = remaining.value
  stopTimer()
  try {
    await flushCurrentAnswer()
    // 补发后仍未保存成功的答案：直接交卷等于永久丢这些题，必须让用户知情并决定
    if (unsavedCount.value > 0) {
      const goOn = await confirmBox(
        `有 ${unsavedCount.value} 题的答案仍未保存成功（可能是网络问题）。继续交卷将丢失这些作答，确认交卷？`,
        '答案未保存',
        { type: 'warning', confirmButtonText: '仍然交卷', cancelButtonText: '返回检查' },
      )
      if (!goOn) {
        if (leftBeforeStop > 0) startTimer(leftBeforeStop)
        return
      }
    }
    const sid = session.value!.session_id
    const res = await examApi.submit(sid)
    submitted.value = true // 交卷成功后再跳转，避免被离开守卫拦截
    if (res.need_review) {
      await alertBox('已交卷。本次考试含简答题，成绩待管理员复核后公布。', '提交成功', { type: 'info' })
    } else if (res.score !== undefined) {
      const passText = res.passed ? '🎉 恭喜通过！' : '很遗憾未通过'
      await alertBox(
        `${passText}\n得分：${res.score} / ${res.total_score}\n答对：${res.correct_count} / ${res.total_count}`,
        '考试结果',
        { type: res.passed ? 'success' : 'warning' },
      )
    } else {
      await alertBox('已交卷。', '提交成功')
    }
  } catch {
    // 交卷失败（网络/服务端）：恢复倒计时并允许重试；已成功交卷时不再恢复。
    // 用 stop 前记录的剩余秒数：remaining 已清零，且为 0 时重启会变成每秒重试一次的自动交卷循环。
    if (!submitted.value && leftBeforeStop > 0) startTimer(leftBeforeStop)
  } finally {
    submitting.value = false
  }
  if (submitted.value) router.replace('/exam')
}

const load = async () => {
  loading.value = true
  const sid = Number(route.params.id)
  try {
    // start 只发生在列表页点击「开始」。这里再调 start，后退/刷新会在已交卷后另开一场。
    const s: ExamSessionDetail = await examApi.sessionDetail(sid)
    if (s.finished) {
      submitted.value = true
      stopTimer()
      await alertBox('该考试已交卷。', '提示', { type: 'info' })
      await router.replace('/exam')
      return
    }
    session.value = s
    version.value = s.version
    currentSeq.value = 0
    syncFromAnswers()
    startTimer(s.remaining_sec ?? s.duration_min * 60)
  } catch {
    // 加载失败不留空白页，退回考试中心（拦截器已提示原因）；同时放行离开守卫
    submitted.value = true
    stopTimer()
    await router.replace('/exam')
  } finally {
    loading.value = false
  }
}

/** 回到前台时用服务端剩余时间校准，避免只信本地时钟（改系统时间会提前或拖后交卷）。 */
const syncRemainingFromServer = async () => {
  if (submitted.value || !session.value) return
  try {
    const s = await examApi.sessionDetail(session.value.session_id)
    if (s.finished) {
      await leaveFinished('该考试已交卷')
      return
    }
    // 只在服务端明确给出剩余时间时重启。start(0) 会立刻触发自动交卷，不能用来表示「未知」。
    if (typeof s.remaining_sec === 'number' && s.remaining_sec >= 0) startTimer(s.remaining_sec)
  } catch {
    /* 校准失败不打断作答，本地 deadline 继续走 */
  }
}

onMounted(() => {
  setOnVisible(() => {
    if (document.visibilityState !== 'visible') return
    void syncRemainingFromServer()
  })
  void load()
})

// 考试中防误触：路由离开需确认（交卷成功后放行）。
// 离开前必须刷完保存队列：未落库的答案只活在内存里，组件销毁后就没了。
onBeforeRouteLeave(async () => {
  if (submitted.value) return true
  await flushCurrentAnswer()
  if (unsavedCount.value > 0) {
    return confirmBox(
      `还有 ${unsavedCount.value} 题未能保存，离开后这些作答会丢失。确定离开吗？`,
      '离开考试',
      { type: 'warning', confirmButtonText: '仍然离开', cancelButtonText: '继续作答' },
    )
  }
  return confirmBox(
    '考试尚未交卷。已保存的作答可稍后继续（计时不停）。确定离开吗？',
    '离开考试',
    { type: 'warning', confirmButtonText: '离开', cancelButtonText: '继续作答' },
  )
})

// 浏览器关闭/刷新提示
const onBeforeUnload = (e: BeforeUnloadEvent) => {
  if (submitted.value) return
  e.preventDefault()
  e.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', onBeforeUnload))
</script>

<style scoped>
.exam-taking {
  max-width: 1100px;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 12px 20px;
  margin-bottom: 16px;
  border: 1px solid var(--el-border-color-lighter);
  gap: 12px;
}
.exam-title {
  font-weight: 600;
  font-size: 16px;
}
.countdown {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 20px;
  font-weight: 700;
  color: var(--brand-primary);
  flex-shrink: 0;
}
.countdown.urgent {
  color: var(--el-color-danger);
  animation: blink 1s infinite;
}
.countdown .hint {
  font-size: 12px;
  margin-left: 4px;
}
@keyframes blink {
  50% {
    opacity: 0.6;
  }
}
/* 前庭敏感用户：闪烁动效降级为静态高亮 */
@media (prefers-reduced-motion: reduce) {
  .countdown.urgent {
    animation: none;
  }
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
.submit-btn {
  width: 100%;
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
  justify-content: flex-end;
  flex-wrap: wrap;
}
/* 手机端：答题卡折叠后置底，题目优先 */
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
  .topbar {
    flex-direction: column;
    align-items: flex-start;
    padding: 10px 14px;
  }
  .countdown {
    font-size: 18px;
  }
  .actions {
    justify-content: stretch;
  }
  .actions .el-button {
    flex: 1;
  }
}
</style>
