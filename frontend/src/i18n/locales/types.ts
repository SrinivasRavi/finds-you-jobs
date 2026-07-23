// Locale typing: `Messages` is the English shape; locales are DeepPartial so a
// partially translated language compiles — anything missing falls back to
// English at runtime (i18next fallbackLng). The string index signature exists
// because CLDR plural categories differ per language: English only has
// `_one`/`_other`, but ru/pl need `_few`/`_many` and ar all six — keys that
// don't exist on the English shape must still be legal in a locale.
import type en from "./en";

export type Messages = typeof en;
export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K];
} & { [extraPluralForm: string]: unknown };
