export type PreviewTransitionType = "dissolve" | "fade" | "fadeblack" | "wipeleft" | "wiperight" | "slideright" | "slideleft" | "circleopen" | "smoothleft";
export type PreviewRole = "incoming" | "outgoing";
export type PreviewSegment = { id: string; source: string; start: number; end: number; transitionAfter?: { type: PreviewTransitionType; duration: number } };
export type PreviewTransition = { type: PreviewTransitionType; duration: number; progress: number; activeRole: PreviewRole; otherSource: string; otherTime: number };
export type PreviewAudioGains = { active: number; other: number };
export type PreviewLayerStyle = { opacity?: number; transform?: string; clipPath?: string; zIndex: number };

function transitionDuration(current: PreviewSegment | undefined, next: PreviewSegment | undefined): number {
  if (!current || !next || !current.transitionAfter) return 0;
  const currentLength = current.end - current.start, nextLength = next.end - next.start;
  const requested = current.transitionAfter.duration;
  if (![currentLength, nextLength, requested].every(Number.isFinite) || currentLength <= 0 || nextLength <= 0) return 0;
  const duration = Math.min(Math.max(0, requested), currentLength * 0.45, nextLength * 0.45);
  return duration >= 0.15 ? duration : 0;
}

/** Describe the neighboring source frame whenever the selected clip is inside a transition. */
export function previewTransitionAt(segments: readonly PreviewSegment[], selectedIndex: number, sourceTime: number): PreviewTransition | null {
  if (!Number.isFinite(sourceTime) || selectedIndex < 0 || selectedIndex >= segments.length) return null;
  const current = segments[selectedIndex];
  const outgoingDuration = transitionDuration(current, segments[selectedIndex + 1]);
  if (outgoingDuration && current.transitionAfter && sourceTime >= current.end - outgoingDuration && sourceTime <= current.end) {
    const progress = Math.max(0, Math.min(1, (sourceTime - (current.end - outgoingDuration)) / outgoingDuration));
    const next = segments[selectedIndex + 1];
    return { type: current.transitionAfter.type, duration: outgoingDuration, progress, activeRole: "outgoing",
      otherSource: next.source, otherTime: +(next.start + progress * outgoingDuration).toFixed(3) };
  }
  const previous = segments[selectedIndex - 1];
  const incomingDuration = transitionDuration(previous, current);
  if (incomingDuration && previous?.transitionAfter && sourceTime >= current.start && sourceTime <= current.start + incomingDuration) {
    const progress = Math.max(0, Math.min(1, (sourceTime - current.start) / incomingDuration));
    return { type: previous.transitionAfter.type, duration: incomingDuration, progress, activeRole: "incoming",
      otherSource: previous.source, otherTime: +(previous.end - incomingDuration + progress * incomingDuration).toFixed(3) };
  }
  return null;
}

export function transitionAudioGains(type: PreviewTransitionType, progress: number, activeRole: PreviewRole): PreviewAudioGains {
  const p = Math.max(0, Math.min(1, Number.isFinite(progress) ? progress : 0));
  const incoming = type === "fadeblack" ? (p > 0.5 ? (p - 0.5) * 2 : 0) : p;
  const outgoing = type === "fadeblack" ? (p < 0.5 ? 1 - p * 2 : 0) : 1 - p;
  return activeRole === "incoming" ? { active: incoming, other: outgoing } : { active: outgoing, other: incoming };
}

/** CSS state for the active or neighboring frame, driven by the same progress as export. */
export function transitionLayerStyle(type: PreviewTransitionType, progress: number, role: PreviewRole): PreviewLayerStyle {
  const p = Math.max(0, Math.min(1, Number.isFinite(progress) ? progress : 0));
  const incoming = role === "incoming";
  const style: PreviewLayerStyle = { zIndex: incoming ? 2 : 1 };
  if (type === "dissolve" || type === "fade") style.opacity = incoming ? p : 1 - p;
  else if (type === "fadeblack") style.opacity = incoming ? (p > 0.5 ? (p - 0.5) * 2 : 0) : (p < 0.5 ? 1 - p * 2 : 0);
  else if (incoming && (type === "wipeleft" || type === "smoothleft")) style.clipPath = `inset(0 0 0 ${(1 - p) * 100}%)`;
  else if (incoming && type === "wiperight") style.clipPath = `inset(0 ${(1 - p) * 100}% 0 0)`;
  else if (incoming && type === "circleopen") style.clipPath = `circle(${p * 150}% at 50% 50%)`;
  else if (type === "slideright" || type === "slideleft") {
    const direction = type === "slideright" ? 1 : -1;
    style.transform = incoming ? `translateX(${direction * (1 - p) * 100}%)` : `translateX(${-direction * p * 100}%)`;
  }
  return style;
}
