import type {
  DesktopPluginContext,
  DesktopPluginFacetManifest,
  DesktopPluginHostAdapter,
} from "./desktopPluginContract";

/** Owner-scoped bridge from a facet runtime to the shared surface store. */
export class DesktopPluginSurfaceManager {
  private readonly adapter: DesktopPluginHostAdapter;
  private readonly owned = new Map<string, Set<string>>();

  constructor(adapter: DesktopPluginHostAdapter) {
    this.adapter = adapter;
  }

  createContext(manifest: DesktopPluginFacetManifest): DesktopPluginContext {
    const declarations = new Map(
      manifest.contributes.surfaces.map((surface) => [surface.id, surface]),
    );
    const ownedIds = new Set<string>();
    this.owned.set(manifest.id, ownedIds);
    return {
      emitEvent: (event) => this.adapter.emitEvent(event),
      setSurface: (surfaceId, view) => {
        const declaration = declarations.get(surfaceId);
        if (!declaration) throw undeclaredSurface(manifest.id, surfaceId);
        ownedIds.add(surfaceId);
        this.adapter.upsertSurface({
          ownerPluginId: manifest.id,
          ...declaration,
          source: "local",
          view,
        });
      },
      removeSurface: (surfaceId) => {
        if (!declarations.has(surfaceId)) {
          throw undeclaredSurface(manifest.id, surfaceId);
        }
        ownedIds.delete(surfaceId);
        this.adapter.removeSurface(manifest.id, surfaceId);
      },
    };
  }

  clear(pluginId: string): void {
    const surfaceIds = this.owned.get(pluginId);
    if (!surfaceIds) return;
    for (const surfaceId of surfaceIds) {
      this.adapter.removeSurface(pluginId, surfaceId);
    }
    this.owned.delete(pluginId);
  }
}

function undeclaredSurface(pluginId: string, surfaceId: string): Error {
  return new Error(`Plugin ${pluginId} did not declare surface ${surfaceId}`);
}
