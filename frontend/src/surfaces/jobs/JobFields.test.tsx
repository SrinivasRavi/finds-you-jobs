import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api";
import type { JobDraft } from "../../api/types";
import { draftForSave, emptyDraft, useAutofill } from "./JobFields";

const LINK = "https://boards.greenhouse.io/acme/jobs/9";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function useForm() {
  const [draft, setDraft] = useState<JobDraft>({ ...emptyDraft(), canonical_url: LINK });
  const autofill = useAutofill(draft, setDraft);
  return { draft, setDraft, autofill };
}

afterEach(() => vi.restoreAllMocks());

describe("useAutofill", () => {
  it("keeps what the user typed while the posting was being read", async () => {
    let land!: (d: JobDraft) => void;
    vi.spyOn(api, "previewJob").mockReturnValue(new Promise((r) => (land = r)));
    const { result } = renderHook(useForm, { wrapper });

    act(() => result.current.autofill.run(LINK));
    // The form stays editable while it reads, so the user keeps typing.
    act(() => result.current.setDraft({ ...result.current.draft, title: "Staff Engineer" }));
    await act(async () =>
      land({
        ...emptyDraft(),
        canonical_url: LINK,
        title: "Senior Software Engineer",
        company: "Acme",
        description: "The board's text.",
        source_adapter: "greenhouse",
      }),
    );

    await waitFor(() => expect(result.current.autofill.state.kind).toBe("filled"));
    expect(result.current.draft.title).toBe("Staff Engineer");
    expect(result.current.draft.company).toBe("Acme");
  });

  it("undo restores only the fields the fetch filled", async () => {
    vi.spyOn(api, "previewJob").mockResolvedValue({
      ...emptyDraft(),
      canonical_url: LINK,
      company: "Acme",
      source_adapter: "greenhouse",
    });
    const { result } = renderHook(useForm, { wrapper });

    act(() => result.current.autofill.run(LINK));
    await waitFor(() => expect(result.current.autofill.state.kind).toBe("filled"));
    act(() => result.current.setDraft({ ...result.current.draft, location: "Pune" }));
    act(() => result.current.autofill.undo());

    expect(result.current.draft.company).toBe("");
    expect(result.current.draft.location).toBe("Pune");
  });
});

describe("draftForSave", () => {
  it("names a link whose autofill never landed a pasted URL, not pasted text", () => {
    const saved = draftForSave({ ...emptyDraft(), canonical_url: ` ${LINK} ` });
    expect(saved.canonical_url).toBe(LINK);
    expect(saved.source_adapter).toBe("paste-url");
  });

  it("keeps the board an autofill read", () => {
    const saved = draftForSave({ ...emptyDraft(), canonical_url: LINK, source_adapter: "greenhouse" });
    expect(saved.source_adapter).toBe("greenhouse");
  });

  it("never lets prose reach the link", () => {
    const saved = draftForSave({ ...emptyDraft(), canonical_url: "JD from a recruiter email" });
    expect(saved.canonical_url).toBe("");
    expect(saved.source_adapter).toBe("paste-text");
  });
});
