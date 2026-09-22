<template>
  <div>
    <div class="toolbar">
      <span class="title">分组管理</span>
      <el-button type="primary" @click="onAdd(null)">新增顶级分组</el-button>
    </div>

    <el-table
      :data="tree"
      row-key="id"
      border
      default-expand-all
      :tree-props="{ children: 'children' }"
      class="mobile-table-hidden"
    >
      <el-table-column prop="id" label="ID" width="80" />
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column prop="type" label="类型" width="120">
        <template #default="{ row }">
          <el-tag :type="typeTag(row.type)">{{ row.type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="sort" label="排序" width="80" />
      <el-table-column label="操作" width="240">
        <template #default="{ row }">
          <el-button size="small" @click="onAdd(row as GroupNode)">新增子分组</el-button>
          <el-button size="small" type="primary" @click="onEdit(row as GroupNode)">编辑</el-button>
          <el-button size="small" type="danger" @click="onDelete(row as GroupNode)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：分组卡片（带层级缩进，整棵树展开） -->
    <div class="mobile-card-list" v-if="isMobile">
      <div class="mc" v-for="item in flatTree" :key="item.node.id" :style="{ marginLeft: item.level * 16 + 'px' }">
        <div class="mc-title">
          <el-tag :type="typeTag(item.node.type)" size="small">{{ item.node.type }}</el-tag> {{ item.node.name }}
          <span class="mc-id">#{{ item.node.id }}</span>
        </div>
        <div class="mc-actions">
          <el-button size="small" @click="onAdd(item.node)">新增子分组</el-button>
          <el-button size="small" type="primary" @click="onEdit(item.node)">编辑</el-button>
          <el-button size="small" type="danger" @click="onDelete(item.node)">删除</el-button>
        </div>
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="420px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="分组名称" />
        </el-form-item>
        <el-form-item label="类型" prop="type">
          <el-select v-model="form.type" style="width: 100%">
            <el-option v-for="t in types" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort" :min="0" />
        </el-form-item>
        <el-form-item v-if="form.parent" label="父分组">
          <el-input :model-value="form.parent" disabled />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance } from 'element-plus'
import { groupApi, type GroupNode } from '@/api/group'
import { useResponsive } from '@/composables/useResponsive'
import { confirmBox } from '@/utils/dialog'
import { validateForm } from '@/utils/form'
import type { TagType } from '@/constants/ui'

const { isMobile } = useResponsive()

const tree = ref<GroupNode[]>([])
const dialogVisible = ref(false)
const saving = ref(false)
const formRef = ref<FormInstance>()
const types = ['部门', '专业', '班级', '自定义']
const editingId = ref<number | null>(null)
const parentId = ref<number | null>(null)

const form = reactive({ name: '', type: '部门', sort: 0, parent: '' })
const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择类型', trigger: 'change' }],
}

const dialogTitle = ref('新增分组')

/** 手机端用：把分组树展开为带层级的扁平列表（桌面表格由 el-table 自行渲染树形）。 */
interface FlatGroup {
  node: GroupNode
  level: number
}
const flatten = (nodes: GroupNode[], level = 0, out: FlatGroup[] = []): FlatGroup[] => {
  for (const node of nodes) {
    out.push({ node, level })
    if (node.children?.length) flatten(node.children, level + 1, out)
  }
  return out
}
const flatTree = computed(() => flatten(tree.value))

const load = async () => {
  try {
    tree.value = await groupApi.tree()
  } catch {
    // 加载失败：http 拦截器已提示；保留当前分组树
  }
}

const GROUP_TYPE_TAGS: Record<string, TagType> = { 部门: 'primary', 专业: 'success', 班级: 'warning', 自定义: 'info' }
const typeTag = (t: string): TagType => GROUP_TYPE_TAGS[t] || 'info'

const onAdd = (parent: GroupNode | null) => {
  editingId.value = null
  parentId.value = parent?.id ?? null
  form.name = ''
  form.type = '部门'
  form.sort = 0
  form.parent = parent ? `${parent.name}（${parent.type}）` : ''
  dialogTitle.value = parent ? `在「${parent.name}」下新增子分组` : '新增顶级分组'
  dialogVisible.value = true
  // 弹窗复用：上次校验失败的红字会残留到新表单上，重开后清掉
  nextTick(() => formRef.value?.clearValidate())
}

const onEdit = (row: GroupNode) => {
  editingId.value = row.id
  parentId.value = row.parent_id
  form.name = row.name
  form.type = row.type
  form.sort = row.sort
  form.parent = ''
  dialogTitle.value = '编辑分组'
  dialogVisible.value = true
  nextTick(() => formRef.value?.clearValidate())
}

const onSave = async () => {
  if (!(await validateForm(formRef.value))) return
  saving.value = true
  try {
    const data = { name: form.name, type: form.type, sort: form.sort, parent_id: parentId.value }
    if (editingId.value) {
      await groupApi.update(editingId.value, data)
    } else {
      await groupApi.create(data)
    }
    ElMessage.success('保存成功')
    dialogVisible.value = false
    await load()
  } catch {
    // http 拦截器已提示；保留弹窗与已填内容，允许修正后重试
  } finally {
    saving.value = false
  }
}

const onDelete = async (row: GroupNode) => {
  const ok = await confirmBox(`确认删除分组「${row.name}」？存在子分组、题库或题目归属时无法删除。`, '提示')
  if (!ok) return
  // 后端校验失败时 http 拦截器已统一提示 detail
  try {
    await groupApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    // http 拦截器已提示
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
.mc-id {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
