import { AlertTriangle, ArrowLeft, ExternalLink, Loader2, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { SettingsSectionKey } from "@/components/settings/contracts";
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
  actionableExtensionActions,
  disclosesExecutableRisk,
  extensionConfigurationSection,
} from "@/components/settings/extensions/extensionModel";
import type { ExtensionConflict } from "@/components/settings/extensions/useExtensionsSettings";
import { StatusPill } from "@/components/settings/shared/SettingsControls";
import { Button } from "@/components/ui/button";
import type {
  ExtensionComponent,
  ExtensionConfigurationTarget,
  ExtensionPackage,
  NanobotExtensionAction,
} from "@/lib/types";
import { cn } from "@/lib/utils";

function DetailField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="mt-0.5 break-words text-[13px] leading-5 text-foreground">{children}</dd>
    </div>
  );
}

function ConfigurationLink({
  configuration,
  onOpenSection,
}: {
  configuration: ExtensionConfigurationTarget | null;
  onOpenSection: (section: SettingsSectionKey) => void;
}) {
  const { t } = useTranslation();
  const section = extensionConfigurationSection(configuration);
  if (!configuration) return null;
  if (!section) {
    // An unmapped destination degrades to plain text rather than a broken navigation.
    return (
      <span className="text-[12px] text-muted-foreground">
        {t("settings.extensions.detail.configureUnavailable", {
          defaultValue: "No configuration page is available for this destination.",
        })}
      </span>
    );
  }
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="h-8 gap-1.5 text-[12.5px]"
      onClick={() => onOpenSection(section)}
    >
      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
      {t("settings.extensions.detail.configureIn", {
        section: t(`settings.nav.${section}`, { defaultValue: section }),
        defaultValue: "Open {{section}}",
      })}
    </Button>
  );
}

function ComponentRow({
  component,
  onOpenSection,
}: {
  component: ExtensionComponent;
  onOpenSection: (section: SettingsSectionKey) => void;
}) {
  const { t } = useTranslation();
  return (
    <li className="rounded-control border border-border/50 bg-settings-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-[13px] font-medium text-foreground">{component.display_name}</span>
        <StatusPill>{kindLabel(t, component.kind)}</StatusPill>
        <StatusPill tone={component.lifecycle === "failed" ? "warning" : "neutral"}>
          {lifecycleLabel(t, component.lifecycle)}
        </StatusPill>
      </div>
      {component.description ? (
        <p className="mt-1 text-[12px] leading-5 text-muted-foreground">{component.description}</p>
      ) : null}
      <dl className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <DetailField
          label={t("settings.extensions.detail.execution", { defaultValue: "Runs" })}
        >
          {executionLabel(t, component.execution)}
        </DetailField>
        <DetailField
          label={t("settings.extensions.detail.identity", { defaultValue: "Extension ID" })}
        >
          <code className="break-all font-mono text-[11.5px]">{component.id}</code>
        </DetailField>
        {component.capabilities.length ? (
          <DetailField
            label={t("settings.extensions.detail.capabilities", { defaultValue: "Capabilities" })}
          >
            {component.capabilities.join(", ")}
          </DetailField>
        ) : null}
        {component.actions.length ? (
          <DetailField
            label={t("settings.extensions.detail.supportedActions", {
              defaultValue: "Supported actions",
            })}
          >
            {component.actions.map((action) => actionLabel(t, action)).join(", ")}
          </DetailField>
        ) : null}
      </dl>
      {component.diagnostic ? (
        <p className="mt-2 text-[12px] leading-5 text-amber-700 dark:text-amber-300">
          {component.diagnostic.message}
        </p>
      ) : null}
      <div className="mt-2">
        <ConfigurationLink
          configuration={component.configuration}
          onOpenSection={onOpenSection}
        />
      </div>
    </li>
  );
}

export function ExtensionPackageDetail({
  pkg,
  conflict,
  pendingAction,
  onAction,
  onBack,
  onOpenSection,
  onReopenAfterConflict,
}: {
  pkg: ExtensionPackage;
  conflict: ExtensionConflict | null;
  pendingAction: string | null;
  onAction: (pkg: ExtensionPackage, action: NanobotExtensionAction) => void;
  onBack: () => void;
  onOpenSection: (section: SettingsSectionKey) => void;
  onReopenAfterConflict: () => void;
}) {
  const { t } = useTranslation();
  const actions = actionableExtensionActions(pkg.actions);
  const executableRisk = disclosesExecutableRisk(pkg);
  const blockedByConflict = conflict !== null;

  return (
    <section
      aria-label={t("settings.extensions.detail.label", { defaultValue: "Extension details" })}
      className="min-w-0 space-y-4"
    >
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="-ml-2 h-8 gap-1.5 text-[12.5px] lg:hidden"
        onClick={onBack}
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        {t("settings.extensions.detail.back", { defaultValue: "Back to all extensions" })}
      </Button>

      <header className="min-w-0">
        <h3 className="text-[15px] font-semibold text-foreground">{pkg.display_name}</h3>
        {pkg.description ? (
          <p className="mt-1 text-[12.5px] leading-5 text-muted-foreground">{pkg.description}</p>
        ) : null}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <StatusPill>{sourceLabel(t, pkg.source)}</StatusPill>
          <StatusPill tone={pkg.lifecycle === "failed" ? "warning" : "neutral"}>
            {lifecycleLabel(t, pkg.lifecycle)}
          </StatusPill>
          <StatusPill>{trustLabel(t, pkg.trust)}</StatusPill>
        </div>
      </header>

      {executableRisk ? (
        <div
          role="note"
          className="flex items-start gap-3 rounded-control border border-amber-500/40 bg-amber-500/8 px-3.5 py-3"
        >
          <ShieldAlert
            className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-300"
            aria-hidden
          />
          <div className="min-w-0">
            <p className="text-[12.5px] font-medium text-foreground">
              {t("settings.extensions.risk.title", {
                defaultValue: "Unisolated third-party code",
              })}
            </p>
            <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
              {t("settings.extensions.risk.body", {
                trust: trustLabel(t, pkg.trust),
                isolation: isolationLabel(t, pkg.isolated),
                execution: executionLabel(t, pkg.execution),
                defaultValue:
                  "{{trust}} · {{isolation}} · {{execution}}. This code may read files, credentials, network, and any other resource visible to the nanobot process. nanobot does not verify it or make it safe.",
              })}
            </p>
          </div>
        </div>
      ) : null}

      {pkg.execution === "data" ? (
        <p className="text-[12px] leading-5 text-muted-foreground">
          {t("settings.extensions.dataOnly", {
            defaultValue:
              "Data only — this package runs no code. Its content still reaches the model's prompt.",
          })}
        </p>
      ) : null}

      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <DetailField label={t("settings.extensions.detail.identity", { defaultValue: "Extension ID" })}>
          <code className="break-all font-mono text-[11.5px]">{pkg.id}</code>
        </DetailField>
        <DetailField label={t("settings.extensions.detail.source", { defaultValue: "Source" })}>
          {sourceLabel(t, pkg.source)}
        </DetailField>
        <DetailField label={t("settings.extensions.detail.trust", { defaultValue: "Trust" })}>
          {trustLabel(t, pkg.trust)}
        </DetailField>
        <DetailField label={t("settings.extensions.detail.execution", { defaultValue: "Runs" })}>
          {executionLabel(t, pkg.execution)}
        </DetailField>
        <DetailField
          label={t("settings.extensions.detail.isolation", { defaultValue: "Isolation" })}
        >
          {isolationLabel(t, pkg.isolated)}
        </DetailField>
        <DetailField
          label={t("settings.extensions.detail.lifecycle", { defaultValue: "Lifecycle" })}
        >
          {lifecycleLabel(t, pkg.lifecycle)}
        </DetailField>
        {pkg.version ? (
          <DetailField label={t("settings.extensions.detail.version", { defaultValue: "Version" })}>
            {pkg.version}
          </DetailField>
        ) : null}
        <DetailField
          label={t("settings.extensions.detail.revision", { defaultValue: "Revision" })}
        >
          <code className="break-all font-mono text-[11.5px]">
            {pkg.revision
              ?? t("settings.extensions.detail.noRevision", { defaultValue: "None reported" })}
          </code>
        </DetailField>
      </dl>

      <div>
        <h4 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("settings.extensions.detail.permissions", { defaultValue: "Declared permissions" })}
        </h4>
        {pkg.permissions.length ? (
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {pkg.permissions.map((permission) => (
              <li key={permission}>
                <StatusPill>{permission}</StatusPill>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[12px] text-muted-foreground">
            {t("settings.extensions.detail.permissionsNone", {
              defaultValue: "This package declares no permissions.",
            })}
          </p>
        )}
        <p className="mt-1.5 text-[12px] leading-5 text-muted-foreground">
          {pkg.permissions_enforced
            ? t("settings.extensions.detail.permissionsEnforced", {
              defaultValue: "These permissions are enforced by nanobot.",
            })
            : t("settings.extensions.detail.permissionsUnenforced", {
              defaultValue:
                "Self-declared by the package and not enforced. nanobot does not restrict what this extension can reach.",
            })}
        </p>
      </div>

      <div>
        <h4 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("settings.extensions.detail.supportedActions", {
            defaultValue: "Supported actions",
          })}
        </h4>
        {pkg.actions.length ? (
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {pkg.actions.map((action) => (
              <li key={action}>
                <StatusPill>{actionLabel(t, action)}</StatusPill>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[12px] text-muted-foreground">
            {t("settings.extensions.detail.noActions", {
              defaultValue: "This package's owner declares no lifecycle action here.",
            })}
          </p>
        )}
      </div>

      {conflict ? (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-control border border-border/55 bg-muted/25 px-3.5 py-3"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="min-w-0">
            <p className="text-[12.5px] font-medium text-foreground">
              {t("settings.extensions.conflict.title", {
                defaultValue: "This extension changed",
              })}
            </p>
            <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">
              {conflict.message
                || t("settings.extensions.conflict.body", {
                  defaultValue:
                    "The host reports a different revision than the one shown. Reopen the details and decide again.",
                })}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2 h-8 text-[12.5px]"
              onClick={onReopenAfterConflict}
            >
              {t("settings.extensions.conflict.reopen", { defaultValue: "Reload details" })}
            </Button>
          </div>
        </div>
      ) : null}

      {actions.length ? (
        <div className="flex flex-wrap gap-2">
          {actions.map((action) => {
            const busy = pendingAction === `${action}:${pkg.id}`;
            return (
              <Button
                key={action}
                type="button"
                variant={action === "uninstall" ? "destructive" : "outline"}
                size="sm"
                className={cn("h-9 gap-1.5 text-[12.5px]")}
                disabled={busy || blockedByConflict}
                onClick={() => onAction(pkg, action)}
              >
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                {actionLabel(t, action)}
              </Button>
            );
          })}
        </div>
      ) : null}

      <div>
        <ConfigurationLink configuration={pkg.configuration} onOpenSection={onOpenSection} />
      </div>

      {pkg.diagnostic ? (
        <p role="status" className="text-[12px] leading-5 text-amber-700 dark:text-amber-300">
          {pkg.diagnostic.message}
        </p>
      ) : null}

      <div>
        <h4 className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">
          {t("settings.extensions.detail.components", {
            count: pkg.components.length,
            defaultValue: "Components ({{count}})",
          })}
        </h4>
        {pkg.components.length ? (
          <ul className="mt-2 space-y-2">
            {pkg.components.map((component) => (
              <ComponentRow
                key={component.id}
                component={component}
                onOpenSection={onOpenSection}
              />
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[12px] text-muted-foreground">
            {t("settings.extensions.detail.componentsNone", {
              defaultValue: "This package contributes no components right now.",
            })}
          </p>
        )}
      </div>
    </section>
  );
}
