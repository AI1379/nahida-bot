import { ref } from "vue";
import { defineStore } from "pinia";

export const useAppStore = defineStore("app", () => {
  const restartRequired = ref(false);
  const lastBackupPath = ref<string | null>(null);

  function setRestartRequired(backupPath?: string | null) {
    restartRequired.value = true;
    lastBackupPath.value = backupPath ?? null;
  }

  function dismissRestartRequired() {
    restartRequired.value = false;
  }

  return { restartRequired, lastBackupPath, setRestartRequired, dismissRestartRequired };
});
