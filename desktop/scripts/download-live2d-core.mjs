import { mkdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";

const source =
  process.env.LIVE2D_CUBISM_CORE_URL ??
  "https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js";
const destination = resolve(
  "public",
  "live2d-core",
  "live2dcubismcore.min.js",
);

if (existsSync(destination)) {
  console.log(`Live2D Cubism Core already exists: ${destination}`);
  process.exit(0);
}

console.log(`Downloading Live2D Cubism Core from ${source}`);
const response = await fetch(source);
if (!response.ok) {
  throw new Error(
    `Failed to download Live2D Cubism Core: ${response.status} ${response.statusText}`,
  );
}

const bytes = new Uint8Array(await response.arrayBuffer());
await mkdir(dirname(destination), { recursive: true });
await writeFile(destination, bytes);
console.log(`Saved Live2D Cubism Core to ${destination}`);
