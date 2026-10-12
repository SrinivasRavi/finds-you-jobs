// The picker's commit rules (docs/internal/place-picker.md section 3), each as its
// own failing-if-broken assertion, against a stubbed suggestion source.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { api } from "../api";
import type { PlaceChip, PlaceSuggestion } from "../api/types";
import { PlaceInput } from "./PlaceInput";

const GB: PlaceSuggestion = {
  label: "London, England, United Kingdom",
  name: "London",
  level: "city",
  id: "2643743",
};
const ON: PlaceSuggestion = {
  label: "London, Ontario, Canada",
  name: "London",
  level: "city",
  id: "6058560",
};

let added: PlaceChip[] = [];

function Harness({ start = [] as PlaceChip[] }) {
  const [items, setItems] = useState<PlaceChip[]>(start);
  return (
    <PlaceInput
      label="Locations"
      items={items}
      onAdd={(v) => {
        added.push(v);
        setItems((r) => [...r, v]);
      }}
      onRemove={(v) => setItems((r) => r.filter((x) => x !== v))}
      placeholder="Add a place"
      testid="loc"
    />
  );
}

function mount(start: PlaceChip[] = []) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Harness start={start} />
    </QueryClientProvider>,
  );
  return screen.getByTestId("loc-input") as HTMLInputElement;
}

beforeEach(() => {
  added = [];
  vi.spyOn(api, "suggestPlaces").mockImplementation(async (q: string) =>
    q.toLowerCase().startsWith("lon") ? [GB, ON] : [],
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("PlaceInput", () => {
  it("offers both Londons, each named in full", async () => {
    const input = mount();
    fireEvent.change(input, { target: { value: "london" } });
    await waitFor(() => expect(screen.getAllByTestId("loc-option")).toHaveLength(2));
    expect(screen.getAllByTestId("loc-option").map((o) => o.textContent)).toEqual([
      GB.label,
      ON.label,
    ]);
  });

  it("Enter takes the top suggestion and the chip shows its full name", async () => {
    const input = mount();
    fireEvent.change(input, { target: { value: "london" } });
    await screen.findAllByTestId("loc-option");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(added).toEqual([GB]));
    expect(screen.getByTestId("loc").textContent).toContain(GB.label);
  });

  it("the arrow keys choose the other London", async () => {
    const input = mount();
    fireEvent.change(input, { target: { value: "london" } });
    await screen.findAllByTestId("loc-option");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(added).toEqual([ON]));
  });

  it("clicking a suggestion picks it, not the top one", async () => {
    const input = mount();
    fireEvent.change(input, { target: { value: "london" } });
    const options = await screen.findAllByTestId("loc-option");
    fireEvent.mouseDown(options[1]);
    await waitFor(() => expect(added).toEqual([ON]));
  });

  it("Enter before the list has loaded still takes a place, not the bare text", async () => {
    const input = mount();
    fireEvent.change(input, { target: { value: "london" } });
    fireEvent.keyDown(input, { key: "Enter" }); // inside the 150 ms debounce
    await waitFor(() => expect(added).toEqual([GB]));
  });

  it("text matching no place is kept as typed", async () => {
    const input = mount();
    fireEvent.change(input, { target: { value: "Moonbase" } });
    await screen.findByText("jobBoard.prefs.placeNoMatch");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(added).toEqual(["Moonbase"]));
  });

  it("a comma never commits, because full names contain commas", async () => {
    const input = mount();
    fireEvent.change(input, { target: { value: "london" } });
    fireEvent.keyDown(input, { key: "," });
    expect(added).toEqual([]);
    expect(input.value).toBe("london");
  });

  it("leaving the box takes the top suggestion", async () => {
    const input = mount();
    fireEvent.change(input, { target: { value: "london" } });
    await screen.findAllByTestId("loc-option");
    fireEvent.blur(input);
    await waitFor(() => expect(added).toEqual([GB]));
  });

  it("picking a place already chosen adds nothing", async () => {
    const input = mount([GB]);
    fireEvent.change(input, { target: { value: "london" } });
    await screen.findAllByTestId("loc-option");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(input.value).toBe(""));
    expect(added).toEqual([]);
  });

  it("a saved plain-string chip still renders", () => {
    mount(["Bengaluru"]);
    expect(screen.getByTestId("loc").textContent).toContain("Bengaluru");
  });
});
