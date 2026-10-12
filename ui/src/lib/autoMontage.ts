export type MontageTransition = { type: "dissolve" | "fade" | "fadeblack"; duration: number };
export type MontageSegment = { id: string; source: string; start: number; end: number; transitionAfter?: MontageTransition };
export type MontageWord = { start: number; end: number; word: string };
export type SceneChangeCandidate = { time: number; score: number };
export type SpeechEditPlan = { segments: MontageSegment[]; sceneCuts: number };

const makeId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

/** Build conservative source ranges: long pauses may be removed; scene cuts need a high-confidence visual cue
 * aligned to a sentence-ending speech gap so a shot change can never split an active word or unfinished phrase.
 */
export function makeSpeechEdit(
  source: { id: string; duration: number },
  inputWords: MontageWord[],
  existingSubtitles: string,
  sceneChanges: SceneChangeCandidate[] = [],
  sceneScanCompleted = false,
  idFactory: () => string = makeId,
): SpeechEditPlan {
  const words = inputWords.filter((word) => Number.isFinite(word.start) && Number.isFinite(word.end) &&
    word.end > word.start && word.word.trim()).map((word) => ({ ...word,
      start: Math.max(0, Math.min(source.duration, word.start)), end: Math.max(0, Math.min(source.duration, word.end)) }))
    .filter((word) => word.end > word.start).sort((a, b) => a.start - b.start);
  const wholeSource = (): SpeechEditPlan => ({
    segments: [{ id: idFactory(), source: source.id, start: 0, end: source.duration }], sceneCuts: 0,
  });
  if (words.length < 8) return wholeSource();

  const pauses = words.slice(1).map((word, index) => ({ index: index + 1, gap: word.start - words[index].end }))
    .filter((pause) => {
      const left = words.slice(Math.max(0, pause.index - 3), pause.index), right = words.slice(pause.index, pause.index + 3);
      return pause.gap >= 2.8 && left.length === 3 && right.length === 3 &&
        left[2].end - left[0].start >= .75 && right[2].end - right[0].start >= .75;
    });
  const cuts: { index: number; at: number; scene: boolean; transitionDuration: number }[] = [];
  for (const pause of [...pauses].sort((a, b) => b.gap - a.gap)) {
    if (cuts.length >= 30) break;
    const at = (words[pause.index - 1].end + words[pause.index].start) / 2;
    if (cuts.every((cut) => Math.abs(cut.index - pause.index) >= 4 && Math.abs(cut.at - at) >= 3)) {
      cuts.push({ index: pause.index, at, scene: false, transitionDuration: .32 });
    }
  }

  const sceneCandidates = sceneScanCompleted && Array.isArray(sceneChanges)
    ? sceneChanges.filter((change) => Number.isFinite(change.time) && Number.isFinite(change.score) &&
      change.score >= .58 && change.time >= 1.2 && change.time <= source.duration - 1.2)
      .sort((a, b) => b.score - a.score)
    : [];
  for (const scene of sceneCandidates) {
    if (cuts.length >= 30) break;
    let best: { index: number; at: number; distance: number } | null = null;
    for (let index = 1; index < words.length; index++) {
      const leftWord = words[index - 1], rightWord = words[index];
      const gap = rightWord.start - leftWord.end;
      if (gap < .35 || gap >= 2.8) continue; // Long pauses already have a dedicated, more conservative path.
      const left = words.slice(Math.max(0, index - 3), index), right = words.slice(index, index + 3);
      if (left.length !== 3 || right.length !== 3 || left[2].end - left[0].start < .75 || right[2].end - right[0].start < .75) continue;
      if (!/[.!?…][\"'”’)]?$/.test(leftWord.word.trim())) continue; // Preserve sentence boundaries, not mid-thought pauses.
      const midpoint = (leftWord.end + rightWord.start) / 2;
      const distance = Math.abs(scene.time - midpoint);
      if (distance > .55 || scene.time < leftWord.end - .18 || scene.time > rightWord.start + .18) continue;
      // Use the actual speech gap as the edit point. The vision detector is sampled at 2 fps,
      // so its timestamp is evidence for the edit, not precise enough to cut dialogue on its own.
      const at = midpoint;
      if (cuts.some((cut) => Math.abs(cut.index - index) < 4 || Math.abs(cut.at - at) < 3.5)) continue;
      if (!best || distance < best.distance) best = { index, at, distance };
    }
    if (best) {
      const gap = words[best.index].start - words[best.index - 1].end;
      cuts.push({ index: best.index, at: best.at, scene: true, transitionDuration: Math.min(.32, gap * .45) });
    }
  }

  cuts.sort((a, b) => a.index - b.index);
  const cutByIndex = new Map(cuts.map((cut) => [cut.index, cut]));
  const boundaries = new Set(cuts.map((cut) => cut.index));
  const groups: MontageWord[][] = [];
  let group: MontageWord[] = [];
  words.forEach((word, index) => {
    if (index > 0 && boundaries.has(index)) { groups.push(group); group = []; }
    group.push(word);
  });
  if (group.length) groups.push(group);

  const transitionType: MontageTransition["type"] = existingSubtitles === "visible" ? "fadeblack" : "dissolve";
  let cursor = 0;
  const groupStarts = groups.map((items) => { const start = cursor; cursor += items.length; return start; });
  const ranges = groups.map((items, index) => {
    const from = groupStarts[index], to = from + items.length;
    const sceneIn = cutByIndex.get(from), sceneOut = cutByIndex.get(to);
    return {
      start: index === 0 && items[0].start < .8 ? 0 : sceneIn?.scene ? sceneIn.at : Math.max(0, items[0].start - .28),
      end: index === groups.length - 1 && source.duration - items[items.length - 1].end < 1 ? source.duration
        : sceneOut?.scene ? sceneOut.at : Math.min(source.duration, items[items.length - 1].end + .38),
      transitionDuration: sceneOut?.transitionDuration ?? .32,
    };
  }).filter((range) => range.end - range.start >= .75);
  if (!ranges.length) return wholeSource();
  return {
    segments: ranges.map((range, index) => ({ id: idFactory(), source: source.id, start: range.start, end: range.end,
      transitionAfter: index < ranges.length - 1 ? { type: transitionType, duration: range.transitionDuration } : undefined })),
    sceneCuts: cuts.filter((cut) => cut.scene).length,
  };
}
