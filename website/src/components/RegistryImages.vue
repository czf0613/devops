<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, errorText } from '../api'
import { auth } from '../auth'
import type { Repository } from '../repositories'
import type { Namespace, RegistryImage, RegistryPage, RegistryTag } from '../registry'
import AppButton from './AppButton.vue'
import AppConfirm from './AppConfirm.vue'
import AppNotice from './AppNotice.vue'
import AppPanel from './AppPanel.vue'
import Pager from './Pager.vue'

const props = defineProps<{ repository: Repository; namespace: Namespace; host: string }>()
const canManage = computed(() => props.repository.permissions.is_owner && !props.repository.archived)
const imageBase = computed(() => `/repositories/${props.repository.id}/registry/namespaces/${props.namespace.id}`)
const selectedImage = ref(''), images = ref<RegistryImage[]>([]), tags = ref<RegistryTag[]>([])
const imagePage = ref(1), imageTotal = ref(0), tagPage = ref(1), tagTotal = ref(0)
const loadingImages = ref(false), loadingTags = ref(false), busy = ref(false)
const message = ref(''), feedbackError = ref(''), imageError = ref(''), tagError = ref('')
const confirmation = ref<InstanceType<typeof AppConfirm>>()
let lifetime = 0, imageReads = 0, tagReads = 0
onUnmounted(() => { lifetime++; imageReads++; tagReads++ })
watch(() => [props.repository.id, props.namespace.id, auth.user?.id], () => {
  lifetime++; imagePage.value = 1; selectedImage.value = ''; tags.value = []; images.value = []
  busy.value = false; message.value = feedbackError.value = ''; void loadImages()
}, { immediate: true })
watch(imagePage, () => { void loadImages() })
async function loadImages() {
  const current = ++imageReads, life = lifetime
  selectedImage.value = ''; images.value = []; imageTotal.value = 0; imageError.value = ''; loadingImages.value = false
  loadingImages.value = true
  try {
    const data = await api<RegistryPage<RegistryImage>>(`${imageBase.value}/images?page=${imagePage.value}`)
    if (current !== imageReads || life !== lifetime) return
    images.value = data.items; imageTotal.value = data.total
  } catch (cause) { if (current === imageReads && life === lifetime) imageError.value = errorText(cause) }
  finally { if (current === imageReads && life === lifetime) loadingImages.value = false }
}
watch([selectedImage, tagPage], () => { void loadTags() })
watch(selectedImage, () => { tagPage.value = 1 })
async function loadTags() {
  const current = ++tagReads, life = lifetime
  tags.value = []; tagTotal.value = 0; tagError.value = ''; loadingTags.value = false
  if (!selectedImage.value) return
  loadingTags.value = true
  try {
    const data = await api<RegistryPage<RegistryTag>>(`${imageBase.value}/tags?image=${encodeURIComponent(selectedImage.value)}&page=${tagPage.value}`)
    if (current !== tagReads || life !== lifetime) return
    tags.value = data.items; tagTotal.value = data.total
  } catch (cause) { if (current === tagReads && life === lifetime) tagError.value = errorText(cause) }
  finally { if (current === tagReads && life === lifetime) loadingTags.value = false }
}
async function remove(tag: RegistryTag) {
  if (!tag.digest || busy.value) return
  const life = lifetime, image = selectedImage.value, namespace = props.namespace.id
  const affected = tags.value.filter(item => item.digest === tag.digest).map(item => item.tag).join('、')
  if (!await confirmation.value?.ask(`删除以下 digest 对应的整个镜像版本：\n${tag.digest}\n\n当前页指向它的标签：${affected}。\n其他页的同 digest 标签也会删除；引用该版本的多平台镜像可能无法拉取。\n\n此操作不可撤销。磁盘空间将在管理员执行 GC 后回收。`, '删除整个镜像版本？', '删除版本') || life !== lifetime || image !== selectedImage.value || namespace !== props.namespace.id) return
  busy.value = true; tagError.value = ''; message.value = ''
  try {
    await api(`${imageBase.value}/manifest?image=${encodeURIComponent(image)}`, 'DELETE', { digest: tag.digest })
    if (life === lifetime) { message.value = '已删除镜像版本及指向它的标签'; await loadTags() }
  } catch (cause) { if (life === lifetime) tagError.value = errorText(cause) }
  finally { if (life === lifetime) busy.value = false }
}
async function copy(tag: RegistryTag) {
  const life = lifetime
  message.value = ''; feedbackError.value = ''
  try {
    await navigator.clipboard.writeText(`docker pull ${props.host}/${props.namespace.name}/${selectedImage.value}@${tag.digest}`)
    if (life === lifetime) message.value = '已复制按 digest 拉取的命令'
  } catch { if (life === lifetime) feedbackError.value = '复制失败，请手动复制镜像地址。' }
}
function size(value?: number) { return value ? `${(value / 1024 / 1024).toFixed(1)} MiB` : '—' }
</script>

<template>
  <AppNotice v-model="message" tone="success" placement="floating" />
  <AppNotice v-model="feedbackError" tone="error" placement="floating" />
  <AppPanel :title="`${namespace.name} 的镜像`" description="选择镜像以查看标签。删除以 digest 为单位，会影响同一版本的所有标签。">
    <template #actions><AppButton variant="secondary" :disabled="busy || loadingImages" @click="loadImages">刷新镜像</AppButton></template>
    <AppNotice v-if="imageError" tone="error">{{ imageError }}</AppNotice>
    <p v-if="loadingImages" class="muted">正在读取镜像…</p>
    <p v-else-if="!images.length && !imageError" class="empty-state">此 namespace 尚无镜像。推送到 {{ host }}/{{ namespace.name }}/&lt;image&gt;:&lt;tag&gt; 后刷新。</p>
    <div v-else class="image-list"><button v-for="item in images" :key="item.name" type="button" :class="{ active: selectedImage === item.name }" :disabled="busy" @click="selectedImage = item.name"><strong>{{ item.name }}</strong><small>{{ item.reference }}</small></button></div>
    <Pager v-if="imageTotal > 20" :page="imagePage" :total="imageTotal" :busy="busy || loadingImages" @change="imagePage = $event" />
    <div v-if="selectedImage" class="tag-section">
      <h3>{{ selectedImage }} 的标签</h3>
      <AppNotice v-if="tagError" tone="error">{{ tagError }}</AppNotice>
      <p v-if="loadingTags" class="muted">正在读取版本…</p>
      <p v-else-if="!tags.length && !tagError" class="muted">暂无标签</p>
      <ul class="tag-list"><li v-for="tag in tags" :key="tag.tag"><div><strong>{{ tag.tag }}</strong><small v-if="tag.missing">已删除，请刷新</small><template v-else><code>{{ tag.digest }}</code><small>{{ tag.platforms?.length ? tag.platforms.join('、') : '单平台镜像' }} · {{ size(tag.size_bytes) }}<span v-if="tag.attestation_count"> · {{ tag.attestation_count }} 份构建证明</span></small></template></div><div v-if="tag.digest" class="actions"><AppButton variant="secondary" class="small" @click="copy(tag)">复制拉取命令</AppButton><AppButton v-if="canManage" variant="danger" class="small" :disabled="busy" @click="remove(tag)">删除版本</AppButton></div></li></ul>
      <Pager v-if="tagTotal > 20" :page="tagPage" :total="tagTotal" :busy="busy || loadingTags" @change="tagPage = $event" />
    </div>
  </AppPanel>
  <AppConfirm :key="`${repository.id}-${namespace.id}-${auth.user?.id}`" ref="confirmation" />
</template>

<style scoped>
.tag-list { list-style: none; margin: 0; padding: 0; }
.tag-list li { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 0; border-bottom: 1px solid var(--line); }
.tag-list small, .tag-list code { display: block; margin-top: 8px; overflow-wrap: anywhere; color: var(--muted); }
.image-list { display: grid; gap: 8px; }
.image-list button { text-align: left; border: 1px solid var(--line); border-radius: 8px; padding: 14px 18px; color: var(--text); background: var(--surface); cursor: pointer; }
.image-list button.active { border-color: var(--accent); background: var(--surface-hover); }
.image-list small { display: block; margin-top: 6px; color: var(--muted); overflow-wrap: anywhere; }
.tag-section { margin-top: 28px; }
.tag-list li > div:first-child { min-width: 0; }
.tag-list li { align-items: flex-start; }
.tag-list .actions { flex-shrink: 0; }
.tag-list strong { display: flex; align-items: center; min-height: 32px; }
@media (max-width: 900px) { .tag-list li { align-items: flex-start; flex-direction: column; } }
</style>
