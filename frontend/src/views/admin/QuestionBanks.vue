<template>
  <div class="banks" v-loading="loading">
    <div class="toolbar">
      <span class="title">题库管理</span>
      <div class="filters">
        <!-- 状态筛选：默认全部（管理员需同时看到已关闭练习的题库以便管理） -->
        <el-select v-model="practiceFilter" placeholder="状态" clearable style="width: 150px" @change="load">
          <el-option label="开放练习" value="enabled" />
          <el-option label="仅考试使用" value="disabled" />
        </el-select>
        <el-button type="primary" @click="onAdd">新建题库</el-button>
        <el-button @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
      关闭练习后，用户练习入口不再出现该题库，且「全部题库」范围也会排除它； 已有的练习记录与错题本不受影响。
    </el-alert>

    <el-table :data="rows" border v-loading="loading" class="mobile-table-hidden">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="name" label="题库名称" min-width="200" />
      <el-table-column label="题量" width="90">
        <template #default="{ row }">{{ row.question_count ?? 0 }}</template>
      </el-table-column>
      <el-table-column label="所属分组" width="140">
        <template #default="{ row }">{{ groupName(row.group_id) }}</template>
      </el-table-column>
      <el-table-column label="允许用户练习" width="140">
        <template #default="{ row }">
          <el-switch
            :model-value="!!row.practice_enabled"
            :loading="togglingId === row.id"
            :aria-label="`允许「${row.name}」练习`"
            @change="(v: string | number | boolean) => onTogglePractice(row as QuestionBank, Boolean(v))"
          />
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="row.practice_enabled ? 'success' : 'info'" size="small">
            {{ row.practice_enabled ? '开放练习' : '仅考试使用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="onRename(row as QuestionBank)">重命名</el-button>
          <el-button size="small" @click="$router.push(`/admin/questions?bank=${row.id}`)">查看题目</el-button>
          <el-button size="small" type="danger" @click="onDelete(row as QuestionBank)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：卡片列表 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title">
          {{ row.name }}
          <el-tag size="small" :type="row.practice_enabled ? 'success' : 'info'">{{
            row.practice_enabled ? '开放练习' : '仅考试使用'
          }}</el-tag>
        </div>
        <div class="mc-row"><span class="mc-label">题量</span>{{ row.question_count ?? 0 }}</div>
        <div class="mc-row"><span class="mc-label">分组</span>{{ groupName(row.group_id) }}</div>
        <div class="mc-row">
          <span class="mc-label">允许练习</span>
          <el-switch
            :model-value="!!row.practice_enabled"
            :loading="togglingId === row.id"
            :aria-label="`允许「${row.name}」练习`"
            @change="(v: string | number | boolean) => onTogglePractice(row, Boolean(v))"
          />
        </div>
        <div class="mc-actions">
          <el-button size="small" @click="onRename(row)">重命名</el-button>
          <el-button size="small" @click="$router.push(`/admin/questions?bank=${row.id}`)">查看题目</el-button>
          <el-button size="small" type="danger" @click="onDelete(row)">删除</el-button>
        </div>
      </div>
    </div>
    <el-empty v-if="isMobile && !loading && rows.length === 0" description="暂无题库" :image-size="80" />
    <el-empty v-if="!isMobile && !loading && rows.length === 0" description="暂无题库" :image-size="80" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { questionApi, type QuestionBank } from '@/api/question'
import { groupApi, type GroupNode } from '@/api/group'
import { useResponsive } from '@/composables/useResponsive'
import { confirmBox, promptBox } from '@/utils/dialog'

const { isMobile } = useResponsive()

const loading = ref(false)
const rows = ref<QuestionBank[]>([])
const groupTree = ref<GroupNode[]>([])
const togglingId = ref<number | null>(null)
// '' = 全部状态；enabled/disabled 由后端按 practice_enabled 过滤
const practiceFilter = ref<'' | 'enabled' | 'disabled'>('')

const flatten = (nodes: GroupNode[], out: GroupNode[] = []): GroupNode[] => {
  for (const n of nodes) {
    out.push(n)
    if (n.children?.length) flatten(n.children, out)
  }
  return out
}

/** 分组 id → 名称映射：避免每行渲染都重新展开整棵分组树。 */
const groupNameMap = computed(() => {
  const map = new Map<number, string>()
  for (const g of flatten(groupTree.value)) map.set(g.id, g.name)
  return map
})

const groupName = (id: number | null | undefined) => {
  if (id == null) return '—'
  return groupNameMap.value.get(id) || `#${id}`
}

/** 列表加载序号：切换状态筛选会并发多个请求，只接受最新一次的结果。 */
let loadSeq = 0

const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    // 状态筛选下推到后端，避免前端本地过滤与分页/统计不一致
    const filter = practiceFilter.value === '' ? undefined : practiceFilter.value === 'enabled'
    const [banks, tree] = await Promise.all([questionApi.listBanks(filter), groupApi.tree()])
    if (seq !== loadSeq) return
    rows.value = banks
    groupTree.value = tree
  } catch {
    // 加载失败：http 拦截器已提示；保留当前列表，避免把失败误显示为“暂无题库”
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

const onTogglePractice = async (row: QuestionBank, val: boolean) => {
  togglingId.value = row.id
  try {
    await questionApi.updateBank(row.id, { practice_enabled: val })
    row.practice_enabled = val
    ElMessage.success(val ? `已开放「${row.name}」练习` : `已停止「${row.name}」练习`)
  } catch {
    // http 拦截器已提示
  } finally {
    togglingId.value = null
  }
}

const onRename = async (row: QuestionBank) => {
  const value = await promptBox('请输入新的题库名称', '重命名题库', {
    inputValue: row.name,
    inputPattern: /\S+/,
    inputErrorMessage: '名称不能为空',
    confirmButtonText: '保存',
    cancelButtonText: '取消',
  })
  if (value === null) return
  try {
    await questionApi.updateBank(row.id, { name: value.trim() })
    ElMessage.success('已保存')
    await load()
  } catch {
    // http 拦截器已提示；不吞成未处理的 rejection
  }
}

const onAdd = async () => {
  const value = await promptBox('请输入题库名称', '新建题库', {
    inputPattern: /\S+/,
    inputErrorMessage: '名称不能为空',
    confirmButtonText: '创建',
    cancelButtonText: '取消',
  })
  if (value === null) return
  try {
    await questionApi.createBank(value.trim())
    ElMessage.success('已创建')
    await load()
  } catch {
    // http 拦截器已提示
  }
}

const onDelete = async (row: QuestionBank) => {
  const ok = await confirmBox(
    `确认删除题库「${row.name}」？其下 ${row.question_count ?? 0} 道题将一并删除，不可恢复。` +
      `若题目已被考试引用则无法删除，可改为关闭练习。`,
    '删除题库',
    { confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  if (!ok) return
  try {
    await questionApi.deleteBank(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    // 409（被考试引用）等错误由 http 拦截器提示
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.title {
  font-size: 18px;
  font-weight: 600;
}
.filters {
  display: flex;
  gap: 8px;
}
</style>
