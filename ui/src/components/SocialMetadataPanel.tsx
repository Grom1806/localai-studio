import React, { useState } from "react";
import { Field } from "./common";

export type SocialMeta = {
  youtube: { title: string; description: string; tags: string[]; hashtags: string[] };
  instagram: { caption: string; hashtags: string[] };
  tiktok: { caption: string; hashtags: string[] };
};

export const blankSocial = (): SocialMeta => ({
  youtube: { title: "", description: "", tags: [], hashtags: [] },
  instagram: { caption: "", hashtags: [] },
  tiktok: { caption: "", hashtags: [] },
});

const splitList = (text: string) => text.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

export function SocialMetadataPanel({ value, onChange, compact = false }: {
  value: Partial<SocialMeta> | null | undefined;
  onChange?: (next: SocialMeta) => void;
  compact?: boolean;
}) {
  const [platform, setPlatform] = useState<keyof SocialMeta>("youtube");
  const empty = blankSocial();
  const social: SocialMeta = {
    youtube: { ...empty.youtube, ...(value?.youtube ?? {}) },
    instagram: { ...empty.instagram, ...(value?.instagram ?? {}) },
    tiktok: { ...empty.tiktok, ...(value?.tiktok ?? {}) },
  };
  const current = social[platform];
  const update = (patch: Partial<typeof current>) => onChange?.({ ...social, [platform]: { ...current, ...patch } } as SocialMeta);
  const area = (label: string, text: string, set: (v: string) => void, rows = 3) => (
    <Field label={label}><textarea rows={rows} value={text} readOnly={!onChange}
      onChange={(e) => set(e.target.value)} style={{ resize: "vertical" }} /></Field>
  );
  const list = (label: string, items: string[], set: (v: string[]) => void) => (
    <Field label={label} hint="Separate with commas">
      <input value={items.join(", ")} readOnly={!onChange} onChange={(e) => set(splitList(e.target.value))} />
    </Field>
  );

  return <div className="col" style={{ gap: compact ? 8 : 12 }}>
    <div className="row wrap" style={{ gap: 6 }}>
      {([ ["youtube", "YouTube"], ["instagram", "Instagram"], ["tiktok", "TikTok"] ] as const).map(([id, label]) =>
        <button key={id} className={`btn sm ${platform === id ? "primary" : "ghost"}`} onClick={() => setPlatform(id)}>{label}</button>)}
    </div>
    {platform === "youtube" && <>
      <Field label="Publication title"><input value={social.youtube.title} readOnly={!onChange}
        onChange={(e) => update({ title: e.target.value })} /></Field>
      {area("Short description", social.youtube.description, (description) => update({ description }), 3)}
      {list("YouTube tags", social.youtube.tags, (tags) => update({ tags }))}
      {list("Hashtags", social.youtube.hashtags, (hashtags) => update({ hashtags }))}
    </>}
    {platform === "instagram" && <>
      {area("Instagram caption", social.instagram.caption, (caption) => update({ caption }), compact ? 3 : 4)}
      {list("Hashtags", social.instagram.hashtags, (hashtags) => update({ hashtags }))}
    </>}
    {platform === "tiktok" && <>
      {area("TikTok caption", social.tiktok.caption, (caption) => update({ caption }), compact ? 3 : 4)}
      {list("Hashtags", social.tiktok.hashtags, (hashtags) => update({ hashtags }))}
    </>}
  </div>;
}
