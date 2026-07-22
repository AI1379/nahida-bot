import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "./App.vue";
import { useDesktopStore } from "./stores/desktop";
import "./styles/base.css";

const app = createApp(App);
const pinia = createPinia();
const desktopStore = useDesktopStore(pinia);
const surface: "main" | "pet" = isTauri()
  ? getCurrentWindow().label === "pet"
    ? "pet"
    : "main"
  : new URLSearchParams(window.location.search).get("window") === "pet"
    ? "pet"
    : "main";

document.documentElement.dataset.window = surface;
if (surface === "main") {
  await desktopStore.hydratePersistentState();
} else {
  // The pet receives its complete runtime state from the main window. It must
  // neither race the main window's migration nor hold gateway credentials.
  desktopStore.gatewayConnection = {
    ...desktopStore.gatewayConnection,
    nodeToken: "",
    adminBearerToken: "",
  };
}
app.use(pinia);
app.mount("#app");
