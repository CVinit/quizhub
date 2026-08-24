<template>
  <div class="exam-taking" v-loading="loading">
    <template v-if="session">
      <!-- 顶部信息条 -->
      <div class="topbar">
        <div class="exam-title">{{ session.exam_name }}</div>
        <div class="countdown" :class="{ urgent: urgent }">
          <el-icon><Clock /></el-icon>
          <span>{{ countdown }}</span>
          <span class="hint" v-if="urgent">即将交卷！</span>
        </div>
      </div>

      <div class="layout">
        <!-- 答题卡 -->
        <div class="answer-card">
          <div class="card-title-row">
            <span class="card-title">答题卡</span>
            <el-icon class="card-toggle" @click="cardOpen = !cardOpen"><ArrowDown v-if="!cardOpen" /><ArrowUp v-else /></el-icon>
          </div>
          <div class="grid" v-show="cardOpen">
            <div
              v-for="q in session.questions" :key="q.id"
              class="cell" :class="cellClass(q)"
              @click="jumpTo(q.seq)">
              {{ q.seq + 1 }}
            </div>
          </div>
          <div class="legend" v-show="cardOpen">
            <span><i class="dot answered"></i>已答</span>
            <span><i class="dot"></i>未答</span>
            <span><i class="dot current"></i>当前</span>
          </div>
          <el-button type="danger" class="submit-btn" @click="confirmSubmit" :loading="submitting">
            交卷
          </el-button>
        </div>

        <!-- 题目区 -->
        <div class="question-area" v-if="current">
          <div class="q-header">
            <el-tag>{{ current.type }}</el-tag>
            <el-tag type="info" size="small">第 {{ current.seq + 1 }} 题</el-tag>
            <el-tag type="warning" size="small">{{ current.score }} 分</el-tag>
          </div>
          <div class="q-stem">{{ current.question }}</div>

          <!-- 单选 -->
          <template v-if="current.type === '单选题'">
            <div
              v-for="(opt, i) in current.options || []" :key="i"
              class="option" :class="{ picked: isPicked(letter(i)) }"
              @click="save(letter(i))">
              <span class="opt-letter">{{ letter(i) }}</span>
              <span class="opt-text">{{ opt }}</span>
            </div>
          </template>

          <!-- 判断 -->
          <template v-if="current.type === '判断题'">
            <div
              v-for="opt in judgeOptions" :key="opt"
              class="option" :class="{ picked: isPicked(opt) }"
              @click="save(opt)">
              <span class="opt-letter">{{ opt === '正确' ? '✓' : '✗' }}</span>
              <span class="opt-text">{{ opt }}</span>
            </div>
          </template>

          <!-- 多选 -->
          <template v-if="current.type === '多选题'">
            <div
              v-for="(opt, i) in current.options || []" :key="i"
              class="option" :class="{ picked: multiPicked.includes(letter(i)) }"
              @click="toggleMulti(letter(i))">
              <span class="opt-letter">{{ letter(i) }}</span>
              <span class="opt-text">{{ opt }}</span>
            </div>
          </template>

          <!-- 填空 -->
          <template v-if="current.type === '填空题'">
            <div v-for="(b, i) in blanks" :key="i" class="blank-row">
              <span class="blank-label">空{{ i + 1 }}</span>
              <el-input v-model="blanks[i]" @blur="save(blanks.filter(x => x).length ? blanks : null)" placeholder="请输入" />
            </div>
          </template>

          <!-- 简答 -->
          <template v-if="current.type === '简答题'">
            <el-input v-model="shortAns" type="textarea" :rows="6" @blur="save(shortAns)" placeholder="请输入答案" />
          </template>

          <!-- 拖拽 -->
          <template v-if="current.type === '拖拽题'">
            <div class="drag-area">
              <div class="drag-source">
                <div v-for="item in shuffledLeft" :key="item"
                  class="drag-item" draggable="true"
                  @dragstart="dragItem = item" @click="pickSource(item)">{{ item }}</div>
              </div>
              <div class="drag-target">
                <div v-for="right in (current.right_items || [])" :key="right" class="drop-zone"
                  @dragover.prevent @drop="onDrop(right)" @click="unassign(right)">
                  <span class="zone-label">{{ right }}</span>
                  <span class="zone-value">{{ dragMap[right] || '—' }}</span>
                </div>
              </div>
            </div>
          </template>

          <div class="actions">
            <el-button :disabled="current.seq === 0" @click="prev">上一题</el-button>
            <el-button type="primary" @click="next">下一题</el-button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Clock, ArrowDown, ArrowUp } from '@element-plus/icons-vue'
import { examApi, type ExamSession } from '@/api/exam'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const session = ref<ExamSession | null>(null)
const version = ref(1)
const currentSeq = ref(0)
const submitting = ref(false)
const countdown = ref('')
const urgent = ref(false)
const cardOpen = ref(false) // 手机端答题卡默认折叠

const picked = ref('') // 当前单选/判断
const multiPicked = ref<string[]>([])
const blanks = ref<string[]>([])
const shortAns = ref('')
const dragMap = reactive<Record<string, string>>({})
const shuffledLeft = ref<string[]>([])
const dragItem = ref('')
const judgeOptions = ['正确', '错误'] // 判断题固定选项（DB 中 options 为 null）

let timer: any = null
let remaining = 0

const current = computed(() => {
  const qs = session.value?.questions || []
  return qs.find((q) => q.seq === currentSeq.value) || qs[0]
})

const letter = (i: number) => String.fromCharCode(65 + i)
const isPicked = (l: string) => picked.value === l

const cellClass = (q: any) => {
  const cls: string[] = []
  if (q.seq === currentSeq.value) cls.push('current')
  const ans = session.value?.answers?.[String(q.id)]
  if (ans && ans.answer !== null && ans.answer !== '' && ans.answer !== undefined) cls.push('answered')
  return cls
}

const syncFromAnswers = () => {
  if (!current.value) return
  const a = session.value?.answers?.[String(current.value.id)]?.answer
  picked.value = ''
  multiPicked.value = []
  blanks.value = []
  shortAns.value = ''
  Object.keys(dragMap).forEach((k) => delete dragMap[k])
  if (a == null) {
    // 空
  } else if (typeof a === 'string') {
    picked.value = a
  } else if (Array.isArray(a)) {
    blanks.value = [...a]
  } else if (typeof a === 'object') {
    // 拖拽 left->right
    Object.entries(a).forEach(([left, right]) => {
      dragMap[String(right)] = left
    })
  }
  // 多选答案也用 string 存（已排序），还原需拆字符
  if (current.value.type === '多选题' && typeof a === 'string') {
    multiPicked.value = a.split('')
    picked.value = ''
  }
  if (current.value.type === '填空题' && blanks.value.length === 0) {
    blanks.value = (current.value.question.match(/_{2,}/g) || ['']).map(() => '')
  }
  shuffledLeft.value = [...(current.value.left_items || [])]
}

const jumpTo = (seq: number) => {
  currentSeq.value = seq
  syncFromAnswers()
}

const prev = () => { if (currentSeq.value > 0) { currentSeq.value--; syncFromAnswers() } }
const next = () => {
  const total = session.value?.questions.length || 0
  if (currentSeq.value < total - 1) { currentSeq.value++; syncFromAnswers() }
}

const save = async (answer: any) => {
  if (!current.value || !session.value) return
  // 多选：从 multiPicked 计算
  let payload = answer
  if (current.value.type === '多选题') {
    payload = multiPicked.value.slice().sort().join('')
  } else if (current.value.type === '拖拽题') {
    const mapping: Record<string, string> = {}
    Object.entries(dragMap).forEach(([right, left]) => { mapping[left] = right })
    payload = mapping
  } else if (current.value.type === '填空题') {
    payload = blanks.value
  } else if (current.value.type === '简答题') {
    payload = shortAns.value
  }
  try {
    const res = await examApi.answer(session.value.session_id, current.value.id, payload, version.value)
    version.value = res.version
    // 同步到本地 answers 用于答题卡着色
    const ans = { ...session.value.answers }
    ans[String(current.value.id)] = { answer: payload }
    session.value.answers = ans
  } catch (err: any) {
    if (err.response?.status === 409) {
      ElMessage.error('数据版本冲突，正在刷新')
      await reload()
    }
  }
}

const toggleMulti = (l: string) => {
  multiPicked.value = multiPicked.value.includes(l)
    ? multiPicked.value.filter((x) => x !== l)
    : [...multiPicked.value, l]
  save(null)
}

// 拖拽
const pickSource = (item: string) => {
  const rights = current.value?.right_items || []
  const empty = rights.find((r: string) => !dragMap[r])
  if (empty) { dragMap[empty] = item; save(null) }
}
const onDrop = (right: string) => {
  if (dragItem.value) {
    Object.keys(dragMap).forEach((k) => { if (dragMap[k] === dragItem.value) delete dragMap[k] })
    dragMap[right] = dragItem.value
    dragItem.value = ''
    save(null)
  }
}
const unassign = (right: string) => { delete dragMap[right]; save(null) }

const reload = async () => {
  const sid = Number(route.params.id)
  // 重新拉会话状态
  const s = await examApi.start(Number(route.query.examId) || 0).catch(() => null)
  // 退回考试列表更稳妥
  router.push('/exam')
}

const startTimer = () => {
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    if (remaining <= 0) {
      clearInterval(timer)
      ElMessage.warning('考试时间已到，自动交卷')
      doSubmit(true)
      return
    }
    remaining -= 1
    const h = Math.floor(remaining / 3600)
    const m = Math.floor((remaining % 3600) / 60)
    const s = remaining % 60
    countdown.value = h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
    urgent.value = remaining <= 300
  }, 1000)
}

const confirmSubmit = async () => {
  const total = session.value?.questions.length || 0
  const answered = Object.keys(session.value?.answers || {}).length
  await ElMessageBox.confirm(`共 ${total} 题，已作答 ${answered} 题。确认交卷？`, '交卷确认', { type: 'warning' })
  await doSubmit(false)
}

const doSubmit = async (auto: boolean) => {
  if (submitting.value) return
  submitting.value = true
  if (timer) clearInterval(timer)
  try {
    const sid = session.value!.session_id
    const res = await examApi.submit(sid)
    if (res.need_review) {
      await ElMessageBox.alert('已交卷。本次考试含简答题，成绩待管理员复核后公布。', '提交成功', { type: 'info' })
    } else if (res.score !== undefined) {
      const passText = res.passed ? '🎉 恭喜通过！' : '很遗憾未通过'
      await ElMessageBox.alert(
        `${passText}\n得分：${res.score} / ${res.total_score}\n答对：${res.correct_count} / ${res.total_count}`,
        '考试结果', { type: res.passed ? 'success' : 'warning' },
      )
    } else {
      await ElMessageBox.alert('已交卷。', '提交成功')
    }
    router.push('/exam')
  } catch (err) {
    if (timer) startTimer()
    submitting.value = false
  } finally {
    submitting.value = false
  }
}

const load = async () => {
  loading.value = true
  const sid = Number(route.params.id)
  try {
    const examId = Number(route.query.examId) || 0
    let s: ExamSession
    if (examId) {
      // 正式考试：start 幂等返回进行中会话
      s = await examApi.start(examId)
    } else {
      // 模拟考试或刷新场景：用会话详情恢复
      s = await examApi.sessionDetail(sid)
    }
    if ((s as any).finished) {
      await ElMessageBox.alert('该考试已交卷。', '提示', { type: 'info' })
      router.push('/exam')
      return
    }
    session.value = s
    version.value = s.version
    remaining = s.remaining_sec ?? s.duration_min * 60 ?? 0
    currentSeq.value = 0
    syncFromAnswers()
    startTimer()
  } finally {
    loading.value = false
  }
}

onMounted(load)
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.exam-taking { max-width: 1100px; }
.topbar { display: flex; justify-content: space-between; align-items: center; background: #fff; border-radius: 8px; padding: 12px 20px; margin-bottom: 16px; border: 1px solid #ebeef5; gap: 12px; }
.exam-title { font-weight: 600; font-size: 16px; }
.countdown { display: flex; align-items: center; gap: 6px; font-size: 20px; font-weight: 700; color: var(--brand-primary); flex-shrink: 0; }
.countdown.urgent { color: #f56c6c; animation: blink 1s infinite; }
.countdown .hint { font-size: 12px; margin-left: 4px; }
@keyframes blink { 50% { opacity: .6; } }
.layout { display: flex; gap: 16px; align-items: flex-start; }
.answer-card { width: 240px; background: #fff; border-radius: 8px; padding: 16px; position: sticky; top: 16px; border: 1px solid #ebeef5; flex-shrink: 0; }
.card-title-row { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-weight: 600; margin-bottom: 12px; }
.card-toggle { display: none; cursor: pointer; font-size: 16px; color: #909399; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(36px, 1fr)); gap: 6px; }
.cell { height: 36px; line-height: 36px; text-align: center; border-radius: 4px; background: #f4f4f5; cursor: pointer; font-size: 13px; }
.cell.answered { background: var(--brand-primary); color: #fff; }
.cell.current { border: 2px solid var(--brand-primary); }
.legend { display: flex; flex-direction: column; gap: 4px; margin: 14px 0; font-size: 12px; color: #909399; }
.legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; background: #f4f4f5; margin-right: 4px; }
.legend .dot.answered { background: var(--brand-primary); }
.legend .dot.current { background: #fff; border: 2px solid var(--brand-primary); }
.submit-btn { width: 100%; }
.question-area { flex: 1; background: #fff; border-radius: 8px; padding: 24px; min-height: 400px; min-width: 0; }
.q-header { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.q-stem { font-size: 16px; line-height: 1.7; margin-bottom: 20px; white-space: pre-wrap; word-break: break-word; }
.option { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border: 1px solid #ebeef5; border-radius: 6px; margin-bottom: 10px; cursor: pointer; }
.option:hover { border-color: var(--brand-primary); }
.option.picked { border-color: var(--brand-primary); background: var(--brand-primary-light-9); }
.opt-letter { width: 28px; height: 28px; line-height: 28px; text-align: center; border-radius: 50%; background: #f4f4f5; font-weight: 600; flex-shrink: 0; }
.blank-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.blank-label { width: 40px; color: #909399; flex-shrink: 0; }
.drag-area { display: flex; gap: 24px; }
.drag-source { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.drag-item { padding: 10px 16px; border: 1px solid #dcdfe6; border-radius: 6px; cursor: grab; background: #fafafa; }
.drag-target { display: flex; flex-direction: column; gap: 8px; flex: 1; }
.drop-zone { display: flex; justify-content: space-between; padding: 10px 16px; border: 1px dashed #c0c4cc; border-radius: 6px; min-height: 44px; align-items: center; cursor: pointer; }
.zone-label { color: #606266; }
.zone-value { font-weight: 600; color: var(--brand-primary); }
.actions { margin-top: 24px; display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
/* 手机端：答题卡折叠置顶，题目区下方堆叠 */
@media (max-width: 767px) {
  .layout { flex-direction: column; }
  .answer-card { width: 100%; position: static; padding: 12px; }
  .card-toggle { display: inline-flex; }
  .card-title { margin-bottom: 0; }
  .question-area { padding: 16px; min-height: auto; }
  .topbar { flex-direction: column; align-items: flex-start; padding: 10px 14px; }
  .countdown { font-size: 18px; }
  .drag-area { flex-direction: column; gap: 12px; }
  .actions { justify-content: stretch; }
  .actions .el-button { flex: 1; }
}
</style>
