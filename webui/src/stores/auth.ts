import { defineStore } from "pinia";
import { ref, computed } from "vue";

export const useAuthStore = defineStore("auth", () => {
  const token = ref<string>("");
  const authenticated = computed(() => !!token.value);

  function set(newToken: string) {
    token.value = newToken;
    try {
      sessionStorage.setItem("nahida-bot:token", newToken);
    } catch {
      /* storage unavailable */
    }
  }

  function clear() {
    token.value = "";
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

  return { token, authenticated, set, clear, restore };
});
