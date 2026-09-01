/** Cubism motion3 segment parsing and deterministic curve evaluation. */
type Motion3SegmentKind = "linear" | "bezier" | "stepped" | "inverse-stepped";

interface Motion3Point {
  timeSeconds: number;
  value: number;
}

interface Motion3Segment {
  kind: Motion3SegmentKind;
  start: Motion3Point;
  end: Motion3Point;
  control1?: Motion3Point;
  control2?: Motion3Point;
}

/** A validated source curve whose segment times increase monotonically. */
export interface ParsedMotion3Curve {
  initial: Motion3Point;
  segments: Motion3Segment[];
}

const segmentValueCounts = {
  0: 2,
  1: 6,
  2: 2,
  3: 2,
} as const;

// Segment arrays encode a type tag followed by either one endpoint or two
// Bezier controls plus an endpoint; the initial point has no type tag.

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}

function point(values: unknown[], offset: number, label: string): Motion3Point {
  return {
    timeSeconds: finiteNumber(values[offset], `${label} time`),
    value: finiteNumber(values[offset + 1], `${label} value`),
  };
}

function segmentKind(value: unknown): keyof typeof segmentValueCounts {
  if (value === 0 || value === 1 || value === 2 || value === 3) return value;
  throw new Error(`unsupported motion3 segment type: ${String(value)}`);
}

function assertSegmentTimes(segment: Motion3Segment): void {
  if (segment.end.timeSeconds <= segment.start.timeSeconds) {
    throw new Error("motion3 segment times must increase");
  }
  for (const control of [segment.control1, segment.control2]) {
    if (
      control &&
      (control.timeSeconds < segment.start.timeSeconds ||
        control.timeSeconds > segment.end.timeSeconds)
    ) {
      throw new Error("motion3 Bezier control times must stay inside the segment");
    }
  }
}

/** Parse the compact Cubism segment array and reject malformed timelines. */
export function parseMotion3Segments(values: unknown): ParsedMotion3Curve {
  if (!Array.isArray(values) || values.length < 2) {
    throw new Error("motion3 curve must contain an initial point");
  }

  const initial = point(values, 0, "initial point");
  const segments: Motion3Segment[] = [];
  let start = initial;
  let offset = 2;

  while (offset < values.length) {
    const type = segmentKind(values[offset]);
    const valueCount = segmentValueCounts[type];
    if (offset + valueCount >= values.length) {
      throw new Error("motion3 segment is truncated");
    }

    let segment: Motion3Segment;
    if (type === 1) {
      segment = {
        kind: "bezier",
        start,
        control1: point(values, offset + 1, "Bezier control 1"),
        control2: point(values, offset + 3, "Bezier control 2"),
        end: point(values, offset + 5, "Bezier end"),
      };
    } else {
      segment = {
        kind:
          type === 0
            ? "linear"
            : type === 2
              ? "stepped"
              : "inverse-stepped",
        start,
        end: point(values, offset + 1, "segment end"),
      };
    }
    assertSegmentTimes(segment);
    segments.push(segment);
    start = segment.end;
    offset += valueCount + 1;
  }

  return { initial, segments };
}

function interpolate(start: number, end: number, progress: number): number {
  return start + (end - start) * progress;
}

function cubicBezier(
  start: number,
  control1: number,
  control2: number,
  end: number,
  progress: number,
): number {
  const remaining = 1 - progress;
  return (
    remaining ** 3 * start +
    3 * remaining ** 2 * progress * control1 +
    3 * remaining * progress ** 2 * control2 +
    progress ** 3 * end
  );
}

function bezierValueAt(segment: Motion3Segment, timeSeconds: number): number {
  const control1 = segment.control1;
  const control2 = segment.control2;
  if (!control1 || !control2) return segment.end.value;

  // Cubism stores Bezier control points in time/value space. Solve time first
  // instead of assuming the curve parameter is linear with wall-clock time.
  // Eighteen bisection steps are deterministic and sub-frame accurate here.
  let lower = 0;
  let upper = 1;
  for (let iteration = 0; iteration < 18; iteration += 1) {
    const progress = (lower + upper) / 2;
    const time = cubicBezier(
      segment.start.timeSeconds,
      control1.timeSeconds,
      control2.timeSeconds,
      segment.end.timeSeconds,
      progress,
    );
    if (time < timeSeconds) lower = progress;
    else upper = progress;
  }
  const progress = (lower + upper) / 2;
  return cubicBezier(
    segment.start.value,
    control1.value,
    control2.value,
    segment.end.value,
    progress,
  );
}

function segmentValueAt(segment: Motion3Segment, timeSeconds: number): number {
  if (segment.kind === "stepped") {
    return timeSeconds < segment.end.timeSeconds
      ? segment.start.value
      : segment.end.value;
  }
  if (segment.kind === "inverse-stepped") return segment.end.value;
  if (segment.kind === "bezier") return bezierValueAt(segment, timeSeconds);

  const progress =
    (timeSeconds - segment.start.timeSeconds) /
    (segment.end.timeSeconds - segment.start.timeSeconds);
  return interpolate(segment.start.value, segment.end.value, progress);
}

/** Evaluate a parsed curve at a source-motion timestamp in seconds. */
export function motion3CurveValueAt(
  curve: ParsedMotion3Curve,
  timeSeconds: number,
): number {
  if (timeSeconds <= curve.initial.timeSeconds || !curve.segments.length) {
    return curve.initial.value;
  }
  const segment =
    curve.segments.find((candidate) => timeSeconds <= candidate.end.timeSeconds) ??
    curve.segments.at(-1);
  if (!segment || timeSeconds >= segment.end.timeSeconds) {
    return segment?.end.value ?? curve.initial.value;
  }
  return segmentValueAt(segment, timeSeconds);
}
