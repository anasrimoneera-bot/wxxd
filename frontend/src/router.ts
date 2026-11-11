import { createRouter, createWebHistory } from 'vue-router'
import Layout from './components/Layout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./views/Login.vue'), meta: { public: true } },
    {
      path: '/',
      component: Layout,
      children: [
        { path: '', component: () => import('./views/Home.vue'), meta: { title: '首页' } },
        { path: 'stores', component: () => import('./views/Stores.vue'), meta: { title: '店铺管理' } },
        { path: 'products', component: () => import('./views/Products.vue'), meta: { title: '商品管理' } },
        { path: 'sourcing', component: () => import('./views/Sourcing.vue'), meta: { title: '找货源' } },
        { path: 'library', component: () => import('./views/Library.vue'), meta: { title: '商品云库' } },
        { path: 'publish', component: () => import('./views/PublishConfig.vue'), meta: { title: '发布配置' } },
        { path: 'tasks', component: () => import('./views/Tasks.vue'), meta: { title: '任务列表' } },
        { path: 'categories', component: () => import('./views/CategoryApply.vue'), meta: { title: '一键申请类目' } },
        { path: 'customers', component: () => import('./views/Customers.vue'), meta: { title: '用户管理' } },
        { path: 'settings', component: () => import('./views/Settings.vue'), meta: { title: '系统设置' } },
        { path: 'subaccounts', component: () => import('./views/SubAccounts.vue'), meta: { title: '子账号管理' } },
      ],
    },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !localStorage.getItem('token')) return '/login'
})

export default router
