// Unit tests for the stage→template mapping (maintainer spec 2026-08-14 + the
// dropdown addendum): each mapped kanban stage exposes its full option set (3
// distinct choices), Engagement personalizes with the contact's own employer
// and degrades cleanly when it's blank, the greeting degrades when the name is
// blank, and unmapped stages offer nothing. i18next is REAL, initialized from
// the English reference bundle, so the assertions cover the shipped strings.

import { createInstance } from "i18next";
import { beforeAll, describe, expect, it } from "vitest";

import en from "../i18n/locales/en";
import type { ConnectionStatus } from "../api/types";
import { firstNameOf, stageTemplateOptions } from "./stageTemplates";

let t: (key: string, opts?: Record<string, unknown>) => string;

beforeAll(async () => {
  const i18n = createInstance();
  await i18n.init({
    lng: "en",
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
  });
  t = i18n.t.bind(i18n);
});

const SARAH = { name: "Sarah Tan", current_company: "Northline" };

describe("stageTemplateOptions", () => {
  it("exposes 3 distinct options for each mapped stage", () => {
    for (const stage of ["accepted", "engagement", "ghosted"] as ConnectionStatus[]) {
      const options = stageTemplateOptions(stage, SARAH, t);
      expect(options).toHaveLength(3);
      expect(new Set(options.map((o) => o.id)).size).toBe(3);
      expect(new Set(options.map((o) => o.label)).size).toBe(3);
      expect(new Set(options.map((o) => o.body)).size).toBe(3);
      for (const o of options) {
        expect(o.id.startsWith(`${stage}-`)).toBe(true);
        expect(o.body.startsWith("Hi Sarah,")).toBe(true);
        expect(o.body.length).toBeGreaterThan(40);
      }
    }
  });

  it("unmapped stages offer no templates (blank, fully manual composer)", () => {
    for (const stage of ["sent", "converted"] as ConnectionStatus[]) {
      expect(stageTemplateOptions(stage, SARAH, t)).toEqual([]);
    }
  });

  it("engagement is a referral ask personalized with the contact's employer", () => {
    const options = stageTemplateOptions("engagement", SARAH, t);
    // Every engagement option asks about a referral at THEIR company.
    for (const o of options) {
      expect(o.body).toContain("Northline");
      expect(o.body.toLowerCase()).toContain("refer");
    }
    // The maintainer's exact ask shape leads the set.
    expect(options[0]!.body).toContain("Can you please refer me for a role at Northline?");
  });

  it("engagement degrades gracefully when the employer is blank", () => {
    const options = stageTemplateOptions("engagement", { ...SARAH, current_company: " " }, t);
    for (const o of options) {
      expect(o.body).toContain("your company");
      expect(o.body).not.toContain("{{company}}");
    }
  });

  it("accepted and ghosted are reminders, not referral asks", () => {
    for (const stage of ["accepted", "ghosted"] as ConnectionStatus[]) {
      for (const o of stageTemplateOptions(stage, SARAH, t)) {
        expect(o.body.toLowerCase()).not.toContain("refer");
      }
    }
    // The stage intent shows in the copy: accepted follows up on the earlier
    // note; ghosted nudges after silence.
    const accepted = stageTemplateOptions("accepted", SARAH, t);
    expect(accepted.some((o) => o.body.includes("earlier"))).toBe(true);
    const ghosted = stageTemplateOptions("ghosted", SARAH, t);
    expect(ghosted.some((o) => o.body.includes("nudge"))).toBe(true);
  });

  it("greets by first name, degrading to a plain greeting when the name is blank", () => {
    expect(firstNameOf("Sarah Tan")).toBe("Sarah");
    expect(firstNameOf("")).toBe("");
    const named = stageTemplateOptions("accepted", SARAH, t);
    expect(named[0]!.body.startsWith("Hi Sarah,\n\n")).toBe(true);
    const unnamed = stageTemplateOptions("accepted", { name: "", current_company: "" }, t);
    expect(unnamed[0]!.body.startsWith("Hi,\n\n")).toBe(true);
  });

  it("never leaves an uninterpolated placeholder in any option of any stage", () => {
    for (const stage of ["accepted", "engagement", "ghosted"] as ConnectionStatus[]) {
      for (const contact of [SARAH, { name: "", current_company: "" }]) {
        for (const o of stageTemplateOptions(stage, contact, t)) {
          expect(o.body).not.toMatch(/\{\{|\}\}/);
          expect(o.label).not.toMatch(/\{\{|\}\}/);
        }
      }
    }
  });
});
