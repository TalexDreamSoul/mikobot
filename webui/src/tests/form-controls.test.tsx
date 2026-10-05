import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Checkbox } from "@/components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

function EnvironmentPicker() {
  const [value, setValue] = useState("staging");
  return <Select value={value} onValueChange={setValue}>
    <SelectTrigger aria-label="Environment"><SelectValue /></SelectTrigger>
    <SelectContent>
      <SelectItem value="staging">Staging</SelectItem>
      <SelectItem value="production">Production</SelectItem>
    </SelectContent>
  </Select>;
}

describe("shared form controls", () => {
  it("selects an environment by keyboard and restores focus with the selected value", async () => {
    const user = userEvent.setup();
    render(<EnvironmentPicker />);
    const trigger = screen.getByRole("combobox", { name: "Environment" });
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{End}{Enter}");
    expect(trigger).toHaveTextContent("Production");
    expect(trigger).toHaveFocus();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("keeps the checkbox native, labelled, and disableable", () => {
    const onChange = vi.fn();
    render(<label><Checkbox checked onChange={onChange} />Ship it</label>);
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
