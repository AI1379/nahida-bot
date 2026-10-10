/** Rebuild the calibrated motion from the local MTN and measured Core ranges. */
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { readMtn, retargetNod, motion3FromSamples } from './curves.js';

const root = new URL('../../live2d_model/', import.meta.url);
const sourcePath = new URL('香澄-2023常服/live2d/chara/001_general_rip/nod01.mtn', root);
const calibrationPath = new URL('_motion-lab/nod01-calibration.json', root);
const outputPath = new URL('Nahida_1080/nahida-kasumi-nod01-calibrated.motion3.json', root);

try {
  const sourceText = await readFile(sourcePath, 'utf8');
  const calibration = JSON.parse(await readFile(calibrationPath, 'utf8'));
  const motion = retargetNod(readMtn(sourceText), calibration.sourceParameters, calibration.targetParameters, calibration.config);
  const document = motion3FromSamples(motion);
  const output = JSON.stringify(document, null, 2);
  await writeFile(outputPath, output, 'utf8');
  const metadata = {
    source: fileURLToPath(sourcePath), sourceSha256: createHash('sha256').update(sourceText).digest('hex'),
    output: fileURLToPath(outputPath), outputSha256: createHash('sha256').update(output).digest('hex'),
    config: calibration.config, mappings: motion.report, duration: motion.duration, frames: motion.count,
  };
  await writeFile(new URL('_motion-lab/nod01-export.json', root), JSON.stringify(metadata, null, 2), 'utf8');
  console.log(JSON.stringify({ output: metadata.output, duration: motion.duration, curves: document.Meta.CurveCount, sha256: metadata.outputSha256 }));
} catch (error) {
  console.error(`Motion export failed: ${error.message}`);
  process.exitCode = 1;
}
