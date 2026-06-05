import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from "vue-router";
import { api } from "@/api/client";
import type { AuthSessionResponse, BootstrapResponse } from "@/api/schemas";
import { useAuthStore } from "@/stores/auth";

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "login",
    component: () => import("@/features/auth/LoginPage.vue"),
    meta: { public: true },
  },
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
      {
        path: "plugins",
        name: "plugins",
        component: () => import("@/features/plugins/PluginsPage.vue"),
        meta: { label: "Plugins", icon: "Plug" },
      },
      {
        path: "skills",
        name: "skills",
        component: () => import("@/features/skills/SkillsPage.vue"),
        meta: { label: "Skills", icon: "Brain" },
      },
      {
        path: "usage",
        name: "usage",
        component: () => import("@/features/usage/UsagePage.vue"),
        meta: { label: "Usage", icon: "Coins" },
      },
      {
        path: "about",
        name: "about",
        component: () => import("@/features/about/AboutPage.vue"),
        meta: { label: "About", icon: "Info" },
      },
    ],
  },
];

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});

let bootstrapPromise: Promise<BootstrapResponse> | null = null;

function getBootstrap() {
  bootstrapPromise ??= api
    .get<BootstrapResponse>("/webui/bootstrap")
    .catch((err) => {
      bootstrapPromise = null;
      throw err;
    });
  return bootstrapPromise;
}

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  const isPublic = to.matched.some((record) => record.meta.public);
  const bootstrap = await getBootstrap();

  if (!bootstrap.auth.required) {
    if (to.name === "login") return { path: "/" };
    return true;
  }

  if (bootstrap.auth.mode === "password") {
    const session = await api.get<AuthSessionResponse>("/auth/session");
    if (session.authenticated) {
      auth.setSessionAuthenticated(true);
    } else {
      auth.clear();
    }
    if (session.authenticated) {
      if (to.name === "login") return { path: "/" };
      return true;
    }
    if (isPublic) return true;
    return {
      path: "/login",
      query: { redirect: to.fullPath },
    };
  }

  if (auth.authenticated) {
    if (to.name === "login") return { path: "/" };
    return true;
  }

  if (isPublic) return true;

  return {
    path: "/login",
    query: { redirect: to.fullPath },
  };
});
