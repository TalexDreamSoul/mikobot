import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

describe("form control focus styles", () => {
  it.each([
    ["input", <Input aria-label="input" />],
    ["textarea", <Textarea aria-label="textarea" />],
    ["select", <Select aria-label="select" />],
  ])("uses a subdued inset focus ring for the %s", (label, control) => {
    render(control);

    const element = screen.getByLabelText(label);
    expect(element).toHaveClass(
      "focus-visible:ring-2",
      "focus-visible:ring-inset",
      "focus-visible:ring-ring/50",
    );
    expect(element).not.toHaveClass(
      "ring-offset-background",
      "focus-visible:ring-ring",
      "focus-visible:ring-offset-2",
    );
  });
});

describe("shared form controls", () => {
  it("keeps the select native so it reports a value and change events", () => {
    const onChange = vi.fn();
    render(
      <Select aria-label="Environment" value="staging" onChange={onChange}>
        <option value="staging">Staging</option>
        <option value="production">Production</option>
      </Select>,
    );

    const select = screen.getByRole("combobox", { name: "Environment" });
    expect(select.tagName).toBe("SELECT");
    expect(select).toHaveValue("staging");

    fireEvent.change(select, { target: { value: "production" } });
    expect(onChange).toHaveBeenCalled();
  });

  it("keeps the checkbox native, labelled, and disableable", () => {
    const onChange = vi.fn();
    render(
      <label>
        <Checkbox checked onChange={onChange} />
        Ship it
      </label>,
    );

    const checkbox = screen.getByRole("checkbox", { name: "Ship it" });
    expect(checkbox).toBeChecked();

    fireEvent.click(checkbox);
    expect(onChange).toHaveBeenCalled();
  });

  it("disables the checkbox input rather than only dimming its box", () => {
    render(<Checkbox aria-label="locked" checked={false} disabled readOnly />);

    expect(screen.getByRole("checkbox", { name: "locked" })).toBeDisabled();
  });
});
