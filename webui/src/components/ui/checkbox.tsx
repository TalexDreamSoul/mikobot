import * as React from "react";
import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

type CheckboxProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "type"> & {
  /** Class names for the visible box, not the input element. */
  boxClassName?: string;
};

/**
 * A native checkbox with the app's mark.
 *
 * The input keeps its own role, focus, and ``change`` events and is only hidden
 * visually, so a caller may still label it, disable it, or drive it in a test.
 * The rendered box mirrors ``:checked`` from React state, and the focus ring is
 * drawn from the input's ``peer`` state so keyboard focus stays visible.
 */
const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, boxClassName, checked, ...props }, ref) => {
    return (
      <span className={cn("relative inline-flex h-5 w-5 shrink-0", className)}>
        <input
          ref={ref}
          type="checkbox"
          checked={checked}
          className="peer absolute inset-0 z-10 h-5 w-5 cursor-pointer opacity-0 disabled:cursor-not-allowed"
          {...props}
        />
        <span
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-0 grid place-items-center rounded-mark border transition-colors",
            "peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2",
            "peer-disabled:opacity-60",
            checked
              ? "border-foreground bg-foreground text-background"
              : "border-input bg-background text-transparent",
            boxClassName,
          )}
        >
          <Check className="h-3 w-3" strokeWidth={2.75} />
        </span>
      </span>
    );
  },
);
Checkbox.displayName = "Checkbox";

export { Checkbox };
