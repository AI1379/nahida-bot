/** Export the browser's measured batch without recalculating or hand-tuning curves. */
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { motion3FromSamples } from './curves.js';

const modelRoot = new URL('../../live2d_model/', import.meta.url);
const outputRoot = new URL('_motion-lab/fitted/', modelRoot);
const sha256 = value => createHash('sha256').update(value).digest('hex');
const fingerprints = [
  ['source', '香澄-2023常服/live2d/chara/001_casual-2023_rip/kasumi_casual-2023.moc'],
  ['target', 'Nahida_1080/Nahida_1080.moc3'],
];

try {
  const batch = JSON.parse(await readFile(new URL('batch-results.json', outputRoot), 'utf8'));
  for (const [side, path] of fingerprints) {
    if (sha256(await readFile(new URL(path, modelRoot))) !== batch.references[side].modelSha256) throw new Error(`Model changed: ${side}`);
  }
  const exports = [];
  for (const [name, result] of Object.entries(batch.results)) {
    if (!/^[a-z0-9_-]+$/.test(name)) throw new Error('Invalid experiment asset name');
    const source = new URL(`香澄-2023常服/live2d/chara/001_general_rip/${name}.mtn`, modelRoot);
    if (sha256(await readFile(source)) !== result.report.sourceFileHash) throw new Error(`Source changed: ${name}`);
    const file = `nahida-${name}-inverse-v1.motion3.json`;
    const data = JSON.stringify(motion3FromSamples(result.motion), null, 2);
    await writeFile(new URL(file, outputRoot), data);
    exports.push({ name, file, sha256: sha256(data), duration: result.motion.duration, frames: result.motion.count,
      configurationFingerprint: result.report.configurationFingerprint, reviewFlags: result.report.reviewFlags,
      status: 'experimental-awaiting-visual-review', summary: result.report.summary });
  }
  await writeFile(new URL('exports.json', outputRoot), JSON.stringify(exports, null, 2));
  console.log(JSON.stringify(exports.map(({ name, file, reviewFlags }) => ({ name, file, reviewFlags }))));
} catch (error) {
  console.error(`Fitted export failed: ${error.message}`);
  process.exitCode = 1;
}
