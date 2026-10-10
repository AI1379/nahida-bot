/** Strict epoch/sequence gate for delayed and duplicate binary output packets. */
export type VoiceLabAudioPacket = { kind: "pcm"; pcm: Int16Array } | { kind: "artifact"; data: ArrayBuffer };

export class VoiceLabPacketGate {
  epoch = 0;
  turnId = "";
  sequence = -1;
  dropped = 0;

  advance(): number {
    this.epoch++;
    this.turnId = "";
    this.sequence = -1;
    return this.epoch;
  }

  accept(data: ArrayBuffer): Int16Array | null {
    const packet = this.decode(data);
    return packet?.kind === "pcm" ? packet.pcm : null;
  }

  decode(data: ArrayBuffer): VoiceLabAudioPacket | null {
    if (data.byteLength < 4) throw new Error("Invalid audio packet");
    const view = new DataView(data);
    const size = view.getUint32(0, true);
    const start = 4 + size;
    if (size > 4096 || start >= data.byteLength || data.byteLength > 8 * 1024 * 1024 + 4100) throw new Error("Invalid audio frame size");
    const header = JSON.parse(new TextDecoder().decode(new Uint8Array(data, 4, size)));
    if (!header || !Number.isInteger(header.sequence)) {
      throw new Error("Invalid audio metadata");
    }
    if (header.epoch !== this.epoch || !this.turnId || header.turn_id !== this.turnId
        || header.sequence <= this.sequence) {
      this.dropped++;
      return null;
    }
    this.sequence = header.sequence;
    if (header.kind === "artifact") return { kind: "artifact", data: data.slice(start) };
    if (header.sample_rate_hz !== 24000 || start + 960 !== data.byteLength) throw new Error("Invalid PCM frame size");
    const pcm = new Int16Array(480);
    for (let i = 0; i < pcm.length; i++) pcm[i] = view.getInt16(start + i * 2, true);
    return { kind: "pcm", pcm };
  }
}

/** Energy VAD for the lab only: 60ms attack and 450ms silence endpoint. */
export class VoiceLabVad {
  speaking = false;
  private loudMs = 0;
  private quietMs = 0;

  update(rms: number, threshold: number): "start" | "stop" | null {
    this.loudMs = rms >= threshold ? this.loudMs + 20 : 0;
    this.quietMs = rms < threshold ? this.quietMs + 20 : 0;
    if (!this.speaking && this.loudMs >= 60) {
      this.speaking = true;
      return "start";
    }
    if (this.speaking && this.quietMs >= 450) {
      this.speaking = false;
      return "stop";
    }
    return null;
  }
}
