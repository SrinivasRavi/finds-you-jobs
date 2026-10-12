// Every English key exists in all 12 other languages. A missing key falls back
// to English silently, which is how the trust chip and the "3d ago" phrases
// shipped English-only; this fails the build instead. Plural suffixes are
// folded, since each language has its own set of plural forms.
import { describe, expect, it } from "vitest";

import en from "./locales/en";

const LANGS = ["ar", "de", "es", "fr", "hi", "it", "ja", "nl", "pl", "pt", "ru", "zh"];
const PLURAL = /_(zero|one|two|few|many|other)$/;

function keys(o: object, prefix = ""): string[] {
  return Object.entries(o).flatMap(([k, v]) =>
    v && typeof v === "object" ? keys(v, `${prefix}${k}.`) : [prefix + k.replace(PLURAL, "")],
  );
}

describe("locale coverage", () => {
  const english = [...new Set(keys(en))];
  it.each(LANGS)("%s has every English key", async (lang) => {
    const mod = (await import(`./locales/${lang}.ts`)) as { default: object };
    const have = new Set(keys(mod.default));
    expect(english.filter((k) => !have.has(k))).toEqual([]);
  });
});
