import { defineStore } from "pinia";
import { ref, computed } from "vue";

export const useAuthStore = defineStore("auth", () => {
  const token = ref<string>("");
  const sessionAuthenticated = ref(false);
  const authenticated = computed(() => sessionAuthenticated.value || !!token.value);

  function set(newToken: string) {
    token.value = newToken;
    sessionAuthenticated.value = false;
    try {
      // Legacy bearer-token fallback for script/API-token deployments.
      sessionStorage.setItem("nahida-bot:token", newToken);
    } catch {
      /* storage unavailable */
    }
  }

  function setSessionAuthenticated(value: boolean) {
    sessionAuthenticated.value = value;
    if (value) {
      token.value = "";
      try {
        sessionStorage.removeItem("nahida-bot:token");
      } catch {
        /* storage unavailable */
      }
    }
  }

  function clear() {
    token.value = "";
    sessionAuthenticated.value = false;
    try {
      sessionStorage.removeItem("nahida-bot:token");
    } catch {
      /* storage unavailable */
    }
  }

  function restore() {
    try {
      const stored = sessionStorage.getItem("nahida-bot:token");
      if (stored) token.value = stored;
    } catch {
      /* storage unavailable */
    }
  }

  return {
    token,
    sessionAuthenticated,
    authenticated,
    set,
    setSessionAuthenticated,
    clear,
    restore,
  };
});
