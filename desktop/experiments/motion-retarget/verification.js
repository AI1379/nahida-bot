/** Verify the exported JSON with Cubism's own decoder, independently of the lab sampler. */
export function verifyNativeMotion(stage, document, samples) {
  const manager = stage.internal.motionManager;
  const decoded = structuredClone(document);
  decoded.Meta.FadeInTime = 0;
  decoded.Meta.FadeOutTime = 0;
  const motion = manager.createMotion(decoded, 'Study', { File: 'calibrated.motion3.json' });
  const queue = new manager.queueManager.constructor();
  queue.setEventCallback(() => {});
  queue.startMotion(motion, false, 0);
  let maximumError = 0, checkedValues = 0;
  try {
    for (let frame = 0; frame < samples.count; frame++) {
      for (const parameter of Object.values(stage.parameters)) stage.setValue(parameter, parameter.default);
      queue.doUpdateMotion(stage.core, frame / samples.fps);
      for (const [id, values] of Object.entries(samples.curves)) {
        const actual = stage.core.getParameterValueByIndex(stage.parameters[id].index);
        if (!Number.isFinite(actual)) throw new Error(`Native decoder produced non-finite ${id}`);
        maximumError = Math.max(maximumError, Math.abs(actual - values[frame]));
        checkedValues++;
      }
    }
    return { checkedValues, maximumError, passed: maximumError < 0.00001, fadeDisabledForNumericalComparison: true };
  } finally {
    queue.release();
  }
}

/** Record the actual WebGL render with labels; no synthetic animation or screenshots. */
export async function recordComparison({ canvas, seek, renderAt, duration = 4.2, rightTitle = '实验候选', rightSubtitle = '尚需视觉验收' }) {
  const output = document.createElement('canvas');
  output.width = canvas.width;
  output.height = canvas.height + 112;
  const context = output.getContext('2d');
  const stream = output.captureStream(30), chunks = [];
  const mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp9') ? 'video/webm;codecs=vp9' : 'video/webm';
  const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 5000000 });
  const completed = new Promise((resolve, reject) => {
    recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = () => resolve(new Blob(chunks, { type: mimeType }));
    recorder.onerror = () => reject(new Error('Comparison recording failed'));
  });
  seek(0);
  recorder.start();
  try {
    for (let frame = 0; frame <= Math.ceil(duration * 30); frame++) {
      const at = frame / 30;
      renderAt(at, 1000 / 30);
      context.fillStyle = '#e9e9df'; context.fillRect(0, 0, output.width, output.height);
      context.fillStyle = '#14201e'; context.fillRect(0, 0, output.width, 68);
      context.fillStyle = '#e7eee8'; context.font = 'bold 21px "Microsoft YaHei", sans-serif';
      ['香澄 · 原动作', '纳西妲 · 数值直转', rightTitle].forEach((title, index) => context.fillText(title, index * output.width / 3 + 20, 31));
      context.fillStyle = '#aebfb7'; context.font = '13px "Microsoft YaHei", sans-serif';
      ['原始曲线', '名称/范围映射基线', rightSubtitle].forEach((label, index) => context.fillText(label, index * output.width / 3 + 20, 53));
      context.drawImage(canvas, 0, 68);
      context.fillStyle = '#24392e'; context.font = '15px "Microsoft YaHei", sans-serif';
      context.fillText(`${at.toFixed(2)} s / ${duration.toFixed(2)} s   ·   物理 ${document.getElementById('physics').checked ? '开启' : '关闭'} · ${document.getElementById('isolation').selectedOptions[0].text}`, 20, output.height - 16);
      await new Promise(resolve => setTimeout(resolve, 1000 / 30));
    }
    recorder.stop();
    const blob = await completed;
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('Could not read comparison recording'));
      reader.readAsDataURL(blob);
    });
  } finally {
    stream.getTracks().forEach(track => track.stop());
    seek(0);
  }
}
