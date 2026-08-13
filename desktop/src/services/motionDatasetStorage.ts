import { invoke, isTauri } from "@tauri-apps/api/core";

import {
  motionDatasetKinds,
  type MotionDatasetExport,
  type MotionDatasetKind,
  type MotionDatasetRecords,
  type MotionDecisionRecord,
  type MotionExecutionRecord,
  type MotionInvalidRecord,
  type MotionPreferenceDatasetRecord,
  type MotionPreferenceRecord,
  type MotionPreferenceRetractionRecord,
} from "@/domain/motionTelemetry";
import type {
  MotionPreferenceStore,
  MotionTelemetry,
} from "@/domain/motionRuntime";

const browserStoragePrefix = "nahida.desktop.motion-dataset.v1";
const maximumBrowserRecords = 5000;

function browserStorageKey(kind: MotionDatasetKind): string {
  return `${browserStoragePrefix}.${kind}`;
}

function readBrowserRecords<K extends MotionDatasetKind>(
  kind: K,
): MotionDatasetRecords[K][] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(browserStorageKey(kind));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed as MotionDatasetRecords[K][] : [];
  } catch {
    return [];
  }
}

function appendBrowserRecord<K extends MotionDatasetKind>(
  kind: K,
  record: MotionDatasetRecords[K],
): void {
  if (typeof window === "undefined") return;
  const records = [...readBrowserRecords(kind), record].slice(
    -maximumBrowserRecords,
  );
  window.localStorage.setItem(browserStorageKey(kind), JSON.stringify(records));
}

export function recordsToJsonLines(records: readonly unknown[]): string {
  return records.map((record) => JSON.stringify(record)).join("\n");
}

export function parseJsonLines(source: string): unknown[] {
  return source
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as unknown);
}

export async function appendMotionDatasetRecord<K extends MotionDatasetKind>(
  kind: K,
  record: MotionDatasetRecords[K],
): Promise<void> {
  if (isTauri()) {
    await invoke("motion_dataset_append", { kind, record });
    return;
  }
  appendBrowserRecord(kind, record);
}

export async function readMotionDataset<K extends MotionDatasetKind>(
  kind: K,
): Promise<MotionDatasetRecords[K][]> {
  if (isTauri()) {
    return invoke<MotionDatasetRecords[K][]>("motion_dataset_read", { kind });
  }
  return readBrowserRecords(kind);
}

export async function exportMotionDataset(): Promise<MotionDatasetExport> {
  if (isTauri()) {
    return invoke<MotionDatasetExport>("motion_dataset_export");
  }
  return Object.fromEntries(
    motionDatasetKinds.map((kind) => [
      kind,
      recordsToJsonLines(readBrowserRecords(kind)),
    ]),
  ) as MotionDatasetExport;
}

export async function clearMotionDataset(
  kind?: MotionDatasetKind,
): Promise<void> {
  if (isTauri()) {
    await invoke("motion_dataset_clear", { kind: kind ?? null });
    return;
  }
  if (typeof window === "undefined") return;
  for (const target of kind ? [kind] : motionDatasetKinds) {
    window.localStorage.removeItem(browserStorageKey(target));
  }
}

export class LocalMotionTelemetry implements MotionTelemetry {
  private writeQueue: Promise<void> = Promise.resolve();

  recordDecision(record: MotionDecisionRecord): Promise<void> {
    return this.append("decisions", record);
  }

  recordExecution(record: MotionExecutionRecord): Promise<void> {
    return this.append("executions", record);
  }

  recordInvalid(record: MotionInvalidRecord): Promise<void> {
    return this.append("invalid", record);
  }

  private append<K extends MotionDatasetKind>(
    kind: K,
    record: MotionDatasetRecords[K],
  ): Promise<void> {
    this.writeQueue = this.writeQueue
      .catch(() => undefined)
      .then(() => appendMotionDatasetRecord(kind, record));
    return this.writeQueue;
  }
}

export class LocalMotionPreferenceStore implements MotionPreferenceStore {
  private writeQueue: Promise<void> = Promise.resolve();

  record(record: MotionPreferenceRecord): Promise<void> {
    return this.append(record);
  }

  retract(record: MotionPreferenceRetractionRecord): Promise<void> {
    return this.append(record);
  }

  private append(record: MotionPreferenceDatasetRecord): Promise<void> {
    this.writeQueue = this.writeQueue
      .catch(() => undefined)
      .then(() => appendMotionDatasetRecord("preferences", record));
    return this.writeQueue;
  }
}

export function activeMotionPreferences(
  records: readonly MotionPreferenceDatasetRecord[],
): MotionPreferenceRecord[] {
  const retractedIds = new Set(
    records.flatMap((record) =>
      record.type === "motion_preference_retraction"
        ? [record.retractsPreferenceId]
        : [],
    ),
  );
  return records.filter(
    (record): record is MotionPreferenceRecord =>
      record.type === "motion_preference" &&
      !retractedIds.has(record.preferenceId),
  );
}

export async function readActiveMotionPreferences(): Promise<
  MotionPreferenceRecord[]
> {
  return activeMotionPreferences(await readMotionDataset("preferences"));
}
