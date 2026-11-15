<script setup lang="ts">
import { computed } from 'vue'
import type { Catalog } from '../collaboration'
import type { RepositoryMember } from '../repositories'
import AppSelect from './AppSelect.vue'
const props = defineProps<{ catalog: Catalog[]; members: RepositoryMember[]; disabled?: boolean }>()
const assignees = defineModel<number[]>('assignees', { default: () => [] })
const selected = defineModel<number[]>('selected', { default: () => [] })
const labels = computed(() => props.catalog.filter((entry) => entry.kind === 'label'))
function chosen(kind: 'type' | 'milestone') {
  return String(
    props.catalog.find((entry) => entry.kind === kind && selected.value.includes(entry.id))?.id ||
      '',
  )
}
function choose(kind: 'type' | 'milestone', value: string) {
  selected.value = [
    ...selected.value.filter((id) => props.catalog.find((entry) => entry.id === id)?.kind !== kind),
    ...(value ? [Number(value)] : []),
  ]
}
</script>
<template>
  <div class="item-metadata-fields">
    <details>
      <summary>
        负责人 <span class="muted">{{ assignees.length || '未指定' }}</span>
      </summary>
      <div class="check-options">
        <label v-for="member in members.filter((m) => m.status === 'active')" :key="member.user_id"
          ><input
            v-model="assignees"
            type="checkbox"
            :value="member.user_id"
            :disabled="disabled"
          />{{ member.display_name }} <span class="muted">@{{ member.username }}</span></label
        >
      </div>
    </details>
    <details>
      <summary>
        标签
        <span class="muted">{{
          selected.filter((id) => labels.some((label) => label.id === id)).length
        }}</span>
      </summary>
      <div class="check-options">
        <label v-for="entry in labels" :key="entry.id"
          ><input v-model="selected" type="checkbox" :value="entry.id" :disabled="disabled" /><span
            class="label-dot"
            :style="{ background: entry.data.color }"
          />{{ entry.name }}</label
        >
        <p v-if="!labels.length" class="muted">可在 Settings 中添加标签。</p>
      </div>
    </details>
    <AppSelect
      v-for="kind in ['type', 'milestone'] as const"
      :key="kind"
      :model-value="chosen(kind)"
      :label="kind === 'type' ? '类型' : '里程碑'"
      :disabled="disabled"
      :options="[
        { value: '', label: '未指定' },
        ...catalog
          .filter((entry) => entry.kind === kind)
          .map((entry) => ({ value: String(entry.id), label: entry.name })),
      ]"
      @change="choose(kind, $event)"
    />
  </div>
</template>
