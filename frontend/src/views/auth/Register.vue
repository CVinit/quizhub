<template>
  <div class="login-page">
    <el-card class="login-card">
      <LogoMark :size="48" :radius="10" class="brand-mark" />
      <h2 class="title">注册账号</h2>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="邮箱" prop="email">
          <el-input
            v-model="form.email"
            placeholder="请输入企业/机构邮箱"
            autocomplete="email"
            data-testid="register-email"
          />
        </el-form-item>
        <el-form-item label="姓名" prop="name">
          <el-input
            v-model="form.name"
            placeholder="选填，默认取邮箱前缀"
            autocomplete="name"
            data-testid="register-name"
          />
        </el-form-item>
        <el-form-item label="所属分组" prop="group_ids">
          <el-tree-select
            v-model="form.group_ids"
            :data="groupTree"
            node-key="id"
            multiple
            check-strictly
            :props="{ label: 'name', children: 'children' }"
            clearable
            :disabled="groupLoaded && groupTree.length === 0"
            :placeholder="groupRequired ? '请选择所属分组（必选）' : '选填，可多选'"
            style="width: 100%"
          />
          <div v-if="groupLoaded && groupTree.length === 0" class="group-empty-tip">
            管理员尚未开放自助注册分组，可直接提交注册，分组由管理员后续分配。
          </div>
        </el-form-item>
        <el-form-item label="图形验证码" prop="captcha_code">
          <div class="captcha-row">
            <el-input
              v-model="form.captcha_code"
              placeholder="输入图中数字"
              maxlength="10"
              data-testid="register-captcha"
            />
            <img
              v-if="captchaImg"
              :src="captchaImg"
              class="captcha-img"
              title="点击刷新"
              role="button"
              tabindex="0"
              aria-label="刷新图形验证码"
              @click="loadCaptcha"
              @keydown.enter.prevent="loadCaptcha"
              @keydown.space.prevent="loadCaptcha"
            />
            <el-button v-else :loading="captchaLoading" link @click="loadCaptcha">获取验证码</el-button>
          </div>
        </el-form-item>
        <el-form-item label="邮箱验证码" prop="code">
          <div class="code-row">
            <el-input
              v-model="form.code"
              placeholder="6 位验证码"
              maxlength="10"
              autocomplete="one-time-code"
              data-testid="register-email-code"
            />
            <el-button
              :loading="sending"
              :disabled="cd > 0 || !captchaOk"
              data-testid="register-send-code"
              @click="onSendCode"
            >
              {{ cd > 0 ? `${cd}s` : '发送验证码' }}
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="至少 6 位"
            autocomplete="new-password"
            data-testid="register-password"
          />
        </el-form-item>
        <el-button
          type="primary"
          native-type="submit"
          :loading="loading"
          style="width: 100%"
          data-testid="register-submit"
          >注册</el-button
        >
        <div class="links"><router-link to="/login">已有账号？去登录</router-link></div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance } from 'element-plus'
import { authApi } from '@/api/auth'
import type { GroupNode } from '@/api/group'
import LogoMark from '@/components/LogoMark.vue'
import { validateForm } from '@/utils/form'
import { useCountdown } from '@/composables/useCountdown'

const router = useRouter()
const formRef = ref<FormInstance>()
const loading = ref(false)
const sending = ref(false)
const captchaLoading = ref(false)
const { seconds: cd, start: startCountdown } = useCountdown()
const captchaImg = ref('')
const captchaId = ref('')
const groupTree = ref<GroupNode[]>([])
const groupRequired = ref(false)
const groupLoaded = ref(false)
const form = reactive({
  email: '',
  password: '',
  name: '',
  captcha_code: '',
  code: '',
  group_ids: [] as number[],
})
const rules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }],
  group_ids: [
    {
      validator: (_r: unknown, v: number[], cb: (e?: Error) => void) => {
        if (groupRequired.value && (!v || v.length === 0)) cb(new Error('请至少选择一个分组'))
        else cb()
      },
      trigger: 'change',
    },
  ],
  captcha_code: [{ required: true, message: '请输入图形验证码', trigger: 'blur' }],
  code: [{ required: true, message: '请输入邮箱验证码', trigger: 'blur' }],
  password: [{ required: true, min: 6, message: '密码至少 6 位', trigger: 'blur' }],
}
// 仅在通过图形验证码校验后才允许发送邮箱验证码（后端再次校验，前端仅做可用性提示）
const captchaOk = computed(() => form.captcha_code.length >= 4)

const loadCaptcha = async () => {
  captchaLoading.value = true
  try {
    const res = await authApi.captcha()
    captchaId.value = res.captcha_id
    captchaImg.value = res.image
  } catch {
    // 拦截器已提示；图形验证码加载失败时用户可点图刷新重试
  } finally {
    captchaLoading.value = false
  }
}

const loadGroups = async () => {
  try {
    const data = await authApi.registerGroups()
    groupTree.value = data.groups
    groupRequired.value = data.required
  } catch {
    /* 忽略，分组为可选时不阻断 */
  } finally {
    groupLoaded.value = true
  }
}

const onSendCode = async () => {
  if (!captchaOk.value) {
    ElMessage.warning('请先填写图形验证码')
    return
  }
  if (!form.email) {
    ElMessage.warning('请先填写邮箱')
    return
  }
  sending.value = true
  try {
    await authApi.sendCode(form.email, captchaId.value, form.captcha_code)
    ElMessage.success('验证码已发送到邮箱，请查收')
    startCountdown(60)
  } catch {
    // 失败后刷新图形验证码（原 id 已被消费）
    form.captcha_code = ''
    await loadCaptcha()
  } finally {
    sending.value = false
  }
}

const onSubmit = async () => {
  // 唯一入口是表单 submit（按钮上的 @click 已移除，否则一次点击会触发两次注册请求：
  // 第二次会消耗注册限流配额并弹出「验证码错误或已过期」）。
  if (loading.value) return
  if (!(await validateForm(formRef.value))) return
  loading.value = true
  try {
    await authApi.register(form.email, form.password, form.name, form.code, form.group_ids)
    ElMessage.success('注册成功，请登录')
    router.push('/login')
  } catch {
    // 拦截器已提示；注册失败时刷新图形验证码（图形验证码一次性，无论提交什么都会失效），
    // 并清空已输入的旧码——否则「发送验证码」按钮仍因旧码长度而可用，但已与新图不匹配。
    form.captcha_code = ''
    await loadCaptcha()
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadGroups()
  loadCaptcha()
})
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--brand-primary-light-9), var(--el-fill-color-light));
  padding: 16px;
}
.brand-mark {
  margin: 0 auto 12px;
}
.login-card {
  width: 380px;
  max-width: 100%;
  padding: 16px;
  border-top: 4px solid var(--brand-primary);
}
.title {
  text-align: center;
  margin: 0 0 20px;
}
.links {
  margin-top: 12px;
  font-size: 13px;
}
.links a {
  color: var(--brand-primary);
}
.captcha-row {
  display: flex;
  gap: 8px;
  width: 100%;
  align-items: center;
}
.captcha-row .el-input {
  flex: 1;
}
.captcha-img {
  height: 40px;
  width: 130px;
  cursor: pointer;
  border-radius: 4px;
  border: 1px solid var(--el-border-color);
}
.code-row {
  display: flex;
  gap: 8px;
  width: 100%;
}
.code-row .el-input {
  flex: 1;
}
.group-empty-tip {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
  margin-top: 4px;
}
@media (max-width: 767px) {
  .login-page {
    align-items: flex-start;
    padding-top: 24px;
  }
  .captcha-img {
    width: 110px;
    height: 36px;
  }
}
</style>
