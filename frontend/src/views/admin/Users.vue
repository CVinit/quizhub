<template>
  <div>
    <div class="toolbar">
      <span class="title">用户管理</span>
      <div class="filters">
        <el-input
          v-model="filters.keyword"
          placeholder="邮箱/姓名"
          clearable
          style="width: 180px"
          @keyup.enter="onFilterChange"
        />
        <el-select v-model="filters.role" placeholder="角色" clearable style="width: 140px" @change="onFilterChange">
          <el-option v-for="o in USER_ROLE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 120px" @change="onFilterChange">
          <el-option v-for="o in USER_STATUS_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-button type="primary" @click="onFilterChange">查询</el-button>
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
          <el-tag :type="row.email_verified ? 'success' : 'info'" size="small">{{
            row.email_verified ? '已验证' : '未验证'
          }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="420" fixed="right">
        <template #default="{ row }">
          <el-button size="small" v-if="row.status === 'pending'" type="success" @click="onApprove(row as UserItem)"
            >审批</el-button
          >
          <el-button size="small" @click="onEdit(row as UserItem)">编辑</el-button>
          <el-button size="small" @click="onGroups(row as UserItem)">分配分组</el-button>
          <el-button size="small" type="warning" @click="onResetPwd(row as UserItem)">重置密码</el-button>
          <el-button size="small" v-if="row.status === 'active'" type="danger" @click="onToggle(row as UserItem, false)"
            >禁用</el-button
          >
          <el-button
            size="small"
            v-else-if="row.status === 'disabled'"
            type="success"
            @click="onToggle(row as UserItem, true)"
            >启用</el-button
          >
          <el-tooltip v-if="deleteReason(row as UserItem)" :content="deleteReason(row as UserItem)!" placement="top">
            <span><el-button size="small" type="danger" plain disabled>删除</el-button></span>
          </el-tooltip>
          <el-button size="small" type="danger" plain v-else @click="onDelete(row as UserItem)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机端：用户卡片 -->
    <div class="mobile-card-list" v-loading="loading" v-if="isMobile && rows.length">
      <div class="mc" v-for="row in rows" :key="row.id">
        <div class="mc-title">
          {{ row.name || '—' }}
          <span style="color: var(--el-text-color-secondary); font-weight: 400">{{ row.email }}</span>
        </div>
        <div class="mc-row"><span class="mc-label">角色</span>{{ roleLabel(row.role) }}</div>
        <div class="mc-row">
          <span class="mc-label">状态</span
          ><el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </div>
        <div class="mc-row">
          <span class="mc-label">邮箱</span
          ><el-tag :type="row.email_verified ? 'success' : 'info'" size="small">{{
            row.email_verified ? '已验证' : '未验证'
          }}</el-tag>
        </div>
        <div class="mc-actions">
          <el-button size="small" v-if="row.status === 'pending'" type="success" @click="onApprove(row)"
            >审批</el-button
          >
          <el-button size="small" @click="onEdit(row)">编辑</el-button>
          <el-button size="small" @click="onGroups(row)">分组</el-button>
          <el-button size="small" type="warning" @click="onResetPwd(row)">重置密码</el-button>
          <el-button size="small" v-if="row.status === 'active'" type="danger" @click="onToggle(row, false)"
            >禁用</el-button
          >
          <el-button size="small" v-else-if="row.status === 'disabled'" type="success" @click="onToggle(row, true)"
            >启用</el-button
          >
          <el-tooltip v-if="deleteReason(row)" :content="deleteReason(row)!" placement="top">
            <span><el-button size="small" type="danger" plain disabled>删除</el-button></span>
          </el-tooltip>
          <el-button size="small" type="danger" plain v-else @click="onDelete(row)">删除</el-button>
        </div>
      </div>
    </div>
    <el-empty v-if="isMobile && !loading && rows.length === 0" description="暂无用户" :image-size="80" />

    <el-pagination
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      :small="isMobile"
      style="margin-top: 16px; justify-content: flex-end; display: flex"
      @current-change="load"
    />

    <el-dialog v-model="editVisible" title="编辑用户" width="420px">
      <el-form :model="editForm" label-width="90px">
        <el-form-item label="姓名"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role" style="width: 100%" :disabled="!auth.isSuper">
            <el-option v-for="o in USER_ROLE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <div v-if="!auth.isSuper" class="field-tip">仅超级管理员可修改角色</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSaveEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="groupVisible" title="分配分组" width="480px">
      <!-- key 跟随被操作用户：el-tree 仅在挂载时按 default-checked-keys 初始化，prop 变化
           只做「补齐勾选」而不会取消旧勾选，弹窗复用会让上一个用户的分组残留并一起提交 -->
      <el-tree
        ref="groupTree"
        :key="`gt-${groupsSeq}-${currentUserId}`"
        :data="groupTreeData"
        node-key="id"
        :props="{ label: 'name', children: 'children' }"
        show-checkbox
        :default-checked-keys="checkedGroups"
      />
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
          <el-select v-model="addForm.role" style="width: 100%" :disabled="!auth.isSuper">
            <el-option v-for="o in creatableRoles" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input v-model="addForm.password" type="password" show-password placeholder="请输入至少 6 位密码" />
          <div class="field-tip">初始密码由管理员设置，请通过安全渠道告知用户</div>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="addForm.status" style="width: 100%">
            <el-option v-for="o in USER_STATUS_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属分组">
          <el-tree-select
            v-model="addForm.group_ids"
            :data="groupTreeData"
            node-key="id"
            multiple
            :props="{ label: 'name', children: 'children' }"
            check-strictly
            show-checkbox
            placeholder="可多选，留空不分配"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSaveAdd">创建</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入用户（弹窗自带三步流程状态） -->
    <UserImportDialog v-model="importVisible" @imported="load" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules, type TreeInstance } from 'element-plus'
import { userApi, type UserItem, type UserListParams } from '@/api/user'
import { groupApi, type GroupNode } from '@/api/group'
import { useAuthStore } from '@/stores/auth'
import { useResponsive } from '@/composables/useResponsive'
import { alertBox, confirmBox, promptBox } from '@/utils/dialog'
import { validateForm } from '@/utils/form'
import { roleLabel, statusLabel, statusTag, USER_ROLE_OPTIONS, USER_STATUS_OPTIONS } from '@/constants/user'
import UserImportDialog from '@/components/UserImportDialog.vue'

const { isMobile } = useResponsive()
const route = useRoute()
const auth = useAuthStore()
/** 非超管创建管理员会被后端 403，创建下拉只给普通用户。 */
const creatableRoles = computed(() =>
  auth.isSuper ? USER_ROLE_OPTIONS : USER_ROLE_OPTIONS.filter((o) => o.value === 'user'),
)

const rows = ref<UserItem[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 20
const total = ref(0)
const filters = reactive<{ keyword: string; role: UserItem['role'] | ''; status: UserItem['status'] | '' }>({
  keyword: '',
  role: '',
  status: '',
})

const editVisible = ref(false)
const groupVisible = ref(false)
const saving = ref(false)
const editForm = reactive<{ id: number; name: string; role: UserItem['role'] }>({ id: 0, name: '', role: 'user' })
const groupTreeData = ref<GroupNode[]>([])
const checkedGroups = ref<number[]>([])
const currentUserId = ref(0)
const groupTree = ref<TreeInstance>()

// 新增用户
const addVisible = ref(false)
const addFormRef = ref<FormInstance>()
const addForm = reactive<{
  email: string
  name: string
  role: UserItem['role']
  password: string
  status: UserItem['status']
  group_ids: number[]
}>({
  email: '',
  name: '',
  role: 'user',
  password: '',
  status: 'active',
  group_ids: [],
})
const addRules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 6, message: '密码至少 6 位', trigger: 'blur' },
    // 与后端 schemas/auth.py 的 Field(min_length=6, max_length=72) 及本页「重置密码」保持一致
    { max: 72, message: '密码最多 72 位', trigger: 'blur' },
  ],
}

// 批量导入：仅保留开关，流程状态在 UserImportDialog 内部
const importVisible = ref(false)
const onImport = () => {
  importVisible.value = true
}

/** 列表加载序号：快速切筛选/翻页会并发多个请求，只接受最新一次的结果。 */
let loadSeq = 0

const load = async () => {
  const seq = ++loadSeq
  loading.value = true
  try {
    const params: UserListParams = { page: page.value, page_size: pageSize }
    if (filters.keyword) params.keyword = filters.keyword
    if (filters.role) params.role = filters.role
    if (filters.status) params.status = filters.status
    const data = await userApi.list(params)
    if (seq !== loadSeq) return
    rows.value = data.items
    total.value = data.total
  } catch {
    // 加载失败：http 拦截器已提示；保留当前列表，避免把失败误显示为“没有用户”
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

/** 筛选条件变化：先回到第 1 页，否则新结果集不足当前页时会出现“空列表”。 */
const onFilterChange = () => {
  page.value = 1
  return load()
}

/**
 * 删除按钮不可用的原因（null 表示可删除）。
 *
 * 与后端 user_service.delete_user 的校验保持一致：仅超级管理员可删、不可删自己。
 * 返回原因而非静默隐藏按钮，避免非超管管理员误以为系统缺少删除功能。
 */
const deleteReason = (row: UserItem): string | null => {
  if (!auth.isSuper) return '仅超级管理员可删除用户'
  if (row.id === auth.user?.id) return '不能删除当前登录账号'
  return null
}

const onApprove = async (row: UserItem) => {
  try {
    await userApi.approve(row.id)
    ElMessage.success('已审批通过')
    await load()
  } catch {
    // http 拦截器已提示
  }
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
    // 非超管带上 role（即使没改）会被后端 403，只提交姓名
    await userApi.update(editForm.id, {
      name: editForm.name,
      ...(auth.isSuper ? { role: editForm.role } : {}),
    })
    ElMessage.success('已保存')
    editVisible.value = false
    await load()
  } catch {
    // http 拦截器已提示；保留弹窗与已填内容
  } finally {
    saving.value = false
  }
}
const onResetPwd = async (row: UserItem) => {
  const value = await promptBox(`为「${row.email}」设置新密码`, '重置密码', {
    inputType: 'password',
    inputPattern: /^.{6,72}$/,
    inputErrorMessage: '密码长度必须为 6~72 位',
    confirmButtonText: '确认',
    cancelButtonText: '取消',
  })
  if (value === null) return
  try {
    await userApi.resetPassword(row.id, value)
    ElMessage.success('密码已重置，请通过安全渠道告知用户')
  } catch {
    // http 拦截器已提示
  }
}
const onToggle = async (row: UserItem, enable: boolean) => {
  try {
    if (enable) await userApi.enable(row.id)
    else await userApi.disable(row.id)
    ElMessage.success('操作成功')
    await load()
  } catch {
    // http 拦截器已提示；开关状态由列表数据驱动，失败时不本地改写
  }
}
const onDelete = async (row: UserItem) => {
  const ok = await confirmBox(
    `确认删除用户「${row.email}」？其练习记录、考试成绩等数据将一并删除，操作不可恢复。`,
    '删除用户',
    { confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  if (!ok) return
  try {
    await userApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    // http 拦截器已提示
  }
}
/**
 * 打开「分配分组」弹窗。
 *
 * 分组树与目标用户都在 await 之后才赋值，若期间又点了另一个用户，先返回的响应会把
 * X 的分组写到 Y 的弹窗上（提交即多授权）。用请求序号丢弃过期响应。
 */
let groupsSeq = 0
const onGroups = async (row: UserItem) => {
  const seq = ++groupsSeq
  let tree: GroupNode[]
  try {
    tree = await groupApi.tree()
  } catch {
    // 分组树加载失败：拦截器已提示；仍打开弹窗为空树会比"点了没反应"更误导，故直接返回
    return
  }
  if (seq !== groupsSeq) return
  groupTreeData.value = tree
  currentUserId.value = row.id
  checkedGroups.value = [...row.groups]
  groupVisible.value = true
}
const onSaveGroups = async () => {
  saving.value = true
  try {
    const ids = (groupTree.value?.getCheckedKeys(false) ?? []).map((k) => Number(k)).filter((n) => Number.isInteger(n))
    await userApi.assignGroups(currentUserId.value, ids)
    ElMessage.success('已分配')
    groupVisible.value = false
    await load()
  } catch {
    // http 拦截器已提示；保留弹窗与勾选结果
  } finally {
    saving.value = false
  }
}

// 新增用户
const onAdd = async () => {
  try {
    groupTreeData.value = await groupApi.tree()
  } catch {
    // 分组树缺失仍可创建用户（分组可在用户列表里再分配），不因此阻断新增流程
  }
  Object.assign(addForm, { email: '', name: '', role: 'user', password: '', status: 'active', group_ids: [] })
  addVisible.value = true
  // 弹窗复用：上次校验失败的红字会残留到新表单上，重开后清掉
  nextTick(() => addFormRef.value?.clearValidate())
}
const onSaveAdd = async () => {
  if (!(await validateForm(addFormRef.value))) return
  saving.value = true
  try {
    const res = await userApi.create({
      email: addForm.email,
      name: addForm.name,
      role: addForm.role,
      password: addForm.password,
      status: addForm.status,
      group_ids: addForm.group_ids,
    })
    await alertBox(`新用户「${res.email}」创建成功，请通过安全渠道告知初始密码`, '创建成功', {
      confirmButtonText: '我已知晓',
      type: 'success',
    })
    addVisible.value = false
    await load()
  } catch {
    // http 拦截器已提示；保留弹窗与已填内容
  } finally {
    saving.value = false
  }
}

// 批量导入
onMounted(() => {
  // 支持从「概览面板」带 ?status=pending 等跳转过来。
  // 只在此处读取 query：若放进 load()，用户清空筛选后会被 query 再次回填，导致筛选无法取消。
  const fromQuery = route.query.status
  if (fromQuery === 'pending' || fromQuery === 'active' || fromQuery === 'disabled') {
    filters.status = fromQuery
    page.value = 1
  }
  load()
})
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
.field-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.4;
  margin-top: 4px;
}
@media (max-width: 767px) {
  .toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .filters {
    flex-wrap: wrap;
  }
  .filters > * {
    flex: 1 1 40%;
    min-width: 0;
  }
  .filters .el-button {
    flex: 1 1 40%;
  }
}
</style>
