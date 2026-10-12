/**
 * Interface language (Russian by default, English available in Settings).
 *
 * The UI source stays in English; this module translates what React renders: text nodes plus the
 * placeholder / title / aria-label attributes, including job messages coming from the backend.
 * Only exact, known interface strings are replaced, so user content (prompts, transcripts, file names)
 * is left alone. Elements marked with class "notranslate", inputs, and editable text are never touched.
 */
import { RU, RU_PATTERNS } from "./i18n_ru";

export type Lang = "ru" | "en";
const KEY = "localai.lang";

export function getLang(): Lang {
  try { return (localStorage.getItem(KEY) as Lang) || "ru"; } catch { return "ru"; }
}
export function setLang(l: Lang) {
  try { localStorage.setItem(KEY, l); } catch { /* storage unavailable */ }
  location.reload();
}

const missing = new Set<string>();
(window as any).__i18nMissing = missing;           // for finding untranslated strings while developing

/** Translate one interface string (used by code that builds strings outside the DOM, e.g. confirm()). */
export function tr(s: string): string {
  if (getLang() === "en") return s;
  return translate(s) ?? s;
}

function translate(raw: string): string | null {
  const s = raw.trim().replace(/\s+/g, " ");
  if (!s || !/[A-Za-z]/.test(s) || s.length > 400) return null;
  let out = RU[s];
  if (out === undefined) {
    for (const [re, rep] of RU_PATTERNS) {
      if (re.test(s)) { out = s.replace(re, rep as any); break; }
    }
  }
  if (out === undefined) { missing.add(s); return null; }
  // keep the surrounding whitespace React relies on between inline elements
  const lead = raw.match(/^\s*/)![0], trail = raw.match(/\s*$/)![0];
  return lead + out + trail;
}

const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "CODE", "PRE"]);
function skipped(el: Element | null): boolean {
  for (let e = el; e; e = e.parentElement) {
    if (SKIP_TAGS.has(e.tagName) || e.classList.contains("notranslate") || (e as HTMLElement).isContentEditable) return true;
  }
  return false;
}

const done = new WeakMap<Node, string>();            // node -> the translated value we wrote (avoids loops)
function doText(n: Text) {
  const v = n.nodeValue ?? "";
  if (done.get(n) === v) return;
  if (skipped(n.parentElement)) return;
  const t = translate(v);
  if (t !== null && t !== v) { n.nodeValue = t; done.set(n, t); } else done.set(n, v);
}
const ATTRS = ["placeholder", "title", "aria-label"];
function doAttrs(el: Element) {
  if (el.closest(".notranslate")) return;
  for (const a of ATTRS) {
    const v = el.getAttribute(a);
    if (!v) continue;
    const t = translate(v);
    if (t !== null && t !== v) el.setAttribute(a, t);
  }
}
function walk(root: Node) {
  if (root.nodeType === Node.TEXT_NODE) { doText(root as Text); return; }
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  const el = root as Element;
  if (SKIP_TAGS.has(el.tagName) && el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") return;
  doAttrs(el);
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    if (n.nodeType === Node.TEXT_NODE) doText(n as Text); else doAttrs(n as Element);
  }
}

export function startI18n() {
  const lang = getLang();
  document.documentElement.lang = lang;
  if (lang === "en") return;
  const title = translate(document.title); if (title) document.title = title;
  walk(document.body);
  new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === "characterData") doText(m.target as Text);
      else if (m.type === "attributes") doAttrs(m.target as Element);
      else m.addedNodes.forEach(walk);
    }
  }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
}
