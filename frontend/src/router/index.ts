import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: '/',
      component: () => import('@/layout/AppLayout.vue'),
      children: [
        {
          path: '',
          redirect: '/overview',
        },
        {
          path: 'overview',
          name: 'overview',
          component: () => import('@/views/OverviewView.vue'),
          meta: { title: '集群总览', eyebrow: '集群' },
        },
        {
          path: 'tasks',
          name: 'tasks',
          component: () => import('@/views/TaskListView.vue'),
          meta: { title: '任务列表', eyebrow: '任务' },
        },
        {
          path: 'tasks/new',
          name: 'task-submit',
          component: () => import('@/views/TaskSubmitView.vue'),
          meta: { title: '提交训练任务', eyebrow: '提交' },
        },
        {
          path: 'tasks/:id',
          name: 'task-detail',
          component: () => import('@/views/TaskDetailView.vue'),
          meta: { title: '任务详情', eyebrow: '任务' },
        },
        {
          path: 'pipelines',
          name: 'pipelines',
          component: () => import('@/views/PipelinesView.vue'),
          meta: { title: '训练流水线', eyebrow: '编排' },
        },
        {
          path: 'assets',
          name: 'assets',
          component: () => import('@/views/AssetsView.vue'),
          meta: { title: '数据与产物', eyebrow: '资产' },
        },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/overview',
    },
  ],
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : '轻量训练平台'
  document.title = `${title} · LiteTrain`
})

export default router
