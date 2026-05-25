import { createApp } from "vue";
import { createPinia } from "pinia";
import { VueQueryPlugin } from "@tanstack/vue-query";

import App from "./App.vue";
import { router } from "./router";
import { useAuthStore } from "./stores/auth";
import "@/styles/base.css";

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);
app.use(VueQueryPlugin, {
  queryClientConfig: {
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  },
});
app.use(router);

const auth = useAuthStore();
auth.restore();

app.mount("#app");
