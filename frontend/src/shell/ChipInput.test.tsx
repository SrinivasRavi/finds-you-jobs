// The role field's suggestion rules (10-10 report 4.3): a suggestion is added only
// when the user picks it; Enter keeps exactly what was typed.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { ChipInput } from "./ChipInput";

afterEach(cleanup);

function renderWith(onAdd: (v: string) => void) {
  render(
    <ChipInput
      label="Roles"
      items={[]}
      onAdd={onAdd}
      onRemove={() => {}}
      placeholder=""
      testid="roles"
      suggestions={["Back End Engineer", "Backer"]}
    />,
  );
  const input = screen.getByTestId("roles-input");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "backend" } });
  return input;
}

describe("ChipInput suggestions", () => {
  it("lists them while typing", () => {
    renderWith(() => {});
    expect(screen.getAllByTestId("roles-option").map((o) => o.textContent)).toEqual([
      "Back End Engineer",
      "Backer",
    ]);
  });

  it("Enter keeps what was typed, never the top suggestion", () => {
    const added: string[] = [];
    const input = renderWith((v) => added.push(v));
    fireEvent.keyDown(input, { key: "Enter" });
    expect(added).toEqual(["backend"]);
  });

  it("an arrow-picked suggestion is what Enter adds", () => {
    const added: string[] = [];
    const input = renderWith((v) => added.push(v));
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(added).toEqual(["Back End Engineer"]);
  });

  it("a clicked suggestion is added", () => {
    const added: string[] = [];
    renderWith((v) => added.push(v));
    fireEvent.mouseDown(screen.getAllByTestId("roles-option")[1]);
    expect(added).toEqual(["Backer"]);
  });
});
