<template>
  <div>
    <div class="toolbar">
      <span class="title">用户管理</span>
      <div class="filters">
        <el-input v-model="filters.keyword" placeholder="邮箱/姓名" clearable style="width: 180px" @keyup.enter="load" />
        <el-select v-model="filters.role" placeholder="角色" clearable style="width: 140px" @change="load">
          <el-option label="普通用户" value="user" />
          <el-option label="部门管理员" value="dept_admin" />
          <el-option label="超级管理员" value="super_admin" />
        </el-select>
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 120px" @change="load">
          <el-option label="待审批" value="pending" />
          <el-option label="正常" value="active" />
          <el-option label="已禁用" value="disabled" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
        <el-button type="success" @click="onAdd">新增用户</el-button>
        <el-button @click="onImport">批量导入</el-button>
      </div>
    </div>

    <el-table :data="rows" v-loading="loading" border class="mobile-table-hidden">
      <el-table-column prop="email" label="邮箱" min-width="180" />
      <el-table-column prop="name" label="姓名" width="120" />
      <el-table-column label="角色" width="110">
        <template #default="{ row }">{{ roleLabel(row.role) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="邮箱验证" width="100">
        <template #default="{ row }">
          <el-tag :type="row.email_verified ? 'success' : 'info'" size="small">{{ row.email_verified ? '已验证' : '未验证' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="360" fixed="right">
        <template #default="{ row }">
          <el-button size="small" v-if="row.status === 'pending'" type="success" @click="onApprove(row)">审批</el-button>
          <el-button size="small" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" @click="onGroups(row)">分配分组</el-button>
          <el-button size="small" type="warning" @click="onResetPwd(row)">重置密码</el-button>
          <el-button size="small" v-if="row.status === 'active'" type="danger" @click="onToggle(row, false)">禁用</el-button>
          <el-button size="small" v-else-if="row.status === 'disabled'" type="success" @click="onToggle(row, true)">启用</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：用户卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title">{{ row.name || '—' }} <span style="color:#909399;font-weight:400">{{ row.email }}</span></div>
        <div class="mc-row"><span class="mc-label">角色</span>{{ roleLabel(row.role) }}</div>
        <div class="mc-row"><span class="mc-label">状态</span><el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag></div>
        <div class="mc-row"><span class="mc-label">邮箱</span><el-tag :type="row.email_verified ? 'success' : 'info'" size="small">{{ row.email_verified ? '已验证' : '未验证' }}</el-tag></div>
        <div class="mc-actions">
          <el-button size="small" v-if="row.status === 'pending'" type="success" @click="onApprove(row)">审批</el-button>
          <el-button size="small" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" @click="onGroups(row)">分组</el-button>
          <el-button size="small" type="warning" @click="onResetPwd(row)">重置密码</el-button>
          <el-button size="small" v-if="row.status === 'active'" type="danger" @click="onToggle(row, false)">禁用</el-button>
          <el-button size="small" v-else-if="row.status === 'disabled'" type="success" @click="onToggle(row, true)">启用</el-button>
        </div>
      </div>
    </div>
    <el-empty v-if="isMobile && !loading && rows.length === 0" description="暂无用户" :image-size="80" />

    <el-pagination
      v-model:current-page="page" :page-size="pageSize" :total="total"
      layout="total, prev, pager, next" :small="isMobile"
      style="margin-top: 16px; justify-content: flex-end; display: flex"
      @current-change="load" />

    <el-dialog v-model="editVisible" title="编辑用户" width="420px">
      <el-form :model="editForm" label-width="90px">
        <el-form-item label="姓名"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role" style="width: 100%">
            <el-option label="普通用户" value="user" />
            <el-option label="部门管理员" value="dept_admin" />
            <el-option label="超级管理员" value="super_admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSaveEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="groupVisible" title="分配分组" width="480px">
      <el-tree
        ref="groupTree" :data="groupTreeData" node-key="id"
        :props="{ label: 'name', children: 'children' }" show-checkbox
        :default-checked-keys="checkedGroups" />
      <template #footer>
        <el-button @click="groupVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSaveGroups">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新增用户 -->
    <el-dialog v-model="addVisible" title="新增用户" width="460px">
      <el-form :model="addForm" label-width="90px" ref="addFormRef" :rules="addRules">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="addForm.email" placeholder="user@example.com" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="addForm.name" placeholder="留空则取邮箱前缀" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="addForm.role" style="width: 100%">
            <el-option label="普通用户" value="user" />
            <el-option label="部门管理员" value="dept_admin" />
            <el-option label="超级管理员" value="super_admin" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始密码">
          <el-input v-model="addForm.password" placeholder="留空自动生成随机密码" />
          <div class="field-tip">留空将自动生成 12 位随机密码；自定义至少 6 位</div>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="addForm.status" style="width: 100%">
            <el-option label="正常" value="active" />
            <el-option label="待审批" value="pending" />
            <el-option label="已禁用" value="disabled" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属分组">
          <el-tree-select v-model="addForm.group_ids" :data="groupTreeData" node-key="id" multiple
            :props="{ label: 'name', children: 'children' }" check-strictly show-checkbox
            placeholder="可多选，留空不分配" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSaveAdd">创建</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入用户 -->
    <el-dialog v-model="importVisible" title="批量导入用户" width="640px" @close="onImportClose">
      <el-steps :active="importStep" align-center finish-status="success">
        <el-step title="下载模板填写" />
        <el-step title="上传预览" />
        <el-step title="确认导入" />
      </el-steps>
      <div class="import-body">
        <template v-if="importStep === 0">
          <el-alert type="info" :closable="false" show-icon style="margin: 16px 0">
            请先下载模板，按格式填写用户信息后上传。
          </el-alert>
          <el-button type="primary" @click="onDownloadTemplate">下载用户导入模板</el-button>
          <div style="margin-top: 24px">
            <el-upload drag :auto-upload="false" :show-file-list="false" accept=".xlsx" :on-change="onImportFileChange">
              <el-icon class="el-icon--upload"><upload-filled /></el-icon>
              <div class="el-upload__text">拖拽 .xlsx 文件到此处，或<em>点击上传</em></div>
            </el-upload>
          </div>
        </template>
        <template v-if="importStep === 1 && importPreview">
          <el-alert :type="importPreview.valid_count > 0 ? 'success' : 'warning'" :closable="false" show-icon style="margin-bottom: 12px">
            共解析 {{ importPreview.total }} 行，有效 {{ importPreview.valid_count }} 行。
          </el-alert>
          <el-table :data="importPreview.rows" border size="small" style="margin-top: 8px" max-height="320">
            <el-table-column prop="row_index" label="行" width="60" />
            <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip />
            <el-table-column prop="name" label="姓名" width="100" />
            <el-table-column label="角色" width="100">
              <template #default="{ row }">{{ roleLabel(row.role) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">{{ statusLabel(row.status) }}</template>
            </el-table-column>
            <el-table-column label="分组" width="100">
              <template #default="{ row }">{{ (row.group_ids || []).join(',') || '—' }}</template>
            </el-table-column>
            <el-table-column label="状态校验" width="90">
              <template #default="{ row }">
                <el-tag :type="row.valid ? 'success' : 'danger'" size="small">{{ row.valid ? '有效' : '错误' }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-collapse v-if="importPreview.errors.length" style="margin-top: 12px">
            <el-collapse-item :title="`错误清单（${importPreview.errors.length}）`">
              <el-table :data="importPreview.errors" border size="small">
                <el-table-column prop="row" label="行号" width="70" />
                <el-table-column prop="email" label="邮箱" min-width="160" />
                <el-table-column prop="error" label="错误" />
              </el-table>
            </el-collapse-item>
          </el-collapse>
        </template>
        <template v-if="importStep === 2 && importResult">
          <el-result :icon="importResult.failed === 0 ? 'success' : 'warning'"
            :title="`导入完成：成功 ${importResult.success}，失败 ${importResult.failed}`">
          </el-result>
          <el-alert v-if="importResult.created.length" type="info" :closable="false" show-icon style="margin: 8px 0 12px">
            以下为系统生成的密码（留空密码的用户），请妥善记录并安全告知对应用户：
          </el-alert>
          <el-table v-if="importResult.created.length" :data="importResult.created" border size="small" max-height="260">
            <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip />
            <el-table-column prop="name" label="姓名" width="120" />
            <el-table-column prop="password" label="初始密码" width="160" />
          </el-table>
          <el-collapse v-if="importResult.errors.length" style="margin-top: 12px">
            <el-collapse-item :title="`失败清单（${importResult.errors.length}）`">
              <el-table :data="importResult.errors" border size="small">
                <el-table-column prop="row" label="行号" width="70" />
                <el-table-column prop="email" label="邮箱" min-width="160" />
                <el-table-column prop="error" label="错误" />
              </el-table>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
      <template #footer>
        <el-button @click="importVisible = false">关闭</el-button>
        <el-button v-if="importStep === 1" @click="importStep = 0">重新上传</el-button>
        <el-button v-if="importStep === 1" type="primary" :loading="importing" :disabled="!importPreview || importPreview.valid_count === 0" @click="onDoImport">
          确认导入 {{ importPreview?.valid_count || 0 }} 个用户
        </el-button>
        <el-button v-if="importStep === 2" type="primary" @click="onImportReset">继续导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { userApi, type UserItem, type UserImportPreview, type UserImportResult } from '@/api/user'
import { groupApi, type GroupNode } from '@/api/group'
import { useResponsive } from '@/composables/useResponsive'

const { isMobile } = useResponsive()

const rows = ref<UserItem[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 20
const total = ref(0)
const filters = reactive({ keyword: '', role: '', status: '' })

const editVisible = ref(false)
const groupVisible = ref(false)
const saving = ref(false)
const editForm = reactive({ id: 0, name: '', role: '' })
const groupTreeData = ref<GroupNode[]>([])
const checkedGroups = ref<number[]>([])
const currentUserId = ref(0)
const groupTree = ref()

// 新增用户
const addVisible = ref(false)
const addFormRef = ref<FormInstance>()
const addForm = reactive({
  email: '', name: '', role: 'user', password: '', status: 'active', group_ids: [] as number[],
})
const addRules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
}

// 批量导入
const importVisible = ref(false)
const importStep = ref(0)
const importPreview = ref<UserImportPreview | null>(null)
const importResult = ref<UserImportResult | null>(null)
const importing = ref(false)

const load = async () => {
  loading.value = true
  try {
    const params: Record<string, any> = { page: page.value, page_size: pageSize }
    if (filters.keyword) params.keyword = filters.keyword
    if (filters.role) params.role = filters.role
    if (filters.status) params.status = filters.status
    const data = await userApi.list(params)
    rows.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

const roleLabel = (r: string) => ({ user: '普通用户', dept_admin: '部门管理员', super_admin: '超级管理员' }[r] || r)
const statusLabel = (s: string) => ({ pending: '待审批', active: '正常', disabled: '已禁用' }[s] || s)
const statusTag = (s: string) => ({ pending: 'warning', active: 'success', disabled: 'info' }[s] || 'info')

const onApprove = async (row: UserItem) => {
  await userApi.approve(row.id)
  ElMessage.success('已审批通过')
  await load()
}
const onEdit = (row: UserItem) => {
  editForm.id = row.id
  editForm.name = row.name
  editForm.role = row.role
  editVisible.value = true
}
const onSaveEdit = async () => {
  saving.value = true
  try {
    await userApi.update(editForm.id, { name: editForm.name, role: editForm.role as any })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}
const onResetPwd = async (row: UserItem) => {
  await ElMessageBox.confirm(
    `确认为「${row.email}」生成新的随机密码？重置后请将新密码安全告知该用户。`,
    '重置密码',
    { type: 'warning' },
  )
  const res = await userApi.resetPassword(row.id)
  // 展示生成的新密码，便于管理员复制并告知用户
  ElMessageBox.alert(`新密码：${res.new_password}`, '重置成功', {
    confirmButtonText: '我已知晓',
    type: 'success',
  })
}
const onToggle = async (row: UserItem, enable: boolean) => {
  if (enable) await userApi.enable(row.id)
  else await userApi.disable(row.id)
  ElMessage.success('操作成功')
  await load()
}
const onGroups = async (row: UserItem) => {
  currentUserId.value = row.id
  groupTreeData.value = await groupApi.tree()
  checkedGroups.value = [...row.groups]
  groupVisible.value = true
}
const onSaveGroups = async () => {
  saving.value = true
  try {
    const ids = groupTree.value?.getCheckedKeys(false) as number[]
    await userApi.assignGroups(currentUserId.value, ids || [])
    ElMessage.success('已分配')
    groupVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

// 新增用户
const onAdd = async () => {
  groupTreeData.value = await groupApi.tree()
  Object.assign(addForm, { email: '', name: '', role: 'user', password: '', status: 'active', group_ids: [] })
  addVisible.value = true
}
const onSaveAdd = async () => {
  if (!addFormRef.value) return
  await addFormRef.value.validate(async (valid) => {
    if (!valid) return
    saving.value = true
    try {
      const res = await userApi.create({
        email: addForm.email,
        name: addForm.name,
        role: addForm.role as any,
        password: addForm.password || undefined,
        status: addForm.status as any,
        group_ids: addForm.group_ids,
      })
      ElMessageBox.alert(`新用户「${res.email}」创建成功，初始密码：${res.password}`, '创建成功', {
        confirmButtonText: '我已知晓', type: 'success',
      })
      addVisible.value = false
      await load()
    } finally {
      saving.value = false
    }
  })
}

// 批量导入
const onImport = async () => {
  importStep.value = 0
  importPreview.value = null
  importResult.value = null
  importVisible.value = true
}
const onImportClose = () => {
  importStep.value = 0
  importPreview.value = null
  importResult.value = null
}
const onImportReset = () => {
  importStep.value = 0
  importPreview.value = null
  importResult.value = null
}
const onDownloadTemplate = async () => {
  try {
    await userApi.downloadImportTemplate()
    ElMessage.success('模板已开始下载')
  } catch { /* http 拦截器已提示 */ }
}
const onImportFileChange = async (file: any) => {
  if (!file.raw) return
  importing.value = true
  try {
    importPreview.value = await userApi.importPreview(file.raw)
    importStep.value = 1
    ElMessage.success('解析完成')
  } finally {
    importing.value = false
  }
}
const onDoImport = async () => {
  if (!importPreview.value) return
  importing.value = true
  try {
    importResult.value = await userApi.doImport(importPreview.value.confirm_token)
    importStep.value = 2
    ElMessage.success('导入完成')
    await load()
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.title { font-size: 18px; font-weight: 600; }
.filters { display: flex; gap: 8px; }
.field-tip { font-size: 12px; color: #909399; line-height: 1.4; margin-top: 4px; }
.import-body { min-height: 220px; margin-top: 8px; }
@media (max-width: 767px) {
  .toolbar { flex-direction: column; align-items: stretch; gap: 12px; }
  .filters { flex-wrap: wrap; }
}
</style>
