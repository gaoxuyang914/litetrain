<script setup lang="ts">
import { computed, ref } from 'vue'
import { DataLine, TrendCharts } from '@element-plus/icons-vue'

import MetricChart, { type ChartSeries } from '@/components/MetricChart.vue'
import type { Task, TaskMetrics } from '@/types/task'
import { formatPercent } from '@/utils/format'

const props = defineProps<{
  task: Task
  metrics: TaskMetrics | null
  loading?: boolean
}>()

type ChartTab = 'loss' | 'precision' | 'resource'

const activeTab = ref<ChartTab>('loss')

const tabs: Array<{ key: ChartTab; label: string; hint: string }> = [
  { key: 'loss', label: '训练损失', hint: '框 / 分类 / DFL' },
  { key: 'precision', label: '验证指标', hint: 'mAP / 精确率 / 召回率' },
  { key: 'resource', label: '资源采样', hint: 'CPU / 心跳' },
]

function seriesByName(fragment: string) {
  return props.metrics?.series.find((item) => item.name === fragment)
}

function valueOf(fragment: string): number | null {
  const value = props.metrics?.final_metrics?.[fragment]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const summary = computed(() => [
  {
    label: 'mAP50',
    value: valueOf('metrics/mAP50(B)'),
    display: formatMetric(valueOf('metrics/mAP50(B)')),
    tone: 'accent',
  },
  {
    label: 'mAP50-95',
    value: valueOf('metrics/mAP50-95(B)'),
    display: formatMetric(valueOf('metrics/mAP50-95(B)')),
    tone: 'copper',
  },
  {
    label: '精确率',
    value: valueOf('metrics/precision(B)'),
    display: formatMetric(valueOf('metrics/precision(B)')),
    tone: 'amber',
  },
  {
    label: '召回率',
    value: valueOf('metrics/recall(B)'),
    display: formatMetric(valueOf('metrics/recall(B)')),
    tone: 'blue',
  },
])

function formatMetric(value: number | null): string {
  if (value == null) return '—'
  return `${(value * 100).toFixed(2)}%`
}

const lossSeries = computed<ChartSeries[]>(() => {
  const metricSeries = props.metrics?.series ?? []
  const preferred = [
    ['train/box_loss', '#0b7a53', true],
    ['train/cls_loss', '#a5542f', true],
    ['train/dfl_loss', '#9a6a1e', false],
    ['val/box_loss', '#4f6f5e', false],
  ] as const
  const values: Array<ChartSeries | null> = preferred.map(([name, color, area]) => {
    const series = metricSeries.find((item) => item.name === name)
    if (!series) return null
    return {
      name: name.replace('train/', '').replace('val/', 'val '),
      color,
      area,
      points: series.points.map((point) => ({ x: `E${point.epoch}`, y: point.value })),
    }
  })
  return values.filter((item): item is ChartSeries => item !== null)
})

const precisionSeries = computed<ChartSeries[]>(() => {
  const metricSeries = props.metrics?.series ?? []
  const preferred = [
    ['metrics/mAP50(B)', 'mAP50', '#0b7a53', true],
    ['metrics/mAP50-95(B)', 'mAP50-95', '#a5542f', true],
    ['metrics/precision(B)', 'precision', '#9a6a1e', false],
    ['metrics/recall(B)', 'recall', '#4f6f5e', false],
  ] as const
  const values: Array<ChartSeries | null> = preferred.map(([key, label, color, area]) => {
    const series = metricSeries.find((item) => item.name === key)
    if (!series) return null
    return {
      name: label,
      color,
      area,
      points: series.points.map((point) => ({ x: `E${point.epoch}`, y: point.value })),
    }
  })
  return values.filter((item): item is ChartSeries => item !== null)
})

const resourceSeries = computed<ChartSeries[]>(() => {
  const resources = (props.metrics?.resources ?? []).slice(-160)
  if (!resources.length) return []
  const cpuPoints = resources.map((sample, index) => ({
    x: new Date(sample.sampled_at).toLocaleTimeString('zh-CN', { hour12: false }),
    y: sample.cpu_percent,
  }))
  return [
    {
      name: 'CPU',
      color: '#0b7a53',
      area: true,
      points: cpuPoints,
    },
  ]
})

const peakMemory = computed(() => {
  const samples = props.metrics?.resources ?? []
  if (!samples.length) return null
  return Math.max(...samples.map((sample) => sample.memory_mb))
})

const currentSeries = computed(() => {
  if (activeTab.value === 'loss') return lossSeries.value
  if (activeTab.value === 'precision') return precisionSeries.value
  return resourceSeries.value
})

const latestEpoch = computed(() => {
  const series = lossSeries.value[0] ?? precisionSeries.value[0]
  if (!series || !series.points.length) return null
  return series.points[series.points.length - 1]?.x
})

const hasMetricData = computed(() => (props.metrics?.series.length ?? 0) > 0)
</script>

<template>
  <div class="metrics-panel">
    <div class="summary-grid">
      <div
        v-for="item in summary"
        :key="item.label"
        class="summary-card"
        :class="item.tone"
      >
        <span>{{ item.label }}</span>
        <strong>{{ item.display }}</strong>
        <small>最终</small>
      </div>
    </div>

    <div class="chart-card">
      <div class="chart-head">
        <div>
          <span class="soft-label"><el-icon><TrendCharts /></el-icon> 训练遥测</span>
          <div class="chart-tabs">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              type="button"
              :class="{ active: activeTab === tab.key }"
              @click="activeTab = tab.key"
            >
              {{ tab.label }}
              <small>{{ tab.hint }}</small>
            </button>
          </div>
        </div>
        <span v-if="latestEpoch" class="epoch-badge mono">{{ latestEpoch }}</span>
      </div>

      <MetricChart
        :series="currentSeries"
        :height="activeTab === 'resource' ? 220 : 252"
        :value-suffix="activeTab === 'precision' ? '' : ''"
        :empty-text="
          task.status === 'running'
            ? '第一个 epoch 完成后开始绘制指标…'
            : '本任务尚未上报训练指标'
        "
      />

      <div v-if="activeTab === 'resource'" class="resource-legend">
        <span><i class="cpu"></i>CPU 百分比来自训练进程心跳</span>
        <span v-if="peakMemory">峰值内存 {{ peakMemory.toFixed(1) }} MB</span>
        <span>{{ metrics?.resources.length ?? 0 }} 个采样</span>
      </div>
      <div v-else class="chart-foot">
        <span>
          {{ hasMetricData ? '数据来源：Ultralytics on_fit_epoch_end 回调' : '尚未收到 epoch 指标' }}
        </span>
        <span class="mono">{{ metrics?.series.length ?? 0 }} 条曲线</span>
      </div>
    </div>

    <div v-if="task.status === 'running' && !loading" class="live-hint">
      <span class="live-dot"></span>
      <span>指标每完成一轮上报一次，资源采样随心跳更新</span>
      <span class="mono">实时</span>
    </div>
  </div>
</template>

<style scoped>
.metrics-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.summary-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 9px 10px 8px;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--panel-soft);
}

.summary-card span {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7.5px;
  letter-spacing: 0.03em;
}

.summary-card strong {
  margin-top: 2px;
  color: var(--text);
  font-family: var(--mono);
  font-size: 14px;
  font-weight: 650;
  letter-spacing: -0.04em;
}

.summary-card small {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 6.5px;
  letter-spacing: 0.08em;
}

.summary-card.accent {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.summary-card.accent strong {
  color: var(--accent);
}

.summary-card.violet strong,
.summary-card.copper strong {
  color: var(--violet);
}

.summary-card.amber strong {
  color: var(--amber);
}

.summary-card.blue strong {
  color: var(--blue);
}

.chart-card {
  padding: 12px 12px 8px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--panel);
}

.chart-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 5px;
}

.chart-head .soft-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 8px;
}

.chart-head .el-icon {
  color: var(--accent);
  font-size: 11px;
}

.chart-tabs {
  display: flex;
  gap: 3px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.chart-tabs button {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 7px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 9px;
  font-weight: 550;
  transition: all 0.15s ease;
}

.chart-tabs button small {
  color: var(--text-muted);
  font-family: var(--mono);
  font-size: 7px;
  opacity: 0.6;
}

.chart-tabs button:hover {
  color: var(--text-secondary);
  background: var(--panel-soft);
}

.chart-tabs button.active {
  border-color: var(--border);
  background: var(--panel-soft);
  color: var(--text);
}

.chart-tabs button.active small {
  color: var(--accent);
  opacity: 1;
}

.epoch-badge {
  flex: none;
  padding: 3px 6px;
  border: 1px solid var(--accent-line);
  border-radius: 5px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 8px;
  font-weight: 600;
}

.chart-foot,
.resource-legend {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  padding-top: 4px;
  color: var(--text-muted);
  font-size: 7.5px;
}

.resource-legend {
  justify-content: flex-start;
  gap: 12px;
}

.resource-legend span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.resource-legend i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
}

.live-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 9px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 8px;
}

.live-dot {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent);
  animation: live-blink 1.8s ease-in-out infinite;
}

.live-hint .mono {
  margin-left: auto;
  color: var(--accent);
  font-size: 7.5px;
}

@keyframes live-blink {
  0%,
  100% {
    opacity: 0.35;
  }
  50% {
    opacity: 1;
  }
}

@media (max-width: 740px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>

<style scoped>
.summary-grid {
  gap: 8px;
}

.summary-card {
  padding: 10px 12px;
  border-radius: 6px;
}

.summary-card span {
  color: var(--text-muted);
  font-family: var(--font);
  font-size: 12px;
  letter-spacing: 0;
}

.summary-card strong {
  margin-top: 3px;
  font-family: var(--mono);
  font-size: 20px;
  font-weight: 600;
}

.summary-card small {
  font-family: var(--font);
  font-size: 12px;
  letter-spacing: 0;
}

.chart-card {
  padding: 14px 14px 10px;
  border-radius: var(--radius);
}

.chart-head .soft-label {
  font-size: 12px;
}

.chart-head .el-icon {
  font-size: 14px;
}

.chart-tabs {
  gap: 4px;
  margin-top: 9px;
}

.chart-tabs button {
  gap: 5px;
  padding: 5px 8px;
  border-radius: 4px;
  font-size: 13px;
}

.chart-tabs button small {
  font-family: var(--font);
  font-size: 12px;
}

.epoch-badge {
  padding: 3px 7px;
  border-radius: 4px;
  font-size: 11px;
}

.chart-foot,
.resource-legend {
  padding-top: 7px;
  font-size: 12px;
}

.live-hint {
  padding: 9px 11px;
  border-radius: 6px;
  font-size: 13px;
}

.live-dot {
  width: 5px;
  height: 5px;
}

@media (max-width: 740px) {
  .chart-tabs button {
    font-size: 12px;
  }

  .chart-tabs button small {
    display: none;
  }
}
</style>
