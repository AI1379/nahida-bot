import { shallowRef } from "vue";

import type {
  PluginRuntimeSnapshot,
  RemotePluginRuntime,
} from "@/domain/pluginRuntime";
import type { CapabilityExecutionResult } from "@/domain/runtime";
import type {
  ActiveRemotePluginPage,
  ActiveDesktopPluginSettingsPanel,
  DesktopPluginContext,
  DesktopPluginDefinition,
  DesktopPluginFacetManifest,
  DesktopPluginHandler,
  DesktopPluginHostAdapter,
  DesktopPluginRecord,
  DesktopPluginReconcileResult,
  DesktopPluginRuntime,
  DesktopPluginSettingsPlacement,
  DesktopPluginSettingsSection,
  DesktopPluginSyncIssue,
} from "./desktopPluginContract";
import { DesktopPluginSurfaceManager } from "./desktopPluginSurfaces";
import {
  errorMessage,
  pluginError,
  validateHandlerSet,
} from "./desktopPluginValidation";

export type {
  ActiveDesktopPluginSettingsPanel,
  ActiveRemotePluginPage,
  DesktopPluginContext,
  DesktopPluginDefinition,
  DesktopPluginFacetManifest,
  DesktopPluginHandler,
  DesktopPluginHostAdapter,
  DesktopPluginRecord,
  DesktopPluginReconcileResult,
  DesktopPluginRuntime,
  DesktopPluginSettingsPanelContribution,
  DesktopPluginSettingsPlacement,
  DesktopPluginSettingsSection,
  DesktopPluginSurfaceDeclaration,
  DesktopPluginSyncIssue,
} from "./desktopPluginContract";

/**
 * Runtime adapter for Desktop facets. Installation and enablement remain owned
 * by the shared plugin manager; this host only activates code already bundled
 * for the Desktop technology stack.
 */
export class DesktopPluginHost {
  private readonly records = new Map<string, DesktopPluginRecord>();
  private readonly definitions = new Map<string, DesktopPluginDefinition>();
  private readonly remotePlugins = new Map<string, RemotePluginRuntime>();
  private readonly capabilityOwners = new Map<string, DesktopPluginRecord>();
  private readonly surfaces: DesktopPluginSurfaceManager;
  private readonly revision = shallowRef(0);
  private runtimeGeneration = "";
  private runtimeRevision = 0;
  private syncIssues: DesktopPluginSyncIssue[] = [];

  constructor(adapter: DesktopPluginHostAdapter) {
    this.surfaces = new DesktopPluginSurfaceManager(adapter);
  }

  activateAll(definitions: DesktopPluginDefinition[]): DesktopPluginRecord[] {
    for (const definition of definitions) {
      this.definitions.set(definition.manifest.id, definition);
    }
    return definitions.map((definition) => this.activate(definition));
  }

  reconcile(snapshot: PluginRuntimeSnapshot): DesktopPluginReconcileResult {
    if (
      snapshot.generation === this.runtimeGeneration &&
      snapshot.revision <= this.runtimeRevision
    ) {
      return this.reconcileResult(false, [], []);
    }
    this.runtimeGeneration = snapshot.generation;
    this.runtimeRevision = snapshot.revision;
    this.remotePlugins.clear();
    for (const plugin of snapshot.plugins) this.remotePlugins.set(plugin.id, plugin);

    const activated: string[] = [];
    const deactivated: string[] = [];
    const issues: DesktopPluginSyncIssue[] = [];
    for (const definition of this.definitions.values()) {
      const remote = this.remotePlugins.get(definition.manifest.id);
      const issue = remote
        ? compatibilityIssue(definition.manifest, remote)
        : null;
      if (!remote || remote.state !== "enabled" || !remote.desktop || issue) {
        if (this.deactivate(definition.manifest.id)) {
          deactivated.push(definition.manifest.id);
        }
        if (remote?.state === "enabled" && issue) issues.push(issue);
        continue;
      }
      if (this.records.get(definition.manifest.id)?.status !== "active") {
        const record = this.activate(definition);
        if (record.status === "active") activated.push(definition.manifest.id);
        else {
          issues.push({
            pluginId: definition.manifest.id,
            code: "artifact_missing",
            message: record.error,
          });
        }
      }
    }
    for (const remote of this.remotePlugins.values()) {
      if (
        remote.state !== "enabled" ||
        !remote.desktop ||
        this.definitions.has(remote.id)
      ) {
        continue;
      }
      issues.push(missingArtifactIssue(remote));
    }
    this.syncIssues = issues;
    this.touch();
    return this.reconcileResult(true, activated, deactivated);
  }

  activate(definition: DesktopPluginDefinition): DesktopPluginRecord {
    const { manifest } = definition;
    const conflict = this.activationConflict(manifest);
    if (conflict) return conflict;
    const context = this.surfaces.createContext(manifest);
    return this.activateRuntime(definition, context);
  }

  private activationConflict(
    manifest: DesktopPluginFacetManifest,
  ): DesktopPluginRecord | null {
    const existing = this.records.get(manifest.id);
    if (existing?.status === "active") {
      return {
        manifest,
        status: "error",
        runtime: null,
        error: `Plugin ${manifest.id} is already active`,
      };
    }
    if (existing) this.records.delete(manifest.id);

    const duplicateCapability = manifest.contributes.capabilities.find((name) =>
      this.capabilityOwners.has(name),
    );
    if (duplicateCapability) {
      return this.failedRecord(
        manifest,
        `Capability ${duplicateCapability} already has an active owner`,
      );
    }
    return null;
  }

  private activateRuntime(
    definition: DesktopPluginDefinition,
    context: DesktopPluginContext,
  ): DesktopPluginRecord {
    const { manifest } = definition;
    let runtime: DesktopPluginRuntime | null = null;
    try {
      runtime = definition.activate(context);
      this.validateHandlers(manifest, runtime);
      const record: DesktopPluginRecord = {
        manifest,
        status: "active",
        runtime,
        error: "",
      };
      this.records.set(manifest.id, record);
      for (const capability of manifest.contributes.capabilities) {
        this.capabilityOwners.set(capability, record);
      }
      this.touch();
      return record;
    } catch (error) {
      try {
        runtime?.dispose();
      } catch {
        // Activation failure remains the primary diagnostic.
      }
      this.surfaces.clear(manifest.id);
      return this.failedRecord(manifest, errorMessage(error));
    }
  }

  executeCapability(
    capability: string,
    args: Record<string, unknown>,
  ): CapabilityExecutionResult | null {
    const record = this.capabilityOwners.get(capability);
    if (!record?.runtime) return null;
    const handler = record.runtime.capabilities?.[capability];
    if (!handler) return null;
    return this.executeHandler(record.manifest.id, capability, handler, args);
  }

  invokeAction(
    pluginId: string,
    action: string,
    args: Record<string, unknown> = {},
  ): CapabilityExecutionResult {
    const record = this.records.get(pluginId);
    const handler = record?.runtime?.actions?.[action];
    if (!record || record.status !== "active" || !handler) {
      return pluginError(
        "plugin_action_not_found",
        `Desktop plugin action ${pluginId}:${action} is not available`,
      );
    }
    return this.executeHandler(pluginId, action, handler, args);
  }

  getRuntime(pluginId: string): DesktopPluginRuntime | null {
    const record = this.records.get(pluginId);
    return record?.status === "active" ? record.runtime : null;
  }

  listRecords(): DesktopPluginRecord[] {
    void this.revision.value;
    return [...this.records.values()];
  }

  listSyncIssues(): DesktopPluginSyncIssue[] {
    void this.revision.value;
    return [...this.syncIssues];
  }

  runtimePages(
    target: ActiveRemotePluginPage["page"]["target"],
  ): ActiveRemotePluginPage[] {
    void this.revision.value;
    const pages: ActiveRemotePluginPage[] = [];
    for (const plugin of this.remotePlugins.values()) {
      if (plugin.state !== "enabled") continue;
      for (const page of plugin.pages) {
        if (page.target !== target) continue;
        pages.push({ pluginId: plugin.id, pluginName: plugin.name, page });
      }
    }
    return pages;
  }

  settingsSections(): DesktopPluginSettingsSection[] {
    const sections = new Map<string, DesktopPluginSettingsSection>();
    for (const panel of this.settingsPanels("settings")) {
      const section = panel.contribution.section;
      if (!sections.has(section.id)) sections.set(section.id, section);
    }
    return [...sections.values()].toSorted(
      (left, right) => left.order - right.order || left.id.localeCompare(right.id),
    );
  }

  settingsPanels(
    placement: DesktopPluginSettingsPlacement,
    sectionId?: string,
  ): ActiveDesktopPluginSettingsPanel[] {
    void this.revision.value;
    const panels: ActiveDesktopPluginSettingsPanel[] = [];
    for (const record of this.records.values()) {
      if (record.status !== "active" || !record.runtime) continue;
      for (const contribution of record.manifest.contributes.settingsPanels) {
        if (!contribution.placements.includes(placement)) continue;
        if (sectionId && contribution.section.id !== sectionId) continue;
        panels.push({
          ownerPluginId: record.manifest.id,
          contribution,
          runtime: record.runtime,
        });
      }
    }
    return panels.toSorted(
      (left, right) =>
        left.contribution.section.order - right.contribution.section.order ||
        left.ownerPluginId.localeCompare(right.ownerPluginId) ||
        left.contribution.id.localeCompare(right.contribution.id),
    );
  }

  deactivate(pluginId: string): boolean {
    const record = this.records.get(pluginId);
    if (!record || record.status !== "active") return false;
    try {
      record.runtime?.dispose();
    } catch (error) {
      record.error = errorMessage(error);
    } finally {
      this.surfaces.clear(pluginId);
      for (const capability of record.manifest.contributes.capabilities) {
        if (this.capabilityOwners.get(capability) === record) {
          this.capabilityOwners.delete(capability);
        }
      }
      record.runtime = null;
      record.status = "disposed";
      this.touch();
    }
    return true;
  }

  dispose(): void {
    for (const record of [...this.records.values()].reverse()) {
      this.deactivate(record.manifest.id);
    }
  }

  private validateHandlers(
    manifest: DesktopPluginFacetManifest,
    runtime: DesktopPluginRuntime,
  ): void {
    validateHandlerSet(
      manifest.id,
      "capability",
      manifest.contributes.capabilities,
      runtime.capabilities ?? {},
    );
    validateHandlerSet(
      manifest.id,
      "action",
      manifest.contributes.actions,
      runtime.actions ?? {},
    );
  }

  private executeHandler(
    pluginId: string,
    name: string,
    handler: DesktopPluginHandler,
    args: Record<string, unknown>,
  ): CapabilityExecutionResult {
    try {
      return handler(args);
    } catch (error) {
      return pluginError(
        "desktop_plugin_failed",
        `Desktop plugin ${pluginId}:${name} failed: ${errorMessage(error)}`,
      );
    }
  }

  private failedRecord(
    manifest: DesktopPluginFacetManifest,
    error: string,
  ): DesktopPluginRecord {
    const record: DesktopPluginRecord = {
      manifest,
      status: "error",
      runtime: null,
      error,
    };
    this.records.set(manifest.id, record);
    this.touch();
    return record;
  }

  private touch(): void {
    this.revision.value += 1;
  }

  private reconcileResult(
    applied: boolean,
    activated: string[],
    deactivated: string[],
  ): DesktopPluginReconcileResult {
    return {
      applied,
      revision: this.runtimeRevision,
      activated,
      deactivated,
      issues: [...this.syncIssues],
    };
  }
}

function compatibilityIssue(
  local: DesktopPluginFacetManifest,
  remote: RemotePluginRuntime,
): DesktopPluginSyncIssue | null {
  const facet = remote.desktop;
  if (!facet) return null;
  if (facet.mode !== "builtin") {
    return {
      pluginId: remote.id,
      code: "unsupported_mode",
      message: `Desktop runtime mode ${facet.mode} is not supported yet`,
    };
  }
  if (remote.version !== local.version) {
    return {
      pluginId: remote.id,
      code: "version_mismatch",
      message: `Gateway has ${remote.version}, Desktop bundle has ${local.version}`,
    };
  }
  if (facet.entrypoint !== local.entrypoint) {
    return {
      pluginId: remote.id,
      code: "entrypoint_mismatch",
      message: `Gateway requested ${facet.entrypoint}, Desktop provides ${local.entrypoint}`,
    };
  }
  return null;
}

function missingArtifactIssue(
  remote: RemotePluginRuntime,
): DesktopPluginSyncIssue {
  const mode = remote.desktop?.mode;
  return mode !== "builtin"
    ? {
        pluginId: remote.id,
        code: "unsupported_mode",
        message: `Desktop runtime mode ${mode} is not supported yet`,
      }
    : {
        pluginId: remote.id,
        code: "artifact_missing",
        message: "The Gateway plugin is enabled but this Desktop bundle has no artifact",
      };
}
