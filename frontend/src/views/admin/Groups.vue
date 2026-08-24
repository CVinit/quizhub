<template>
  <div>
    <div class="toolbar">
      <span class="title">分组管理</span>
      <el-button type="primary" @click="onAdd(null)">新增顶级分组</el-button>
    </div>

    <el-table :data="tree" row-key="id" border default-expand-all :tree-props="{ children: 'children' }" class="mobile-table-hidden">
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column prop="type" label="类型" width="120">
        <template #default="{ row }">
          <el-tag :type="typeTag(row.type)">{{ row.type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="sort" label="排序" width="80" />
      <el-table-column label="操作" width="240">
        <template #default="{ row }">
          <el-button size="small" @click="onAdd(row)">新增子分组</el-button>
          <el-button size="small" type="primary" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：分组卡片（带层级缩进） -->
    <div class="mobile-card-list" v-if="isMobile">
      <template v-for="node in tree" :key="node.id">
        <div class="mc" :style="{ marginLeft: '0' }">
          <div class="mc-title"><el-tag :type="typeTag(node.type)" size="small">{{ node.type }}</el-tag> {{ node.name }}</div>
          <div class="mc-actions">
            <el-button size="small" @click="onAdd(node)">新增子分组</el-button>
            <el-button size="small" type="primary" @click="onEdit(node)">编辑</el-button>
            <el-button size="small" type="danger" @click="onDelete(node)">删除</el-button>
          </div>
        </div>
        <div class="mc" v-for="child in (node.children || [])" :key="child.id" style="margin-left: 16px; border-left: 3px solid var(--brand-primary-light-7);">
          <div class="mc-title"><el-tag :type="typeTag(child.type)" size="small">{{ child.type }}</el-tag> {{ child.name }}</div>
          <div class="mc-actions">
            <el-button size="small" @click="onAdd(child)">新增子分组</el-button>
            <el-button size="small" type="primary" @click="onEdit(child)">编辑</el-button>
            <el-button size="small" type="danger" @click="onDelete(child)">删除</el-button>
          </div>
        </div>
      </template>
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
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type FormInstance } from 'element-plus'
import { groupApi, type GroupNode } from '@/api/group'
import { useResponsive } from '@/composables/useResponsive'

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

const load = async () => {
  tree.value = await groupApi.tree()
}

const typeTag = (t: string) => ({ '部门': 'primary', '专业': 'success', '班级': 'warning', '自定义': 'info' }[t] || 'info')

const onAdd = (parent: GroupNode | null) => {
  editingId.value = null
  parentId.value = parent?.id ?? null
  form.name = ''
  form.type = '部门'
  form.sort = 0
  form.parent = parent ? `${parent.name}（${parent.type}）` : ''
  dialogTitle.value = parent ? `在「${parent.name}」下新增子分组` : '新增顶级分组'
  dialogVisible.value = true
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
}

const onSave = async () => {
  await formRef.value?.validate()
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
  } finally {
    saving.value = false
  }
}

const onDelete = async (row: GroupNode) => {
  await ElMessageBox.confirm(`确认删除分组「${row.name}」？存在子分组、题库或题目归属时无法删除。`, '提示', { type: 'warning' })
  // 后端校验失败时 http 拦截器已统一提示 detail；await 抛出会中断后续成功提示与刷新
  await groupApi.remove(row.id)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
</style>
