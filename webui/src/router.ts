import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from "vue-router";

const routes: RouteRecordRaw[] = [
  {
    path: "/",
    component: () => import("@/shell/AppShell.vue"),
    children: [
      {
        path: "",
        name: "home",
        component: () => import("@/features/home/HomePage.vue"),
        meta: { label: "Overview", icon: "LayoutDashboard" },
      },
      {
        path: "config",
        name: "config",
        component: () => import("@/features/config/ConfigPage.vue"),
        meta: { label: "Config", icon: "Settings" },
      },
      {
        path: "cron",
        name: "cron",
        component: () => import("@/features/cron/CronPage.vue"),
        meta: { label: "CRON", icon: "Clock" },
      },
      {
        path: "sessions",
        name: "sessions",
        component: () => import("@/features/sessions/SessionsPage.vue"),
        meta: { label: "Sessions", icon: "MessageSquare" },
      },
      {
        path: "files",
        name: "files",
        component: () => import("@/features/files/FilesPage.vue"),
        meta: { label: "Files", icon: "FolderOpen" },
      },
      {
        path: "logs",
        name: "logs",
        component: () => import("@/features/logs/LogsPage.vue"),
        meta: { label: "Logs", icon: "ScrollText" },
      },
    ],
  },
];

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});
