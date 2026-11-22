<script setup lang="ts">
import { computed } from 'vue'

import type { TaskStatus } from '@/types/task'
import { statusMeta } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    status: TaskStatus
    compact?: boolean
    label?: string
  }>(),
  { compact: false },
)

const meta = computed(() => statusMeta[props.status])
</script>

<template>
  <span class="status-badge" :class="[meta.tone, { compact }]">
    <span class="status-dot" :class="{ live: status === 'running' }"></span>
    {{ label ?? (compact ? meta.short : meta.label) }}
  </span>
</template>

<style scoped>
.status-badge {
  --badge-color: var(--text-secondary);
  --badge-bg: var(--panel-soft);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  padding: 4px 9px;
  border: 1px solid color-mix(in srgb, var(--badge-color) 22%, transparent);
  border-radius: 999px;
  background: var(--badge-bg);
  color: var(--badge-color);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0;
  line-height: 1.4;
  white-space: nowrap;
}

.status-badge.compact {
  padding: 3px 7px;
  font-size: 11px;
}

.status-dot {
  width: 5px;
  height: 5px;
  flex: none;
  border-radius: 50%;
  background: currentColor;
}

.status-dot.live {
  animation: none;
}

.status-badge.pending {
  --badge-color: var(--amber);
  --badge-bg: var(--amber-soft);
}

.status-badge.running {
  --badge-color: var(--accent);
  --badge-bg: var(--accent-soft);
}

.status-badge.succeeded {
  --badge-color: var(--success);
  --badge-bg: var(--success-soft);
}

.status-badge.failed {
  --badge-color: var(--red);
  --badge-bg: var(--red-soft);
}

.status-badge.cancelled {
  --badge-color: var(--text-muted);
  --badge-bg: var(--panel-soft);
}

html.dark .status-badge.succeeded {
  --badge-color: var(--success);
  --badge-bg: var(--success-soft);
}

@keyframes soft-pulse {
  0% {
    box-shadow: 0 0 0 0 currentColor;
  }
  70% {
    box-shadow: 0 0 0 4px transparent;
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
  }
}
</style>
