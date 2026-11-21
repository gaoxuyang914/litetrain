<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

import { useTheme } from '@/composables/useTheme'

export interface ChartPoint {
  x: string | number
  y: number
}

export interface ChartSeries {
  name: string
  points: ChartPoint[]
  color?: string
  area?: boolean
}

const props = withDefaults(
  defineProps<{
    series: ChartSeries[]
    height?: number
    emptyText?: string
    valueSuffix?: string
    maxPoints?: number
  }>(),
  {
    height: 260,
    emptyText: '等待训练指标上报…',
    valueSuffix: '',
    maxPoints: 0,
  },
)

const { isDark } = useTheme()
const chartEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

const palette = ['#0d9b6b', '#6b5ce7', '#d88a08', '#d24b4b', '#4f8dd6', '#c2589a']

const hasData = computed(() => props.series.some((item) => item.points.length > 0))

function trimPoints(points: ChartPoint[]): ChartPoint[] {
  if (!props.maxPoints || points.length <= props.maxPoints) return points
  return points.slice(-props.maxPoints)
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function buildOption(): echarts.EChartsOption {
  const textColor = cssVar('--text-secondary', '#54685f')
  const mutedColor = cssVar('--text-muted', '#87978f')
  const borderColor = cssVar('--border', '#dfe8e2')
  const series = props.series.map((item, index) => {
    const data = trimPoints(item.points)
    const color = item.color ?? palette[index % palette.length]
    return {
      name: item.name,
      type: 'line',
      smooth: 0.25,
      showSymbol: data.length < 24,
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 2, color },
      itemStyle: { color },
      areaStyle: item.area
        ? {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: `${color}30` },
              { offset: 1, color: `${color}00` },
            ]),
          }
        : undefined,
      emphasis: { focus: 'series' },
      data: data.map((point) => [String(point.x), point.y]),
    }
  })

  return {
    animationDuration: 260,
    color: palette,
    backgroundColor: 'transparent',
    grid: { top: 34, left: 45, right: 16, bottom: 30, containLabel: false },
    tooltip: {
      trigger: 'axis',
      backgroundColor: cssVar('--panel-raised', '#ffffff'),
      borderColor,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: textColor, fontSize: 10, fontFamily: 'Cascadia Code, monospace' },
      axisPointer: { type: 'line', lineStyle: { color: accentLine(), width: 1 } },
    },
    legend: {
      top: 2,
      left: 0,
      right: 0,
      icon: 'roundRect',
      itemWidth: 9,
      itemHeight: 3,
      itemGap: 12,
      textStyle: { color: mutedColor, fontSize: 9, fontFamily: 'Segoe UI, sans-serif' },
      type: 'scroll',
      pageIconColor: textColor,
      pageTextStyle: { color: mutedColor },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      axisLine: { lineStyle: { color: borderColor } },
      axisTick: { show: false },
      axisLabel: {
        color: mutedColor,
        fontSize: 8.5,
        fontFamily: 'Cascadia Code, monospace',
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: mutedColor,
        fontSize: 8.5,
        fontFamily: 'Cascadia Code, monospace',
      },
      splitLine: { lineStyle: { color: borderColor, type: 'dashed', opacity: 0.65 } },
    },
    series,
  } as echarts.EChartsOption
}

function accentLine(): string {
  return cssVar('--accent-line', 'rgba(13,155,107,.22)')
}

function render() {
  if (!chartEl.value) return
  if (!chart) {
    chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
  }
  if (!hasData.value) {
    chart.clear()
    return
  }
  chart.setOption(buildOption(), true)
}

watch(
  () => props.series,
  () => nextTick(render),
  { deep: true },
)

watch(isDark, () => nextTick(render))

onMounted(() => {
  render()
  if (chartEl.value) {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(chartEl.value)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="metric-chart" :style="{ height: `${height}px` }">
    <div ref="chartEl" class="chart-canvas"></div>
    <div v-if="!hasData" class="chart-empty">
      <span class="chart-empty-dot"></span>
      <span>{{ emptyText }}</span>
    </div>
  </div>
</template>

<style scoped>
.metric-chart {
  position: relative;
  width: 100%;
  min-height: 180px;
}

.chart-canvas {
  width: 100%;
  height: 100%;
}

.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  color: var(--text-muted);
  font-size: 10px;
}

.chart-empty-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.55;
  animation: breathe 1.8s ease-in-out infinite;
}

@keyframes breathe {
  0%,
  100% {
    opacity: 0.3;
  }
  50% {
    opacity: 0.9;
  }
}
</style>
