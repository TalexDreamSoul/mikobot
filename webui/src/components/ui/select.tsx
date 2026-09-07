import * as React from "react";
import { ChevronDown } from "lucide-react";

import { formControlFocusClassName } from "@/components/ui/form-control";
import { cn } from "@/lib/utils";

type SelectProps = React.SelectHTMLAttributes<HTMLSelectElement> & {
  /** Class names for the positioning wrapper, not the control itself. */
  containerClassName?: string;
};

/**
 * A native ``select`` styled like {@link Input}.
 *
 * The element stays native on purpose: it keeps the platform popup on mobile,
 * the implicit ``combobox`` role for assistive technology, and ``change`` events
 * for tests. Only the chrome is ours.
 */
const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, containerClassName, children, ...props }, ref) => {
    return (
      <div className={cn("relative w-full", containerClassName)}>
        <select
          ref={ref}
          className={cn(
            "flex h-10 w-full appearance-none truncate rounded-control border border-input bg-background",
            "py-2 pl-3 pr-9 text-sm disabled:cursor-not-allowed disabled:opacity-50",
            formControlFocusClassName,
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
      </div>
    );
  },
);
Select.displayName = "Select";

export { Select };
