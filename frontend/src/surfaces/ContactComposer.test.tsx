// Component tests for the contact-modal composer (addendum 2026-08-14): the
// stage's option set fills a dropdown, the FIRST option prefills the editable
// box on open, selecting another option refills it, the user's own typing
// wins, and the submit hands the parent the message plus the HONEST channel
// (DM for 1st-degree, invite note otherwise; a pending invite blocks the form
// with a reason instead of a dead send). i18next is real English so the
// asserted prefills are the shipped strings.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createInstance } from "i18next";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { NetContact } from "../api/types";
import en from "../i18n/locales/en";

// Late-bound real translator behind the hoisted react-i18next mock.
const h = vi.hoisted(() => ({
  t: (key: string, _opts?: Record<string, unknown>): string => key,
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string, opts?: Record<string, unknown>) => h.t(key, opts) }),
}));

import { ContactComposer } from "./ContactComposer";
import { stageTemplateOptions } from "./stageTemplates";

beforeAll(async () => {
  const i18n = createInstance();
  await i18n.init({
    lng: "en",
    resources: { en: { translation: en } },
    interpolation: { escapeValue: false },
  });
  h.t = i18n.t.bind(i18n) as typeof h.t;
});

afterEach(cleanup);

function contact(overrides: Partial<NetContact>): NetContact {
  return {
    id: "c1",
    linkedin_url: "https://www.linkedin.com/in/sarah-tan",
    name: "Sarah Tan",
    current_role: "Engineering Manager",
    current_company: "Northline",
    headline: "",
    connection_degree: 2,
    is_first_degree: false,
    audience_tag: "hm",
    warmth: "cold",
    connection_status: "engagement",
    last_message: null,
    last_message_at: null,
    last_message_direction: null,
    last_message_from: null,
    sent_at: null,
    accepted_at: null,
    added_at: "2026-08-01T00:00:00+00:00",
    ...overrides,
  };
}

function box(): HTMLTextAreaElement {
  return screen.getByTestId("contact-compose-message") as HTMLTextAreaElement;
}

describe("ContactComposer", () => {
  it("prefills the box with the stage's first option and refills on selection", () => {
    const c = contact({ connection_status: "engagement" });
    const options = stageTemplateOptions("engagement", c, h.t);
    render(<ContactComposer contact={c} onSubmit={() => {}} />);

    expect(box().value).toBe(options[0]!.body);
    expect(box().value).toContain("Can you please refer me for a role at Northline?");

    // Switching options autofills the box with that option's text.
    fireEvent.change(screen.getByTestId("contact-compose-template"), {
      target: { value: options[1]!.id },
    });
    expect(box().value).toBe(options[1]!.body);

    // "Write my own" clears it for a fresh start.
    fireEvent.change(screen.getByTestId("contact-compose-template"), {
      target: { value: "custom" },
    });
    expect(box().value).toBe("");
  });

  it("keeps the user's edits — templates are starting points, never locks", () => {
    const c = contact({ connection_status: "accepted", is_first_degree: true });
    const onSubmit = vi.fn();
    render(<ContactComposer contact={c} onSubmit={onSubmit} />);

    fireEvent.change(box(), { target: { value: "My own words entirely." } });
    expect(box().value).toBe("My own words entirely.");

    fireEvent.submit(screen.getByTestId("contact-compose"));
    expect(onSubmit).toHaveBeenCalledWith("My own words entirely.", "dm");
  });

  it("states the DM channel for a 1st-degree contact", () => {
    render(
      <ContactComposer
        contact={contact({ connection_status: "accepted", is_first_degree: true })}
        onSubmit={() => {}}
      />,
    );
    expect(screen.getByTestId("contact-compose-channel").textContent).toContain(
      "real DM",
    );
  });

  it("states the invite-note channel for a non-1st-degree contact", () => {
    const onSubmit = vi.fn();
    render(
      <ContactComposer
        contact={contact({ connection_status: "ghosted", is_first_degree: false })}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByTestId("contact-compose-channel").textContent).toContain(
      "connection request",
    );
    fireEvent.submit(screen.getByTestId("contact-compose"));
    expect(onSubmit).toHaveBeenCalledWith(expect.stringContaining("Hi Sarah,"), "connection_note");
  });

  it("blocks the form with a reason while an invite is still pending", () => {
    render(
      <ContactComposer
        contact={contact({ connection_status: "sent", is_first_degree: false })}
        onSubmit={() => {}}
      />,
    );
    expect(screen.getByTestId("contact-compose-blocked").textContent).toContain(
      "still pending",
    );
    expect(screen.queryByTestId("contact-compose-message")).toBeNull();
    expect(screen.queryByTestId("contact-compose-send")).toBeNull();
  });
});
