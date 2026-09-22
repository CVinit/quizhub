<template>
  <div class="answer-modes">
    <div class="modes-header">
      <h2>选择练习模式</h2>
      <!-- 题库范围选择器：默认全部题库（即总题库），可选某个题库 -->
      <div class="range-picker">
        <span class="range-label">题库范围</span>
        <el-select v-model="selectedBank" placeholder="全部题库" style="width: 280px" @change="onRangeChange">
          <el-option :value="0" :label="`全部题库（${allTotal} 题）`" />
          <el-option v-for="b in banks" :key="b.id" :value="b.id" :label="`${b.name}（${b.count} 题）`" />
        </el-select>
      </div>
    </div>

    <div class="modes" v-loading="loading">
      <button type="button" class="mode-card" v-for="m in modeList" :key="m.key" @click="onPick(m.key)">
        <el-icon class="mode-icon"><component :is="m.icon" /></el-icon>
        <div class="mode-title">{{ m.title }}</div>
        <div class="mode-desc">{{ m.desc }}</div>
        <div class="mode-count" v-if="m.count !== undefined">{{ m.count }} 题</div>
      </button>
    </div>

    <!-- 按题型：选择题型（范围内题型分布）-->
    <el-dialog v-model="typeDialog" title="选择题型" width="320px">
      <div class="type-list">
        <el-button v-for="t in types" :key="t" @click="startType(t)"> {{ t }}（{{ typeDist[t] || 0 }} 题） </el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { practiceApi, type BankBrief } from '@/api/practice'
import { QUESTION_TYPES } from '@/constants/question'
import { Document, Refresh, Files, Warning, Collection } from '@/utils/icons'

const router = useRouter()
const loading = ref(false)
const typeDialog = ref(false)
const types = QUESTION_TYPES
const typeDist = ref<Record<string, number>>({})
const banks = ref<BankBrief[]>([])
// selectedBank: 0 = 全部题库（总题库）；>0 = 选定某题库
// （el-option 的 value 不接受 null，故用 0 作哨兵值，与后端 bank_id 自增主键不冲突）
const selectedBank = ref(0)

const modeList = ref([
  {
    key: 'sequence',
    title: '顺序练习',
    desc: '按题目顺序逐题作答',
    icon: Document,
    count: undefined as number | undefined,
  },
  { key: 'type', title: '按题型练习', desc: '选择题型专项练习', icon: Files, count: undefined as number | undefined },
  { key: 'random', title: '随机抽题', desc: '随机打乱顺序练习', icon: Refresh, count: undefined as number | undefined },
  { key: 'wrong', title: '错题本', desc: '重做做错的题目', icon: Warning, count: undefined as number | undefined },
  { key: 'mark', title: '我的标记', desc: '查看标记的题目', icon: Collection, count: undefined as number | undefined },
])

// 全部题库的总题量（范围选择器"全部题库(N 题)"标签用，不随单个题库范围变化）
const allTotal = ref(0)

// 请求序号：快速切换题库范围时，先发出的慢响应可能后到达，会把上一个范围的统计
// 覆盖到当前范围上（题型题量、模式计数全错）。只接受最新一次请求的结果。
let loadSeq = 0

const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    // 传 selectedBank 范围过滤（0 表示不限）；接口返回的 banks 始终全量
    const d = await practiceApi.modes(selectedBank.value || undefined)
    if (seq !== loadSeq) return
    typeDist.value = d.type_dist
    banks.value = d.banks || []
    if (!selectedBank.value) allTotal.value = d.total
    // 按 key 映射而不是按下标：modeList 顺序调整时下标写法会静默把计数贴到别的模式上
    const counts: Record<string, number> = {
      sequence: d.total,
      type: d.total,
      random: d.total,
      wrong: d.wrong,
      mark: d.marked,
    }
    for (const m of modeList.value) m.count = counts[m.key]
  } catch {
    // 加载失败：http 拦截器已提示；保留上一次的统计与题库列表
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

// 范围切换：重新拉取范围内统计
const onRangeChange = () => {
  load()
}

const onPick = (key: string) => {
  if (key === 'type') {
    typeDialog.value = true
    return
  }
  // 范围为全部题库时不带 bank；选定某题库时带 bank query
  const query: Record<string, string> = {}
  if (selectedBank.value) query.bank = String(selectedBank.value)
  router.push({ name: 'answer', params: { mode: key }, query })
}

const startType = (t: string) => {
  typeDialog.value = false
  const query: Record<string, string> = { type: t }
  if (selectedBank.value) query.bank = String(selectedBank.value)
  router.push({ name: 'answer', params: { mode: 'type' }, query })
}

onMounted(load)
</script>

<style scoped>
.answer-modes {
  max-width: 900px;
}
.modes-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.answer-modes h2 {
  margin: 0;
}
.range-picker {
  display: flex;
  align-items: center;
  gap: 10px;
}
.range-label {
  color: var(--el-text-color-regular);
  font-size: 14px;
}
.modes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}
.mode-card {
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 24px;
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid var(--el-border-color-lighter);
  text-align: center;
  font: inherit;
  color: inherit;
}
.mode-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}
.mode-icon {
  font-size: 32px;
  color: var(--brand-primary);
  margin-bottom: 12px;
}
.mode-title {
  font-weight: 600;
  font-size: 16px;
  margin-bottom: 4px;
}
.mode-desc {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.mode-count {
  color: var(--brand-primary);
  font-size: 13px;
  margin-top: 8px;
}
.type-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.type-list .el-button {
  margin-left: 0;
}
@media (max-width: 767px) {
  .modes-header {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .range-picker .el-select {
    width: 100% !important;
  }
  .modes {
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  .mode-card {
    padding: 16px 10px;
  }
  .mode-icon {
    font-size: 26px;
    margin-bottom: 8px;
  }
  .mode-title {
    font-size: 14px;
  }
  .mode-desc {
    font-size: 12px;
  }
}
</style>
