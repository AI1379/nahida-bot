import type { Component } from "vue";

import type {
  PluginSurfaceKind,
  PluginSurfaceTarget,
  PluginSurfaceView,
} from "@/domain/pluginSurface";
import type {
  CapabilityExecutionResult,
  DesktopEvent,
} from "@/domain/runtime";

export type DesktopPluginSettingsPlacement = "settings" | "workbench";

export interface DesktopPluginSettingsSection {
  id: string;
  label: string;
  hint: string;
  order: number;
}

export interface DesktopPluginSettingsPanelContribution {
  id: string;
  section: DesktopPluginSettingsSection;
  placements: DesktopPluginSettingsPlacement[];
  component: Component;
}

export interface DesktopPluginSurfaceDeclaration {
  id: string;
  target: PluginSurfaceTarget;
  kind: PluginSurfaceKind;
  priority: number;
}

/** Compiled Desktop facet metadata from the shared plugin manifest. */
export interface DesktopPluginFacetManifest {
  id: string;
  name: string;
  version: string;
  entrypoint: string;
  builtin: boolean;
  contributes: {
    capabilities: string[];
    actions: string[];
    surfaces: DesktopPluginSurfaceDeclaration[];
    settingsPanels: DesktopPluginSettingsPanelContribution[];
  };
}

export interface DesktopPluginContext {
  emitEvent(event: DesktopEvent): unknown;
  setSurface(surfaceId: string, view: PluginSurfaceView): void;
  removeSurface(surfaceId: string): void;
}

export type DesktopPluginHandler = (
  args: Record<string, unknown>,
) => CapabilityExecutionResult;

export interface DesktopPluginRuntime {
  state?: unknown;
  capabilities?: Record<string, DesktopPluginHandler>;
  actions?: Record<string, DesktopPluginHandler>;
  dispose(): void;
}

export interface DesktopPluginDefinition {
  manifest: DesktopPluginFacetManifest;
  activate(context: DesktopPluginContext): DesktopPluginRuntime;
}

export interface DesktopPluginHostAdapter {
  emitEvent(event: DesktopEvent): unknown;
  upsertSurface(surface: {
    ownerPluginId: string;
    id: string;
    target: PluginSurfaceTarget;
    kind: PluginSurfaceKind;
    priority: number;
    source: "local";
    view: PluginSurfaceView;
  }): void;
  removeSurface(ownerPluginId: string, surfaceId: string): void;
}

export interface ActiveDesktopPluginSettingsPanel {
  ownerPluginId: string;
  contribution: DesktopPluginSettingsPanelContribution;
  runtime: DesktopPluginRuntime;
}

export interface DesktopPluginRecord {
  manifest: DesktopPluginFacetManifest;
  status: "active" | "error" | "disposed";
  runtime: DesktopPluginRuntime | null;
  error: string;
}
