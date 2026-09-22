<template>
  <div class="settings-page" v-loading="loading">
    <h2>系统设置</h2>

    <el-tabs v-model="activeCat" @tab-change="loadCategory">
      <el-tab-pane v-for="cat in categories" :key="cat.key" :label="cat.label" :name="cat.key" />
    </el-tabs>

    <el-card v-loading="catLoading">
      <el-form label-width="160px" style="max-width: 640px">
        <el-form-item v-for="f in currentFields" :key="f.key" :label="f.label">
          <!-- site_logo 由专用上传控件管理（不渲染普通文本框） -->
          <template v-if="f.key === 'site_logo'">
            <div class="logo-upload">
              <LogoMark :size="48" :radius="10" />
              <el-upload
                :show-file-list="false"
                :auto-upload="true"
                :http-request="onLogoUpload"
                accept=".png,.jpg,.jpeg,.gif,.webp"
              >
                <el-button :loading="logoUploading">上传 Logo</el-button>
              </el-upload>
              <el-button v-if="form[f.key]" link type="danger" @click="onClearLogo">移除</el-button>
              <span class="enc-tip">支持 png/jpg/webp 等位图，≤2MB（不支持 svg：同源内联会执行脚本）</span>
            </div>
          </template>
          <!-- 公开注册可选分组：用分组树下拉勾选，避免让管理员手填分组 ID -->
          <template v-else-if="f.key === 'register_allowed_group_ids'">
            <div style="width: 100%">
              <el-tree-select
                v-model="allowedGroupIds"
                :data="groupTree"
                node-key="id"
                multiple
                check-strictly
                :render-after-expand="false"
                :props="{ label: 'name', children: 'children' }"
                placeholder="不勾选表示注册页不可自选分组"
                style="width: 100%"
              />
              <span class="enc-tip">仅在注册页展示这里勾选的分组；不勾选则注册时不能选择任何分组。</span>
            </div>
          </template>
          <template v-else-if="f.value_type === 'bool'">
            <el-switch
              :model-value="form[f.key] === 'true'"
              @update:model-value="form[f.key] = $event ? 'true' : 'false'"
            />
          </template>
          <template v-else-if="f.value_type === 'number'">
            <el-input-number
              :model-value="Number(form[f.key])"
              @update:model-value="form[f.key] = $event == null ? '' : String($event)"
            />
          </template>
          <template v-else-if="f.encrypted">
            <!-- 列表接口对加密项只回掩码，不能绑回输入框：清空或自动填充后再保存会覆盖真实密钥 -->
            <el-input
              v-model="secretDraft[f.key]"
              type="password"
              show-password
              autocomplete="new-password"
              :placeholder="form[f.key] ? '已设置，留空则不修改' : '未设置'"
            />
            <span class="enc-tip">（加密存储，留空不修改）</span>
          </template>
          <template v-else>
            <el-input v-model="form[f.key]" />
          </template>
        </el-form-item>

        <el-form-item v-if="activeCat === 'smtp'">
          <el-button type="success" :loading="testing" @click="onSmtpTest">发送测试邮件</el-button>
          <el-input v-model="testEmail" placeholder="收件邮箱" style="width: 240px; margin-left: 12px" />
        </el-form-item>

        <!-- general 分类保存（site_name/brand_color 等）；site_logo 已由上传即时生效，提交时跳过它 -->
        <el-form-item v-if="activeCat !== 'smtp' || currentFields.length">
          <el-button type="primary" :loading="saving" :disabled="catLoading" @click="save">保存设置</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, computed } from 'vue'
import { ElMessage, type UploadRequestOptions } from 'element-plus'
import { systemApi, type SettingCategory } from '@/api/system'
import { groupApi, type GroupNode } from '@/api/group'
import { useSiteStore } from '@/stores/site'
import LogoMark from '@/components/LogoMark.vue'
import { confirmBox } from '@/utils/dialog'

const loading = ref(false)
const catLoading = ref(false)
const saving = ref(false)
const testing = ref(false)
const logoUploading = ref(false)
const categories = ref<SettingCategory[]>([])
const activeCat = ref('general')
const form = reactive<Record<string, string>>({})
/** 加密项的新值。空字符串表示本次不修改，绝不把接口返回的掩码写回去。 */
const secretDraft = reactive<Record<string, string>>({})
const testEmail = ref('')
const groupTree = ref<GroupNode[]>([])
const site = useSiteStore()

const currentFields = computed(() => {
  const c = categories.value.find((x) => x.key === activeCat.value)
  return c?.fields || []
})

// register_allowed_group_ids 在接口里是逗号分隔的字符串，下拉里用 number[] 双向映射
const allowedGroupIds = computed<number[]>({
  get: () =>
    (form.register_allowed_group_ids || '')
      .split(',')
      .map((s) => Number(s.trim()))
      .filter((n) => Number.isInteger(n) && n > 0),
  set: (ids) => {
    form.register_allowed_group_ids = (ids || []).join(',')
  },
})

// 分组树只在需要时拉取一次（注册设置页）
const loadGroupTree = async () => {
  if (groupTree.value.length) return
  try {
    groupTree.value = await groupApi.tree()
  } catch {
    /* 拉取失败仅影响下拉展示，不阻断设置页 */
  }
}

const loadCategories = async () => {
  loading.value = true
  try {
    categories.value = await systemApi.categories()
    await loadCategory(activeCat.value)
  } catch {
    // http 拦截器已提示；分类列表为空时页面无输入项，但不至于白屏
  } finally {
    loading.value = false
  }
}

// tab 切换传入的是 TabPaneName（string | number），统一收敛为分类 key
const loadCategory = async (cat: string | number) => {
  const key = String(cat)
  catLoading.value = true
  try {
    // 注册与审批分类需要分组树给「允许公开注册加入的分组」用
    if (key === 'register') await loadGroupTree()
    const items = await systemApi.listSettings(key)
    // 快速切换 tab 时先发出的慢响应可能后到达：过期响应会把 form 覆盖成上一个分类的键，
    // 导致当前分类输入框绑不到值、保存时提交错误的键（后端 400）。此处丢弃过期响应。
    if (key !== String(activeCat.value)) return
    Object.keys(form).forEach((k) => delete form[k])
    Object.keys(secretDraft).forEach((k) => delete secretDraft[k])
    items.forEach((it) => {
      form[it.key] = it.value
    })
    // logo 的当前值同步到站点 store（供预览即时反映）
    if (form.site_logo !== undefined) site.setLogo(form.site_logo)
  } catch {
    // http 拦截器已提示。只有仍是当前分类时才清空：过期请求的 catch 若无条件清 form，
    // 会把已经加载好的新分类值删掉，保存时提交空载荷。
    if (key !== String(activeCat.value)) return
    Object.keys(form).forEach((k) => delete form[k])
    Object.keys(secretDraft).forEach((k) => delete secretDraft[k])
  } finally {
    // 只有仍是最新分类时才结束加载态，避免被过期响应提前关掉
    if (key === String(activeCat.value)) catLoading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    // site_logo 由专用上传即时生效，保存时跳过它，避免把相对路径当作文本写回
    const updates = { ...form }
    if (updates.site_logo !== undefined) {
      if (form.site_logo) site.setLogo(form.site_logo)
      delete updates.site_logo
    }
    // 加密项：接口回的是掩码，不能原样提交。只提交用户新输入的值；留空表示不修改。
    for (const field of currentFields.value) {
      if (!field.encrypted) continue
      const draft = (secretDraft[field.key] || '').trim()
      if (draft) updates[field.key] = draft
      else delete updates[field.key]
    }
    await systemApi.updateSettings(activeCat.value, updates)
    Object.keys(secretDraft).forEach((k) => delete secretDraft[k])
    ElMessage.success('已保存')
  } catch {
    /* http 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

// Logo 自定义上传：调专用接口，成功后即时更新 store 与表单值
const MAX_LOGO_BYTES = 2 * 1024 * 1024
const onLogoUpload = async (opt: UploadRequestOptions) => {
  const file = opt.file
  // 前端先做一次体积/类型校验，避免大文件白跑一趟服务端（服务端仍会再校验）
  if (!/^image\/(png|jpe?g|gif|webp)$/i.test(file.type)) {
    ElMessage.warning('仅支持 png/jpg/gif/webp 位图（不支持 svg）')
    return
  }
  if (file.size > MAX_LOGO_BYTES) {
    ElMessage.warning('Logo 不能超过 2MB')
    return
  }
  logoUploading.value = true
  try {
    const res = await systemApi.uploadLogo(file)
    form.site_logo = res.url
    site.setLogo(res.url)
    ElMessage.success('Logo 已更新')
  } catch {
    /* 拦截器已提示 */
  } finally {
    logoUploading.value = false
  }
}

const onClearLogo = async () => {
  try {
    // 先落库成功再更新本地状态与 store，避免请求失败后界面显示已移除
    await systemApi.updateSettings(activeCat.value, { site_logo: '' })
    form.site_logo = ''
    site.setLogo('')
    ElMessage.success('已移除 Logo')
  } catch {
    /* http 拦截器已提示 */
  }
}

const onSmtpTest = async () => {
  if (!testEmail.value) {
    ElMessage.warning('请输入收件邮箱')
    return
  }
  // 测试接口从数据库读取已落库的 smtp 配置：未保存的修改不会被测试到，
  // 直接提示成功会让人误以为新配置可用（首次配置时则必然报「请先配置 SMTP 服务器」）。
  const ok = await confirmBox(
    '测试邮件使用已保存的配置发送。若刚修改过参数，请先点「保存设置」。是否继续？',
    '发送测试邮件',
    {
      confirmButtonText: '继续测试',
      cancelButtonText: '先去保存',
    },
  )
  if (!ok) return
  testing.value = true
  try {
    const res = await systemApi.smtpTest(testEmail.value)
    ElMessage.success(res.message || '测试邮件已发送')
  } catch {
    // http 拦截器已提示（含 SMTP 原始错误）
  } finally {
    testing.value = false
  }
}

onMounted(loadCategories)
</script>

<style scoped>
.settings-page {
  max-width: 760px;
}
.settings-page h2 {
  margin-bottom: 20px;
}
.enc-tip {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 8px;
}
.logo-upload {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
@media (max-width: 767px) {
  .settings-page {
    max-width: none;
  }
  /* 160px 固定 label 在手机上挤压输入区，改为上置标签 */
  .settings-page :deep(.el-form-item__label) {
    display: block;
    text-align: left;
    width: 100% !important;
    padding-bottom: 4px;
    font-size: 13px;
  }
  .settings-page :deep(.el-form-item) {
    display: block;
  }
  .settings-page :deep(.el-form-item__content) {
    margin-left: 0 !important;
  }
  .enc-tip {
    display: inline-block;
    margin-left: 0;
    margin-top: 4px;
  }
}
</style>
