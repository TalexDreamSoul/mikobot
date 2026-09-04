import { useEffect, useId, useRef } from "react";
import { Loader2, RotateCcw, Search, ShieldAlert, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SettingsSectionKey } from "@/components/settings/contracts";
import { ExtensionPackageDetail } from "@/components/settings/extensions/ExtensionPackageDetail";
import {
  actionLabel,
  executionLabel,
  isolationLabel,
  kindLabel,
  lifecycleLabel,
  sourceLabel,
  trustLabel,
} from "@/components/settings/extensions/extensionLabels";
import {
  ANY_FILTER,
  availableComponentKinds,
  availableLifecycles,
  availableSources,
  availableTrust,
  disclosesExecutableRisk,
  hasActiveExtensionFilters,
  type ExtensionFilters,
} from "@/components/settings/extensions/extensionModel";
import { useExtensionsSettings } from "@/components/settings/extensions/useExtensionsSettings";
import {
  SETTINGS_SEARCH_INPUT_CLASS,
  SettingsSectionTitle,
  StatusPill,
} from "@/components/settings/shared/SettingsControls";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ExtensionDiagnostic, ExtensionPackage } from "@/lib/types";
import { cn } from "@/lib/utils";

const FILTER_SELECT_CLASS = cn(
  "h-9 w-full min-w-0 rounded-control border border-border/45 bg-settings-surface px-2.5",
  "text-[12.5px] text-foreground transition-colors hover:border-border/70",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:w-auto",
);

function FilterSelect<T extends string>({
  label,
  value,
  options,
  optionLabel,
  onChange,
}: {
  label: string;
  value: T | typeof ANY_FILTER;
  options: readonly T[];
  optionLabel: (option: T) => string;
  onChange: (value: T | typeof ANY_FILTER) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <select
        id={id}
        aria-label={label}
        className={FILTER_SELECT_CLASS}
        value={value}
        onChange={(event) => onChange(event.target.value as T | typeof ANY_FILTER)}
      >
        <option value={ANY_FILTER}>
          {t("settings.extensions.filters.anyOf", {
            label,
            defaultValue: "All: {{label}}",
          })}
        </option>
        {options.map((option) => (
          <option key={option} value={option}>
            {optionLabel(option)}
          </option>
        ))}
      </select>
    </div>
  );
}

function DiagnosticsRegion({ diagnostics }: { diagnostics: ExtensionDiagnostic[] }) {
  const { t } = useTranslation();
  if (!diagnostics.length) return null;
  return (
    <section
      role="alert"
      aria-label={t("settings.extensions.diagnostics.title", {
        defaultValue: "Extension families that could not report",
      })}
      className="rounded-control border border-amber-500/40 bg-amber-500/8 px-3.5 py-3"
    >
      <div className="flex items-start gap-3">
        <TriangleAlert
          className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-300"
          aria-hidden
        />
        <div className="min-w-0">
          <p className="text-[12.5px] font-medium text-foreground">
            {t("settings.extensions.diagnostics.title", {
              defaultValue: "Extension families that could not report",
            })}
          </p>
          <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
            {t("settings.extensions.diagnostics.description", {
              defaultValue:
                "These owners failed to report. Everything below is still complete for every other owner.",
            })}
          </p>
          <ul className="mt-2 space-y-1.5">
            {diagnostics.map((diagnostic) => (
              <li key={`${diagnostic.owner_id}:${diagnostic.code}`} className="min-w-0">
                <code className="break-all font-mono text-[11.5px] text-foreground">
                  {diagnostic.owner_id}
                </code>
                <span className="ml-1.5 text-[12px] leading-5 text-muted-foreground">
                  {diagnostic.message}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

function PackageListRow({
  pkg,
  selected,
  onSelect,
}: {
  pkg: ExtensionPackage;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation();
  return (
    <li>
      <button
        type="button"
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
        className={cn(
          "touch-target w-full min-w-0 rounded-control px-3 py-2.5 text-left transition-colors",
          selected ? "bg-sidebar-accent" : "hover:bg-muted/45",
        )}
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-foreground">
            {pkg.display_name}
          </span>
          {disclosesExecutableRisk(pkg) ? (
            <ShieldAlert
              className="h-3.5 w-3.5 shrink-0 text-amber-700 dark:text-amber-300"
              aria-label={t("settings.extensions.risk.badge", {
                defaultValue: "Unisolated third-party code",
              })}
            />
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <StatusPill>{sourceLabel(t, pkg.source)}</StatusPill>
          <StatusPill tone={pkg.lifecycle === "failed" ? "warning" : "neutral"}>
            {lifecycleLabel(t, pkg.lifecycle)}
          </StatusPill>
          <span className="text-[11.5px] text-muted-foreground">
            {t("settings.extensions.list.componentCount", {
              count: pkg.components.length,
              defaultValue: "{{count}} components",
            })}
          </span>
        </div>
      </button>
    </li>
  );
}

export function ExtensionsSettings({
  active = true,
  onOpenSection,
}: {
  active?: boolean;
  onOpenSection: (section: SettingsSectionKey) => void;
}) {
  const { t } = useTranslation();
  const controller = useExtensionsSettings(active);
  const {
    actionError,
    actionMessage,
    available,
    cancelRiskPrompt,
    clearFilters,
    confirmRiskPrompt,
    conflict,
    diagnostics,
    filters,
    loadError,
    loading,
    packages,
    pendingAction,
    refresh,
    reopenAfterConflict,
    riskPrompt,
    select,
    selected,
    setFilters,
    startAction,
    visiblePackages,
  } = controller;

  const update = (patch: Partial<ExtensionFilters>) =>
    setFilters((prev) => ({ ...prev, ...patch }));
  const filtered = hasActiveExtensionFilters(filters);

  // The warning is opened from code rather than from an AlertDialogTrigger, so Radix has
  // no trigger to hand focus back to. Remember the control the operator actually used, so
  // cancelling returns a keyboard or screen-reader user to where they were.
  const riskTriggerRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (riskPrompt && riskTriggerRef.current === null) {
      riskTriggerRef.current = document.activeElement as HTMLElement | null;
    }
  }, [riskPrompt]);
  const restoreRiskTriggerFocus = () => {
    const trigger = riskTriggerRef.current;
    riskTriggerRef.current = null;
    window.setTimeout(() => trigger?.focus(), 0);
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <SettingsSectionTitle>
            {t("settings.extensions.title", { defaultValue: "Extensions" })}
          </SettingsSectionTitle>
          <p className="mt-1 max-w-prose text-[12.5px] leading-5 text-muted-foreground">
            {t("settings.extensions.description", {
              defaultValue:
                "Every package this gateway has loaded, who owns each component, and what it is trusted to do. nanobot does not sandbox or verify third-party extensions.",
            })}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-9 gap-1.5 text-[12.5px]"
          disabled={loading}
          onClick={() => void refresh()}
        >
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
          )}
          {t("settings.extensions.refresh", { defaultValue: "Refresh" })}
        </Button>
      </div>

      {loadError ? (
        <p role="alert" className="text-[12.5px] leading-5 text-destructive">
          {loadError}
        </p>
      ) : null}

      {!available ? (
        <p role="status" className="text-[12.5px] leading-5 text-muted-foreground">
          {t("settings.extensions.registryUnavailable", {
            defaultValue:
              "This gateway composed no extension registry, so no inventory can be reported. This is not an empty host.",
          })}
        </p>
      ) : null}

      <DiagnosticsRegion diagnostics={diagnostics} />

      {actionError ? (
        <p role="alert" className="text-[12.5px] leading-5 text-destructive">
          {actionError}
        </p>
      ) : null}
      {!actionError && actionMessage ? (
        <p role="status" className="text-[12.5px] leading-5 text-muted-foreground">
          {actionMessage}
        </p>
      ) : null}

      <div className="space-y-2">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            type="search"
            value={filters.query}
            aria-label={t("settings.extensions.filters.search", {
              defaultValue: "Search extensions",
            })}
            placeholder={t("settings.extensions.filters.searchPlaceholder", {
              defaultValue: "Search packages and components",
            })}
            className={cn(SETTINGS_SEARCH_INPUT_CLASS, "h-9 pl-9 text-[12.5px]")}
            onChange={(event) => update({ query: event.target.value })}
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <FilterSelect
            label={t("settings.extensions.filters.kind", { defaultValue: "Family" })}
            value={filters.kind}
            options={availableComponentKinds(packages)}
            optionLabel={(option) => kindLabel(t, option)}
            onChange={(kind) => update({ kind })}
          />
          <FilterSelect
            label={t("settings.extensions.filters.source", { defaultValue: "Source" })}
            value={filters.source}
            options={availableSources(packages)}
            optionLabel={(option) => sourceLabel(t, option)}
            onChange={(source) => update({ source })}
          />
          <FilterSelect
            label={t("settings.extensions.filters.trust", { defaultValue: "Trust" })}
            value={filters.trust}
            options={availableTrust(packages)}
            optionLabel={(option) => trustLabel(t, option)}
            onChange={(trust) => update({ trust })}
          />
          <FilterSelect
            label={t("settings.extensions.filters.lifecycle", { defaultValue: "Lifecycle" })}
            value={filters.lifecycle}
            options={availableLifecycles(packages)}
            optionLabel={(option) => lifecycleLabel(t, option)}
            onChange={(lifecycle) => update({ lifecycle })}
          />
          {filtered ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-9 text-[12.5px]"
              onClick={clearFilters}
            >
              {t("settings.extensions.filters.clear", { defaultValue: "Clear filters" })}
            </Button>
          ) : null}
        </div>
        <p role="status" className="text-[12px] text-muted-foreground">
          {t("settings.extensions.resultCount", {
            shown: visiblePackages.length,
            total: packages.length,
            defaultValue: "Showing {{shown}} of {{total}} packages",
          })}
        </p>
      </div>

      <div className="grid min-w-0 grid-cols-1 gap-5 lg:grid-cols-[minmax(0,19rem)_minmax(0,1fr)]">
        <div className={cn("min-w-0", selected ? "hidden lg:block" : "block")}>
          {visiblePackages.length ? (
            <ul
              aria-label={t("settings.extensions.list.label", {
                defaultValue: "Installed extension packages",
              })}
              className="space-y-1"
            >
              {visiblePackages.map((pkg) => (
                <PackageListRow
                  key={pkg.id}
                  pkg={pkg}
                  selected={selected?.id === pkg.id}
                  onSelect={() => select(pkg.id)}
                />
              ))}
            </ul>
          ) : loading ? (
            <p className="text-[12.5px] text-muted-foreground">
              {t("settings.extensions.loading", { defaultValue: "Loading extensions…" })}
            </p>
          ) : (
            <p role="status" className="text-[12.5px] leading-5 text-muted-foreground">
              {filtered
                ? t("settings.extensions.emptyFiltered", {
                  defaultValue: "No package matches these filters.",
                })
                : available
                  ? t("settings.extensions.emptyHost", {
                    defaultValue: "This gateway reports no extension packages.",
                  })
                  : t("settings.extensions.registryUnavailableShort", {
                    defaultValue: "No inventory is available.",
                  })}
            </p>
          )}
        </div>

        <div className={cn("min-w-0", selected ? "block" : "hidden lg:block")}>
          {selected ? (
            <ExtensionPackageDetail
              pkg={selected}
              conflict={conflict?.targetId === selected.id ? conflict : null}
              pendingAction={pendingAction}
              onAction={startAction}
              onBack={() => select(null)}
              onOpenSection={onOpenSection}
              onReopenAfterConflict={reopenAfterConflict}
            />
          ) : (
            <p className="text-[12.5px] text-muted-foreground">
              {t("settings.extensions.detail.selectPrompt", {
                defaultValue: "Select a package to inspect its components and trust.",
              })}
            </p>
          )}
        </div>
      </div>

      <AlertDialog
        open={riskPrompt !== null}
        onOpenChange={(open) => {
          if (open) return;
          cancelRiskPrompt();
          restoreRiskTriggerFocus();
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-control bg-muted text-foreground">
              <ShieldAlert className="h-5 w-5" aria-hidden />
            </div>
            <AlertDialogTitle>
              {t("settings.extensions.risk.dialogTitle", {
                name: riskPrompt?.pkg.display_name ?? "",
                action: riskPrompt ? actionLabel(t, riskPrompt.action) : "",
                defaultValue: "{{action}} {{name}}?",
              })}
            </AlertDialogTitle>
            <AlertDialogDescription className="leading-6">
              {t("settings.extensions.risk.dialogBody", {
                trust: riskPrompt ? trustLabel(t, riskPrompt.pkg.trust) : "",
                isolation: riskPrompt ? isolationLabel(t, riskPrompt.pkg.isolated) : "",
                execution: riskPrompt ? executionLabel(t, riskPrompt.pkg.execution) : "",
                defaultValue:
                  "{{trust}} · {{isolation}} · {{execution}}. This code may read files, credentials, network, and any other resource visible to the nanobot process. Its declared permissions are self-declared and not enforced. nanobot does not verify this package or make it safe.",
              })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {riskPrompt ? (
            <p className="text-[12px] leading-5 text-muted-foreground">
              {t("settings.extensions.risk.revisionLabel", { defaultValue: "Revision" })}
              {": "}
              <code className="break-all font-mono text-[11.5px]">
                {riskPrompt.revision
                  || t("settings.extensions.detail.noRevision", {
                    defaultValue: "None reported",
                  })}
              </code>
            </p>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel className="w-full sm:w-auto">
              {t("common.cancel", { defaultValue: "Cancel" })}
            </AlertDialogCancel>
            <AlertDialogAction className="w-full sm:w-auto" onClick={confirmRiskPrompt}>
              {t("settings.extensions.risk.confirm", {
                action: riskPrompt ? actionLabel(t, riskPrompt.action) : "",
                defaultValue: "{{action}} anyway",
              })}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
