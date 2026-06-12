import { basename, dirname, isAbsolute, resolve, sep } from "node:path";
import {
  cpSync,
  createReadStream,
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";
import type { Connect, Plugin } from "vite";

const configuredModelRoot =
  process.env.NAHIDA_DESKTOP_LIVE2D_MODEL_ROOT ?? "live2d_model";
const modelRoot = isAbsolute(configuredModelRoot)
  ? resolve(configuredModelRoot)
  : resolve(__dirname, configuredModelRoot);
const buildOutputRoot = resolve(__dirname, "dist");

const contentTypes: Record<string, string> = {
  ".cdi3.json": "application/json; charset=utf-8",
  ".exp3.json": "application/json; charset=utf-8",
  ".motion3.json": "application/json; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".moc3": "application/octet-stream",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
};

function contentTypeFor(pathname: string): string {
  const lower = pathname.toLowerCase();
  const exact = Object.entries(contentTypes).find(([suffix]) =>
    lower.endsWith(suffix),
  );
  return exact?.[1] ?? "application/octet-stream";
}

function hasMotionReferences(
  motions: Record<string, Array<{ File: string }>> | undefined,
): boolean {
  return Object.values(motions ?? {}).some((items) => items.length > 0);
}

const modelJsonCache = new Map<string, { mtime: number; result: string | null }>();

function modelJsonWithLocalReferences(filePath: string): string | null {
  const stat = statSync(filePath);
  const cached = modelJsonCache.get(filePath);
  if (cached && cached.mtime === stat.mtimeMs) return cached.result;

  const result = modelJsonWithLocalReferencesInner(filePath, stat.mtimeMs);
  return result;
}

function modelJsonWithLocalReferencesInner(
  filePath: string,
  mtime: number,
): string | null {
  let result: string | null = null;
  try {
    const source = JSON.parse(readFileSync(filePath, "utf8")) as {
      FileReferences?: {
        Expressions?: Array<{ Name: string; File: string }>;
        Motions?: Record<string, Array<{ File: string }>>;
      };
    };
    const refs = (source.FileReferences ??= {});
    let patched = false;

    const modelDir = dirname(filePath);

    if (!refs.Expressions?.length) {
      const expressions = readdirSync(modelDir)
        .filter((name) => name.toLowerCase().endsWith(".exp3.json"))
        .sort((a, b) => a.localeCompare(b))
        .map((name) => ({
          Name: basename(name, ".exp3.json"),
          File: name,
        }));

      if (expressions.length) {
        refs.Expressions = expressions;
        patched = true;
      }
    }

    if (!hasMotionReferences(refs.Motions)) {
      const motionFiles = readdirSync(modelDir)
        .filter((name) => name.toLowerCase().endsWith(".motion3.json"))
        .sort((a, b) => a.localeCompare(b));
      const idleFiles = motionFiles.filter((name) =>
        /idle|standby/i.test(name),
      );
      const gestureFiles = motionFiles.filter(
        (name) => !idleFiles.includes(name),
      );
      const motions: Record<string, Array<{ File: string }>> = {};

      if (idleFiles.length) {
        motions.Idle = idleFiles.map((name) => ({ File: name }));
      }
      if (gestureFiles.length) {
        motions.Gesture = gestureFiles.map((name) => ({ File: name }));
      }

      if (hasMotionReferences(motions)) {
        refs.Motions = motions;
        patched = true;
      }
    }

    if (!patched && !refs.Expressions?.length && !hasMotionReferences(refs.Motions)) {
      modelJsonCache.set(filePath, { mtime, result: null });
      return null;
    }

    result = JSON.stringify(source);
    modelJsonCache.set(filePath, { mtime, result });
    return result;
  } catch {
    modelJsonCache.set(filePath, { mtime, result: null });
    return null;
  }
}

function serveLive2DModel(
  req: Connect.IncomingMessage,
  res: Connect.ServerResponse,
  next: Connect.NextFunction,
) {
  if (!req.url) {
    next();
    return;
  }

  const requestPath = decodeURIComponent(req.url.split("?")[0] ?? "/");
  const relativePath = requestPath
    .replace(/^\/live2d_model\/?/, "")
    .replace(/^\/+/, "");
  const filePath = resolve(modelRoot, relativePath);

  if (filePath !== modelRoot && !filePath.startsWith(`${modelRoot}${sep}`)) {
    res.statusCode = 403;
    res.end("Forbidden");
    return;
  }

  if (!existsSync(filePath)) {
    next();
    return;
  }

  const stat = statSync(filePath);
  if (!stat.isFile()) {
    next();
    return;
  }

  if (filePath.toLowerCase().endsWith(".model3.json")) {
    const patched = modelJsonWithLocalReferences(filePath);
    if (patched !== null) {
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Content-Length", String(Buffer.byteLength(patched)));
      res.end(patched);
      return;
    }
  }

  res.setHeader("Content-Type", contentTypeFor(filePath));
  res.setHeader("Content-Length", String(stat.size));
  createReadStream(filePath).pipe(res);
}

function live2DModelServer(): Plugin {
  return {
    name: "nahida-live2d-model-server",
    configureServer(server) {
      server.middlewares.use("/live2d_model", serveLive2DModel);
    },
    configurePreviewServer(server) {
      server.middlewares.use("/live2d_model", serveLive2DModel);
    },
    closeBundle() {
      copyLive2DModelsForBuild();
    },
  };
}

function copyLive2DModelsForBuild(): void {
  if (!existsSync(modelRoot)) {
    throw new Error(`Live2D model root does not exist: ${modelRoot}`);
  }

  const destination = resolve(buildOutputRoot, "live2d_model");
  mkdirSync(destination, { recursive: true });
  cpSync(modelRoot, destination, {
    recursive: true,
    filter: (source) =>
      !source.toLowerCase().endsWith(".zip") &&
      !lstatSync(source).isSymbolicLink(),
  });
  patchCopiedModelJsonFiles(destination);
}

function patchCopiedModelJsonFiles(directory: string): void {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      patchCopiedModelJsonFiles(path);
      continue;
    }
    if (!entry.name.toLowerCase().endsWith(".model3.json")) continue;

    const patched = modelJsonWithLocalReferences(path);
    if (patched !== null) {
      writeFileSync(path, patched, "utf8");
    }
  }
}

export default defineConfig({
  plugins: [vue(), tailwindcss(), live2DModelServer()],
  clearScreen: false,
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
  },
  build: {
    outDir: buildOutputRoot,
    emptyOutDir: true,
  },
});
