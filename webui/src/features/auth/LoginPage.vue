<script setup lang="ts">
import { computed, ref, watchEffect } from "vue";
import { useRoute, useRouter } from "vue-router";
import { KeyRound, LogIn } from "lucide-vue-next";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import Input from "@/components/ui/Input.vue";
import Spinner from "@/components/ui/Spinner.vue";
import { api, toApiError } from "@/api/client";
import { useBootstrap } from "@/api/queries";
import type {
  AuthLoginResponse,
  AuthSessionResponse,
  StatusResponse,
} from "@/api/schemas";
import { useAuthStore } from "@/stores/auth";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const credential = ref("");
const error = ref("");
const verifying = ref(false);
const { data: bootstrap, isLoading: bootstrapLoading } = useBootstrap();

const redirectTo = computed(() => {
  const value = route.query.redirect;
  return typeof value === "string" && value.startsWith("/") ? value : "/";
});

watchEffect(() => {
  if (bootstrap.value && !bootstrap.value.auth.required) {
    router.replace(redirectTo.value);
  } else if (
    bootstrap.value?.auth.mode === "password" &&
    auth.sessionAuthenticated
  ) {
    router.replace(redirectTo.value);
  } else if (
    bootstrap.value?.auth.mode === "bearer" &&
    auth.authenticated
  ) {
    router.replace(redirectTo.value);
  }
});

async function submit() {
  const candidate = credential.value.trim();
  error.value = "";

  if (!candidate) {
    error.value =
      bootstrap.value?.auth.mode === "bearer"
        ? "Token is required."
        : "Password is required.";
    return;
  }

  verifying.value = true;
  try {
    if (bootstrap.value?.auth.mode === "bearer") {
      await api.getWithToken<StatusResponse>("/status", candidate);
      auth.set(candidate);
    } else {
      await api.post<AuthLoginResponse>("/auth/login", { password: candidate });
      const session = await api.get<AuthSessionResponse>("/auth/session");
      auth.setSessionAuthenticated(session.authenticated);
    }
    await router.replace(redirectTo.value);
  } catch (err) {
    const apiError = toApiError(err);
    error.value =
      apiError.status === 401
        ? "Invalid credentials."
        : `Verification failed: ${apiError.detail}`;
  } finally {
    verifying.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-mark">
        <KeyRound :size="22" />
      </div>
      <div class="login-heading">
        <h1 id="login-title">Nahida Bot</h1>
        <p>Operator access</p>
      </div>

      <Alert v-if="error" variant="destructive">{{ error }}</Alert>

      <form class="login-form" @submit.prevent="submit">
        <label class="field">
          <span>{{ bootstrap?.auth.mode === "bearer" ? "API token" : "Password" }}</span>
          <Input
            v-model="credential"
            type="password"
            autocomplete="current-password"
            :placeholder="bootstrap?.auth.mode === 'bearer' ? 'Bearer token' : 'Admin password'"
            :disabled="verifying || bootstrapLoading"
          />
        </label>

        <Button
          type="submit"
          class="login-submit"
          :disabled="verifying || bootstrapLoading"
        >
          <Spinner v-if="verifying || bootstrapLoading" size="sm" />
          <LogIn v-else :size="14" />
          Sign in
        </Button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--color-muted) 60%, transparent), transparent 45%),
    var(--color-background);
}

.login-panel {
  width: min(100%, 360px);
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.25rem;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-card);
}

.login-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-md);
  border: 1px solid color-mix(in srgb, var(--color-primary) 32%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 14%, var(--color-card));
  color: var(--color-primary);
  box-shadow: 0 0 12px color-mix(in srgb, var(--color-primary) 6%, transparent);
}

.login-heading {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.login-heading h1 {
  margin: 0;
  font-size: 1.125rem;
  line-height: 1.2;
}

.login-heading p {
  margin: 0;
  color: var(--color-muted-foreground);
  font-size: 0.8125rem;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-muted-foreground);
}

.login-submit {
  width: 100%;
}
</style>
