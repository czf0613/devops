<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { api, dateText, errorText, type Page } from '../api'
import type { Notification } from '../dashboard'
import AppButton from '../components/AppButton.vue'
import AppNotice from '../components/AppNotice.vue'
import Pager from '../components/Pager.vue'
const data = ref<Page<Notification> & { unread_count: number }>({
    items: [],
    total: 0,
    page: 1,
    page_size: 30,
    unread_count: 0,
  }),
  unread = ref(true),
  busy = ref(false),
  error = ref('')
let sequence = 0
onUnmounted(() => { sequence++ })
async function load(page = 1) {
  const current = ++sequence
  busy.value = true
  error.value = ''
  try {
    const result = await api<typeof data.value>(`/me/notifications?page=${page}&unread=${unread.value}`)
    if (current === sequence) data.value = result
  } catch (cause) {
    if (current === sequence) error.value = errorText(cause)
  } finally {
    if (current === sequence) busy.value = false
  }
}
async function read(id?: number) {
  try {
    await api(`/me/notifications/${id ? `${id}/` : ''}read`, 'PUT')
    await load(data.value.page)
  } catch (cause) {
    error.value = errorText(cause)
  }
}
onMounted(() => load())
function setUnread(value: boolean) {
  unread.value = value
  void load()
}
</script>
<template>
  <header class="heading-row">
    <h1>
      通知 <span class="muted">{{ data.unread_count }} 条未读</span>
    </h1>
    <div class="actions">
    <AppButton
      variant="secondary"
      :aria-pressed="unread"
      @click="setUnread(true)"
      >未读</AppButton
    ><AppButton
      variant="secondary"
      :aria-pressed="!unread"
      @click="setUnread(false)"
      >全部</AppButton
    >
    <AppButton variant="secondary" :disabled="busy || !data.unread_count" @click="read()"
      >全部标为已读</AppButton
    >
    </div>
  </header>
  <AppNotice v-if="error" tone="error">{{ error }}</AppNotice>
  <section class="notification-list">
    <div
      v-for="entry in data.items"
      :key="entry.id"
      class="notification-row"
      :class="{ unread: !entry.read_at }"
    >
      <div>
        <RouterLink :to="entry.url" @click="read(entry.id)"
          >{{ entry.item.title }} <span class="muted">#{{ entry.item.number }}</span></RouterLink
        ><small>{{ entry.actor?.display_name || '系统' }} · {{ dateText(entry.created_at) }}</small>
      </div>
      <AppButton v-if="!entry.read_at" variant="secondary" @click="read(entry.id)"
        >标为已读</AppButton
      >
    </div>
    <p v-if="!data.items.length" class="collaboration-empty">
      {{ busy ? '正在读取…' : '没有通知' }}
    </p>
    <Pager
      :page="data.page"
      :total="data.total"
      :page-size="data.page_size"
      :busy="busy"
      @change="load"
    />
  </section>
</template>
