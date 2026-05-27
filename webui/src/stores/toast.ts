import { ref } from "vue";
import { defineStore } from "pinia";

export interface Toast {
  id: number;
  message: string;
  variant: "default" | "success" | "error" | "warning";
}

let nextId = 0;

export const useToastStore = defineStore("toast", () => {
  const toasts = ref<Toast[]>([]);

  function add(
    message: string,
    variant: Toast["variant"] = "default",
    duration = 4000,
  ) {
    const id = nextId++;
    toasts.value.push({ id, message, variant });
    if (duration > 0) {
      setTimeout(() => remove(id), duration);
    }
  }

  function remove(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id);
  }

  return { toasts, add, remove };
});
