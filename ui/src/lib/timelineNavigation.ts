export type SeekableTimelineSegment = {
  id: string; source: string; start: number; end: number;
  transitionAfter?: { duration: number };
};
export type TimelineSeekTarget = { segmentId: string; source: string; sourceTime: number; timelineTime: number };

/** Map an assembled timeline time to a source frame. At an overlap, choose the incoming clip. */
export function timelineSeekTargetAt(segments: readonly SeekableTimelineSegment[], requestedTime: number): TimelineSeekTarget | null {
  if (!segments.length || !Number.isFinite(requestedTime)) return null;
  const entries: { segment: SeekableTimelineSegment; timelineStart: number }[] = [];
  let total = 0;
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index], length = segment.end - segment.start;
    if (!Number.isFinite(length) || length <= 0 || !Number.isFinite(segment.start) || !Number.isFinite(segment.end)) continue;
    entries.push({ segment, timelineStart: total });
    let overlap = 0;
    if (index < segments.length - 1) {
      const nextLength = segments[index + 1].end - segments[index + 1].start;
      const requested = Number(segment.transitionAfter?.duration) || 0;
      const bounded = Math.max(0, Math.min(requested, length * 0.45, nextLength * 0.45));
      overlap = bounded >= 0.15 ? bounded : 0;
    }
    total += length - overlap;
  }
  if (!entries.length) return null;
  const timelineTime = Math.max(0, Math.min(total, requestedTime));
  let entry = entries[0];
  for (const candidate of entries) {
    if (candidate.timelineStart <= timelineTime) entry = candidate;
    else break;
  }
  const sourceTime = Math.max(entry.segment.start, Math.min(entry.segment.end,
    entry.segment.start + timelineTime - entry.timelineStart));
  return { segmentId: entry.segment.id, source: entry.segment.source,
    sourceTime: +sourceTime.toFixed(3), timelineTime: +timelineTime.toFixed(3) };
}
