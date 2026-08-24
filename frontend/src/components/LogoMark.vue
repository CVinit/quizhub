<template>
  <span class="logo-mark" :style="markStyle">
    <img v-if="logoUrl" :src="logoUrl" class="logo-img" :style="imgStyle" :alt="site.site_name" @error="onErr" />
    <span v-else class="logo-fallback" :style="fallbackStyle">{{ char }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useSiteStore } from '@/stores/site'

const props = withDefaults(defineProps<{ size?: number; radius?: number }>(), {
  size: 30, radius: 6,
})

const site = useSiteStore()
// 本地存储加载失败的 URL，触发回退到首字方块（避免破图）
const failed = ref('')

const logoUrl = computed(() => {
  const u = site.site_logo
  if (!u || u === failed.value) return ''
  return u
})
const char = computed(() => site.logoChar)

const markStyle = computed(() => ({ width: `${props.size}px`, height: `${props.size}px` }))
const imgStyle = computed(() => ({ maxWidth: '100%', maxHeight: '100%' }))
const fallbackStyle = computed(() => ({
  width: '100%', height: '100%',
  borderRadius: `${props.radius}px`,
  fontSize: `${Math.round(props.size * 0.55)}px`,
}))

const onErr = () => { failed.value = site.site_logo }
watch(() => site.site_logo, () => { failed.value = '' })

// 确保 store 已加载（在未经过 App.vue useTheme 的场景也安全）
site.load()
</script>

<style scoped>
.logo-mark { display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
.logo-img { object-fit: contain; }
.logo-fallback {
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--brand-primary); color: #fff;
  font-weight: 700;
}
</style>
