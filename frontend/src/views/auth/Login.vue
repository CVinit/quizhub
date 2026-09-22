<template>
  <div class="login-page">
    <el-card class="login-card">
      <LogoMark :size="48" :radius="10" class="brand-mark" />
      <h2 class="title">登录 {{ site.site_name }}</h2>
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="请输入邮箱" autocomplete="email" data-testid="login-email" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入密码"
            autocomplete="current-password"
            data-testid="login-password"
          />
        </el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" style="width: 100%" data-testid="login-submit"
          >登录</el-button
        >
        <div class="links">
          <router-link to="/register">注册账号</router-link>
          <router-link to="/verify">重新发送验证码</router-link>
        </div>
      </el-form>
      <p class="hint">管理员账号请使用部署时配置的邮箱和密码</p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import LogoMark from '@/components/LogoMark.vue'
import { validateForm } from '@/utils/form'

const router = useRouter()
const auth = useAuthStore()
const site = useSiteStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ email: '', password: '' })
const rules = {
  email: [{ required: true, message: '请输入邮箱', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const onSubmit = async () => {
  // 提交按钮是 native-type="submit"，表单的 submit 事件是唯一入口（按钮上不再挂 @click，
  // 否则一次点击会同时触发 click 与 submit，向接口发两次请求）。
  if (loading.value) return
  if (!(await validateForm(formRef.value))) return
  loading.value = true
  try {
    await auth.login(form.email, form.password)
    router.push('/')
  } catch {
    // 拦截器已提示（凭据错误的 401 会显示后端返回的具体原因）
  } finally {
    loading.value = false
  }
}
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
  display: flex;
  justify-content: space-between;
  margin-top: 12px;
  font-size: 13px;
}
.links a {
  color: var(--brand-primary);
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 12px 0 0;
  text-align: center;
}
</style>
