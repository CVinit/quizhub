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
                :show-file-list="false" :auto-upload="true" :http-request="onLogoUpload"
                accept=".png,.jpg,.jpeg,.gif,.svg,.webp">
                <el-button :loading="logoUploading">上传 Logo</el-button>
              </el-upload>
              <el-button v-if="form[f.key]" link type="danger" @click="onClearLogo">移除</el-button>
              <span class="enc-tip">支持 png/jpg/svg 等，≤2MB</span>
            </div>
          </template>
          <template v-else-if="f.value_type === 'bool'">
            <el-switch :model-value="form[f.key] === 'true'" @update:model-value="form[f.key] = $event ? 'true' : 'false'" />
          </template>
          <template v-else-if="f.value_type === 'number'">
            <el-input-number :model-value="Number(form[f.key])" @update:model-value="form[f.key] = String($event)" />
          </template>
          <template v-else>
            <el-input v-model="form[f.key]" :type="f.key === 'smtp_password' ? 'password' : 'text'" show-word-limit />
            <span class="enc-tip" v-if="f.encrypted">（加密存储）</span>
          </template>
        </el-form-item>

        <el-form-item v-if="activeCat === 'smtp'">
          <el-button type="success" :loading="testing" @click="onSmtpTest">发送测试邮件</el-button>
          <el-input v-model="testEmail" placeholder="收件邮箱" style="width: 240px; margin-left: 12px" />
        </el-form-item>

        <!-- general 分类保存（site_name/brand_color 等）；site_logo 已由上传即时生效，提交时跳过它 -->
        <el-form-item v-if="activeCat !== 'smtp' || currentFields.length">
          <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { systemApi, type SettingCategory } from '@/api/system'
import { useSiteStore } from '@/stores/site'
import LogoMark from '@/components/LogoMark.vue'

const loading = ref(false)
const catLoading = ref(false)
const saving = ref(false)
const testing = ref(false)
const logoUploading = ref(false)
const categories = ref<SettingCategory[]>([])
const activeCat = ref('general')
const form = reactive<Record<string, string>>({})
const testEmail = ref('')
const site = useSiteStore()

const currentFields = computed(() => {
  const c = categories.value.find((x) => x.key === activeCat.value)
  return c?.fields || []
})

const loadCategories = async () => {
  loading.value = true
  try {
    categories.value = await systemApi.categories()
    await loadCategory(activeCat.value)
  } finally {
    loading.value = false
  }
}

const loadCategory = async (cat: string) => {
  catLoading.value = true
  try {
    const items = await systemApi.listSettings(cat)
    Object.keys(form).forEach((k) => delete form[k])
    items.forEach((it) => { form[it.key] = it.value })
    // logo 的当前值同步到站点 store（供预览即时反映）
    if (form.site_logo !== undefined) site.setLogo(form.site_logo)
  } finally {
    catLoading.value = false
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
    await systemApi.updateSettings(activeCat.value, updates)
    ElMessage.success('已保存')
  } finally {
    saving.value = false
  }
}

// Logo 自定义上传：调专用接口，成功后即时更新 store 与表单值
const onLogoUpload = async (opt: any) => {
  const file = opt.file as File
  logoUploading.value = true
  try {
    const res = await systemApi.uploadLogo(file)
    form.site_logo = res.url
    site.setLogo(res.url)
    ElMessage.success('Logo 已更新')
  } catch { /* 拦截器已提示 */ } finally {
    logoUploading.value = false
  }
}

const onClearLogo = async () => {
  form.site_logo = ''
  await systemApi.updateSettings('general', { site_logo: '' })
  site.setLogo('')
  ElMessage.success('已移除 Logo')
}

const onSmtpTest = async () => {
  if (!testEmail.value) { ElMessage.warning('请输入收件邮箱'); return }
  testing.value = true
  try {
    const res = await systemApi.smtpTest(testEmail.value)
    ElMessage.success(res.message || '测试邮件已发送')
  } finally {
    testing.value = false
  }
}

onMounted(loadCategories)
</script>

<style scoped>
.settings-page { max-width: 760px; }
.settings-page h2 { margin-bottom: 20px; }
.enc-tip { color: #909399; font-size: 12px; margin-left: 8px; }
.logo-upload { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
@media (max-width: 767px) {
  .settings-page { max-width: none; }
  /* 160px 固定 label 在手机上挤压输入区，改为上置标签 */
  .settings-page :deep(.el-form-item__label) {
    display: block; text-align: left; width: 100% !important; padding-bottom: 4px; font-size: 13px;
  }
  .settings-page :deep(.el-form-item) { display: block; }
  .settings-page :deep(.el-form-item__content) { margin-left: 0 !important; }
  .enc-tip { display: inline-block; margin-left: 0; margin-top: 4px; }
}
</style>
