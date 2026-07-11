<script setup lang="ts">
import { onMounted, ref } from "vue";
import { Link, RefreshCw, Unlink, UserPlus } from "lucide-vue-next";
import { api } from "@/api/client";
import Alert from "@/components/ui/Alert.vue";
import Button from "@/components/ui/Button.vue";
import Card from "@/components/ui/Card.vue";
import Input from "@/components/ui/Input.vue";

interface AccountRow {
  account_key: string;
  label: string;
  verification: string;
  linked_by: string;
}

interface PersonRow {
  person_id: string;
  display_name: string;
  status: string;
  accounts: AccountRow[];
}

interface ObservationRow {
  account_key: string;
  chat_address: string;
  display_name: string;
}

interface AuditRow {
  audit_id: number;
  action: string;
  actor: string;
  target_id: string;
}

interface IdentityResponse {
  people: PersonRow[];
  observations: ObservationRow[];
  audit: AuditRow[];
}

const data = ref<IdentityResponse>({ people: [], observations: [], audit: [] });
const loading = ref(false);
const error = ref("");
const personId = ref("");
const displayName = ref("");
const accountKey = ref("");
const targetPersonId = ref("");

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    data.value = await api.get<IdentityResponse>("/identity");
  } catch (err) {
    error.value = String(err);
  } finally {
    loading.value = false;
  }
}

async function savePerson() {
  if (!personId.value.trim()) return;
  await api.post("/identity/persons", {
    person_id: personId.value.trim(),
    display_name: displayName.value.trim(),
  });
  personId.value = "";
  displayName.value = "";
  await refresh();
}

async function linkAccount() {
  if (!accountKey.value.trim() || !targetPersonId.value.trim()) return;
  await api.post("/identity/accounts/link", {
    account_key: accountKey.value.trim(),
    person_id: targetPersonId.value.trim(),
  });
  accountKey.value = "";
  await refresh();
}

async function unlinkAccount(key: string) {
  await api.post("/identity/accounts/unlink", { account_key: key });
  await refresh();
}

onMounted(refresh);
</script>

<template>
  <div class="identity-page">
    <div class="page-header">
      <div>
        <h1>Identity</h1>
        <p>Manage people, platform accounts, observations, and audited links.</p>
      </div>
      <Button variant="outline" :disabled="loading" @click="refresh">
        <RefreshCw :size="15" /> Refresh
      </Button>
    </div>

    <Alert v-if="error" variant="destructive">{{ error }}</Alert>

    <div class="forms">
      <Card>
        <h2><UserPlus :size="17" /> Create or update person</h2>
        <Input v-model="personId" placeholder="person_id, e.g. owner" />
        <Input v-model="displayName" placeholder="Display name" />
        <Button :disabled="!personId.trim()" @click="savePerson">Save person</Button>
      </Card>
      <Card>
        <h2><Link :size="17" /> Link account</h2>
        <Input v-model="accountKey" placeholder="desktop:user:owner" />
        <Input v-model="targetPersonId" placeholder="Target person_id" />
        <Button
          :disabled="!accountKey.trim() || !targetPersonId.trim()"
          @click="linkAccount"
        >Link account</Button>
      </Card>
    </div>

    <section>
      <h2>People</h2>
      <div v-if="!data.people.length" class="empty">No people configured.</div>
      <div class="people-grid">
        <Card v-for="person in data.people" :key="person.person_id">
          <div class="person-title">
            <strong>{{ person.display_name || person.person_id }}</strong>
            <code>{{ person.person_id }}</code>
          </div>
          <div v-if="!person.accounts.length" class="muted">No linked accounts.</div>
          <div v-for="account in person.accounts" :key="account.account_key" class="account">
            <div>
              <code>{{ account.account_key }}</code>
              <small>{{ account.verification }} · {{ account.linked_by || "seed" }}</small>
            </div>
            <Button variant="ghost" size="icon" @click="unlinkAccount(account.account_key)">
              <Unlink :size="14" />
            </Button>
          </div>
        </Card>
      </div>
    </section>

    <div class="lower-grid">
      <section>
        <h2>Recent observations</h2>
        <Card>
          <div v-for="item in data.observations" :key="`${item.account_key}:${item.chat_address}`" class="row">
            <code>{{ item.account_key }}</code>
            <span>{{ item.display_name || "—" }} in {{ item.chat_address }}</span>
          </div>
          <div v-if="!data.observations.length" class="empty">No observations yet.</div>
        </Card>
      </section>
      <section>
        <h2>Audit log</h2>
        <Card>
          <div v-for="item in data.audit" :key="item.audit_id" class="row">
            <strong>{{ item.action }}</strong>
            <span>{{ item.target_id }} · {{ item.actor }}</span>
          </div>
          <div v-if="!data.audit.length" class="empty">No identity mutations yet.</div>
        </Card>
      </section>
    </div>
  </div>
</template>

<style scoped>
.identity-page { padding: 1.5rem; display: grid; gap: 1.25rem; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-header h1 { margin: 0; font-size: 1.5rem; }
.page-header p, .muted, .empty, small { color: var(--color-muted-foreground); }
.forms, .lower-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
.forms .card { display: grid; gap: 0.75rem; }
h2 { display: flex; align-items: center; gap: 0.4rem; margin: 0 0 0.75rem; font-size: 1rem; }
.people-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 0.75rem; }
.person-title { display: flex; justify-content: space-between; margin-bottom: 0.75rem; }
.account, .row { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; padding: 0.55rem 0; border-top: 1px solid var(--color-border); }
.account > div { display: grid; gap: 0.2rem; min-width: 0; }
code { overflow-wrap: anywhere; color: var(--color-primary); }
.row span { color: var(--color-muted-foreground); text-align: right; }
@media (max-width: 800px) { .forms, .lower-grid { grid-template-columns: 1fr; } }
</style>
