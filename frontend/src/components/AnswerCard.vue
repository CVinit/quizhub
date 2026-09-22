<template>
  <div class="answer-card">
    <div class="card-title-row">
      <span class="card-title">{{ title }}</span>
      <!-- 手机端折叠答题卡 -->
      <button
        type="button"
        class="card-toggle"
        data-testid="answer-card-toggle"
        :aria-expanded="open"
        aria-label="展开或收起答题卡"
        @click="open = !open"
      >
        <el-icon><ArrowDown v-if="!open" /><ArrowUp v-else /></el-icon>
      </button>
    </div>

    <div class="grid" v-show="open">
      <button
        v-for="i in count"
        :key="i"
        type="button"
        class="cell"
        :data-testid="`answer-card-cell-${i - 1}`"
        :class="states[i - 1]"
        :aria-label="`第 ${i} 题${stateSuffix(i - 1)}`"
        :aria-current="i - 1 === activeIndex ? 'true' : undefined"
        @click="emit('jump', i - 1)"
      >
        {{ i }}
      </button>
    </div>

    <div class="legend" v-show="open && legend.length">
      <span v-for="l in legend" :key="l.text"><i class="dot" :class="l.className"></i>{{ l.text }}</span>
    </div>

    <div class="card-hint" v-show="open && hint">{{ hint }}</div>

    <!-- 底部操作（练习页无、考试页放交卷按钮） -->
    <slot />
  </div>
</template>

<script setup lang="ts">
/**
 * 答题卡（题号网格 + 图例 + 折叠）。
 *
 * 练习页与考试页的差异（状态配色、图例、底部按钮）通过 cellClass / legend / 插槽注入，
 * 网格与折叠交互只此一份，避免两处各写一遍后行为漂移。
 */
import { computed } from 'vue'
import { ArrowDown, ArrowUp } from '@element-plus/icons-vue'

const props = withDefaults(
  defineProps<{
    title: string
    /** 题号数量 */
    count: number
    /** 当前题号下标 */
    activeIndex: number
    /** 题号 → 额外状态类（current / cell-correct / cell-wrong / cell-pending / cell-answered） */
    cellClass: (index: number) => string[]
    legend?: { text: string; className: string }[]
    hint?: string
  }>(),
  { legend: () => [], hint: '' },
)

/** 折叠状态（手机端默认折叠由调用方决定初始值）。 */
const open = defineModel<boolean>('open', { required: true })

const emit = defineEmits<{ jump: [index: number] }>()

/**
 * 每个题号的状态类，预先算一次。
 *
 * 模板里原先对同一格调用两次 cellClass（一次给 :class、一次在 stateSuffix 内），
 * 而考试页把交卷按钮放在本组件的插槽里，父组件任何状态变化（包括简答/填空的每次输入）
 * 都会重渲染整张网格——100 题时每次输入就是 200 次回调 + 100 次按钮 patch。
 */
const states = computed(() => Array.from({ length: props.count }, (_, i) => props.cellClass(i)))

/** 状态类 → 无障碍文案后缀（状态仅靠背景色区分，读屏需要显式播报）。 */
const STATE_SUFFIX: Record<string, string> = {
  'cell-correct': '，正确',
  'cell-wrong': '，错误',
  'cell-pending': '，待自评',
  'cell-answered': '，已作答',
}

const stateSuffix = (index: number) => {
  const state = states.value[index]?.find((c) => c in STATE_SUFFIX)
  const suffix = state ? STATE_SUFFIX[state] : '，未作答'
  return index === props.activeIndex ? `${suffix}，当前题` : suffix
}
</script>

<style scoped>
/* 只负责卡片外观；宽度/吸顶/移动端排序由各页面通过外层容器决定 */
.answer-card {
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 16px;
  border: 1px solid var(--el-border-color-lighter);
}
.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-weight: 600;
  margin-bottom: 12px;
}
.card-toggle {
  display: none;
  cursor: pointer;
  font-size: 16px;
  color: var(--el-text-color-secondary);
  background: none;
  border: none;
  padding: 0;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(36px, 1fr));
  gap: 6px;
}
.cell {
  height: 36px;
  line-height: 36px;
  text-align: center;
  border-radius: 4px;
  background: var(--el-color-info-light-9);
  cursor: pointer;
  font-size: 13px;
  border: none;
  font-family: inherit;
  color: inherit;
  padding: 0;
}
.cell.current {
  border: 2px solid var(--brand-primary);
}
.cell.cell-answered {
  background: var(--brand-primary);
  color: var(--el-color-white);
}
.cell.cell-correct {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
  font-weight: 600;
}
.cell.cell-wrong {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-weight: 600;
}
.cell.cell-pending {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 14px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.legend .dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  background: var(--el-color-info-light-9);
  margin-right: 4px;
  vertical-align: -1px;
}
.legend .dot-answered {
  background: var(--brand-primary);
}
.legend .dot-current {
  background: var(--el-bg-color);
  border: 2px solid var(--brand-primary);
}
.legend .dot-correct {
  background: var(--el-color-success-light-3);
}
.legend .dot-wrong {
  background: var(--el-color-danger-light-3);
}
.legend .dot-pending {
  background: var(--el-color-warning-light-5);
}
.card-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-disabled);
}
@media (max-width: 767px) {
  .answer-card {
    padding: 12px;
  }
  .card-toggle {
    display: inline-flex;
  }
  .card-title {
    margin-bottom: 0;
  }
  .card-hint {
    display: none; /* 快捷键提示仅桌面端有意义 */
  }
}
</style>
