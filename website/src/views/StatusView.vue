<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AppButton from '../components/AppButton.vue'

type State = 'loading' | 'ready' | 'error'
const state = ref<State>('loading')
const message = ref('正在检查服务连接…')

async function checkStatus() {
  state.value = 'loading'
  message.value = '正在检查服务连接…'
  try {
    const response = await fetch('/api/health/ready', { signal: AbortSignal.timeout(5000) })
    const data: unknown = await response.json()
    if (
      !response.ok ||
      typeof data !== 'object' ||
      data === null ||
      !('status' in data) ||
      data.status !== 'ready'
    ) {
      throw new Error('Service unavailable')
    }
    state.value = 'ready'
    message.value = '后端服务、PostgreSQL 与 Redis 连接正常。'
  } catch {
    state.value = 'error'
    message.value = '暂时无法连接，请确认本地后端、PostgreSQL 与 Redis 已启动。'
  }
}

onMounted(checkStatus)
</script>

<template>
  <section class="panel">
    <p class="eyebrow">开发环境</p>
    <h1>服务连接状态</h1>
    <p>通过当前网站检查后端服务、PostgreSQL 与 Redis 的连接。</p>
    <div class="status" :class="state" role="status" aria-live="polite">
      <span class="status-dot" aria-hidden="true"></span>
      {{ message }}
    </div>
    <AppButton :busy="state === 'loading'" @click="checkStatus">重新检查</AppButton>
  </section>
</template>
