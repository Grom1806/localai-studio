export type TimelineSourceSegment = {
  id: string;
  source: string;
  start: number;
  end: number;
};

export type SceneChangeEvidence = {
  time?: unknown;
  score?: unknown;
};

export type SceneTimelineMarker = {
  id: string;
  segmentId: string;
  sourceId: string;
  sourceTime: number;
  timelineTime: number;
  score: number;
};

/** Place source-time visual candidates only inside retained ranges of the analyzed source. */
export function sceneMarkersForTimeline(
  segments: TimelineSourceSegment[],
  timelineStarts: number[],
  sourceId: string | undefined,
  evidence: SceneChangeEvidence[],
  edgePadding = 0.15,
): SceneTimelineMarker[] {
  if (!sourceId || !evidence.length) return [];
  const markers: SceneTimelineMarker[] = [];
  const seen = new Set<string>();
  const padding = Number.isFinite(edgePadding) ? Math.max(0, edgePadding) : 0.15;

  segments.forEach((segment, index) => {
    const timelineStart = timelineStarts[index];
    if (segment.source !== sourceId || !Number.isFinite(timelineStart)
      || !Number.isFinite(segment.start) || !Number.isFinite(segment.end) || segment.end <= segment.start) return;

    for (const candidate of evidence) {
      const sourceTime = Number(candidate.time);
      if (!Number.isFinite(sourceTime) || sourceTime <= segment.start + padding || sourceTime >= segment.end - padding) continue;
      const id = `${segment.id}:${sourceTime.toFixed(2)}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const rawScore = Number(candidate.score);
      markers.push({
        id,
        segmentId: segment.id,
        sourceId,
        sourceTime,
        timelineTime: +(timelineStart + sourceTime - segment.start).toFixed(3),
        score: Number.isFinite(rawScore) ? Math.max(0, Math.min(1, rawScore)) : 0,
      });
    }
  });

  return markers.sort((a, b) => a.timelineTime - b.timelineTime);
}
