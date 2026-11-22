<script setup lang="ts">
import { computed } from 'vue'

import type { Task, TaskMetrics } from '@/types/task'

const props = defineProps<{
  task: Task
  metrics: TaskMetrics | null
}>()

function metric(name: string): number | null {
  const value = props.metrics?.final_metrics?.[name]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function percent(value: number | null): string {
  return value == null ? '—' : `${(value * 100).toFixed(2)}%`
}

function count(value: number | null): string {
  return value == null ? '—' : value.toFixed(0)
}

function fixed(value: number | null, digits = 1): string {
  return value == null ? '—' : value.toFixed(digits)
}

const cards = computed(() => {
  if (props.task.kind === 'preprocess') {
    return [
      { label: '保留图片', value: count(metric('data/images')), hint: '进入训练的数据' },
      { label: '训练集', value: count(metric('data/train_images')), hint: '训练 split' },
      { label: '验证集', value: count(metric('data/val_images')), hint: '评估 split' },
      { label: '去重图片', value: count(metric('data/duplicates')), hint: 'SHA-256 命中' },
      { label: '无效标签', value: count(metric('data/invalid_labels')), hint: '已丢弃' },
      { label: '类别数', value: count(metric('data/classes')), hint: 'names' },
    ]
  }
  return [
    { label: 'mAP50', value: percent(metric('eval/map50')), hint: '整体' },
    { label: 'mAP50-95', value: percent(metric('eval/map50_95')), hint: '整体' },
    { label: '精确率', value: percent(metric('eval/precision')), hint: 'Box' },
    { label: '召回率', value: percent(metric('eval/recall')), hint: 'Box' },
    { label: '推理延迟', value: `${fixed(metric('eval/latency_ms'))} ms`, hint: '每张图' },
    { label: 'FPS', value: fixed(metric('eval/fps')), hint: 'CPU' },
  ]
})

const title = computed(() =>
  props.task.kind === 'preprocess' ? '预处理报告' : '评估报告',
)

const note = computed(() =>
  props.task.kind === 'preprocess'
    ? '报告来自去重、标签校验和可复现切分阶段；完整明细见 preprocess.json。'
    : '逐类 AP、阈值配置和评估参数见 evaluation.json；此处展示最终整体指标。',
)
</script>

<template>
  <div class="stage-metrics">
    <div class="stage-metrics-head">
      <strong>{{ title }}</strong>
      <span>阶段终值</span>
    </div>
    <div class="stage-metric-grid">
      <div v-for="card in cards" :key="card.label" class="stage-metric">
        <span>{{ card.label }}</span>
        <strong>{{ card.value }}</strong>
        <small>{{ card.hint }}</small>
      </div>
    </div>
    <p>{{ note }}</p>
  </div>
</template>

<style scoped>
.stage-metrics {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.stage-metrics-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.stage-metrics-head strong {
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
}

.stage-metrics-head span {
  color: var(--text-muted);
  font-size: 10px;
}

.stage-metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
}

.stage-metric {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--panel-soft);
}

.stage-metric span,
.stage-metric small {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-metric strong {
  overflow: hidden;
  color: var(--text);
  font-family: var(--mono);
  font-size: 16px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-metrics > p {
  margin: 0;
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1.6;
}
</style>
