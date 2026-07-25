// Unit tests for the setLanguage switch-token guard (F-L10 lazy locales).
//
// What is mocked and why: the per-language locale chunks ("./locales/es" etc.)
// are replaced with gate-controlled promises so each test decides WHEN (and
// whether) a chunk resolves — that's the only way to pin the fast-double-switch
// ordering. i18next itself is REAL: the module under test is imported fresh per
// test (vi.resetModules) so its top-level await + synchronous init run for
// real, with localStorage cleared so the boot language is always "en" (the
// boot path takes no dynamic import and the top-level await is not entangled).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Gate registry shared with the hoisted vi.mock factories. Each test recreates
// the gates it needs (fresh per vi.resetModules cycle — the factory re-runs on
// the next dynamic import and reads the gate at that moment).
const ctl = vi.hoisted(() => {
  interface Gate {
    promise: Promise<{ default: object }>;
    resolve: () => void;
    reject: (e: Error) => void;
  }
  const gates: Record<string, Gate> = {};
  function make(code: string): Gate {
    let resolve!: () => void;
    let reject!: (e: Error) => void;
    const promise = new Promise<{ default: object }>((res, rej) => {
      resolve = () => res({ default: {} });
      reject = rej;
    });
    // Detached handler so a rejected gate never trips the unhandled-rejection
    // reporter (the import() consumer is attached a tick later).
    promise.catch(() => {});
    const gate = { promise, resolve, reject };
    gates[code] = gate;
    return gate;
  }
  return { gates, make };
});

// One language per timing-sensitive scenario: a mock factory runs only on the
// module's FIRST-ever import (the instance then survives vi.resetModules), so
// each scenario that must control chunk timing uses a language first imported
// in that scenario. Later imports of an already-resolved chunk settle
// instantly — which only ever makes the races tighter, never looser.
vi.mock("./locales/es", () => ctl.gates.es.promise);
vi.mock("./locales/fr", () => ctl.gates.fr.promise);
vi.mock("./locales/de", () => ctl.gates.de.promise);
vi.mock("./locales/ar", () => ctl.gates.ar.promise);
vi.mock("./locales/pl", () => ctl.gates.pl.promise);

type I18nModule = typeof import("./index");

const flush = () => new Promise<void>((r) => setTimeout(r, 0));

async function importFresh(): Promise<I18nModule> {
  return import("./index");
}

describe("setLanguage switch-token guard", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    document.documentElement.dir = "ltr";
    document.documentElement.lang = "en";
    for (const code of ["es", "fr", "de", "ar", "pl"]) ctl.make(code);
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fast double-switch: only the latest language applies, even when the stale chunk resolves last", async () => {
    const mod = await importFresh();
    const i18n = mod.default;
    mod.setLanguage("es"); // superseded — its chunk is still in flight
    mod.setLanguage("fr"); // latest
    ctl.gates.fr.resolve();
    await vi.waitFor(() => {
      expect(i18n.language).toBe("fr");
    });
    expect(document.documentElement.lang).toBe("fr");
    expect(document.documentElement.dir).toBe("ltr");
    // The stale es chunk lands AFTER fr applied — it must not clobber.
    ctl.gates.es.resolve();
    await flush();
    await flush();
    expect(i18n.language).toBe("fr");
    expect(document.documentElement.lang).toBe("fr");
    // The chunk was still registered (harmless) — but never applied.
    expect(i18n.hasResourceBundle("es", "translation")).toBe(true);
  });

  it("localStorage.setItem throwing does not desync direction vs language, and switching stays alive", async () => {
    const mod = await importFresh();
    const i18n = mod.default;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    // RTL language: direction must flip together with the language even though
    // persistence failed (a storage-based guard would never match and the
    // switch would go dead / desync dir from lang).
    mod.setLanguage("ar");
    ctl.gates.ar.resolve();
    await vi.waitFor(() => {
      expect(i18n.language).toBe("ar");
    });
    expect(document.documentElement.lang).toBe("ar");
    expect(document.documentElement.dir).toBe("rtl");
    // In-session switching still works on the next call (token guard is
    // in-memory, not storage-based) — and direction follows back to ltr.
    mod.setLanguage("es");
    ctl.gates.es.resolve();
    await vi.waitFor(() => {
      expect(i18n.language).toBe("es");
    });
    expect(document.documentElement.lang).toBe("es");
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("chunk-load failure falls back to English strings without changing direction", async () => {
    const mod = await importFresh();
    const i18n = mod.default;
    mod.setLanguage("de"); // ltr language whose chunk will fail
    ctl.gates.de.reject(new Error("chunk load failed"));
    // The switch still lands (missing strings render via fallbackLng en)…
    await vi.waitFor(() => {
      expect(i18n.language).toBe("de");
    });
    // …no bundle was registered for it…
    expect(i18n.hasResourceBundle("de", "translation")).toBe(false);
    // …and dir/lang stay consistent with each other: lang follows the switch,
    // direction is unchanged (ltr → ltr).
    expect(document.documentElement.lang).toBe("de");
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("a superseded switch whose chunk FAILS never applies either", async () => {
    const mod = await importFresh();
    const i18n = mod.default;
    mod.setLanguage("pl"); // superseded AND about to fail (first pl import)
    mod.setLanguage("fr"); // latest
    ctl.gates.fr.resolve();
    await vi.waitFor(() => {
      expect(i18n.language).toBe("fr");
    });
    ctl.gates.pl.reject(new Error("chunk load failed"));
    await flush();
    await flush();
    // The failed, stale pl switch must not touch lang/dir.
    expect(i18n.language).toBe("fr");
    expect(document.documentElement.lang).toBe("fr");
    expect(document.documentElement.dir).toBe("ltr");
  });
});
