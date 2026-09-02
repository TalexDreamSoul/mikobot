import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { KeyRound, Loader2, Save, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchLoginSecurity, updateLoginSecurity } from "@/lib/api";
import type { LoginSecuritySettings } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

export function LoginSecuritySettingsPanel() {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [form, setForm] = useState<LoginSecuritySettings | null>(null);
  const [secret, setSecret] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void fetchLoginSecurity(getToken()).then((payload) => {
      if (!cancelled) {
        setForm(payload.login_security);
        setError(null);
      }
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form || saving) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const payload = await updateLoginSecurity(client, {
        enabled: form.enabled,
        issuer: form.issuer,
        client_id: form.client_id,
        redirect_uri: form.redirect_uri,
        scopes: form.scopes,
        admin_subjects: form.admin_subjects,
        token_endpoint_auth_method: form.token_endpoint_auth_method,
        session_ttl_s: form.session_ttl_s,
        flow_ttl_s: form.flow_ttl_s,
        session_capacity: form.session_capacity,
        flow_capacity: form.flow_capacity,
        ...(secret ? { client_secret: secret } : {}),
      });
      setForm(payload.login_security);
      setSecret("");
      setSaved(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-48 items-center justify-center rounded-panel bg-settings-surface text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
        {t("settings.loginSecurity.loading")}
      </div>
    );
  }
  if (!form) {
    return (
      <div role="alert" className="rounded-panel border border-destructive/20 bg-destructive/5 p-5 text-sm text-destructive">
        {error ?? t("settings.loginSecurity.unavailable")}
      </div>
    );
  }

  const update = <K extends keyof LoginSecuritySettings>(key: K, value: LoginSecuritySettings[K]) => {
    setForm((current) => current ? { ...current, [key]: value } : current);
    setSaved(false);
  };

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-5">
      <section className="rounded-panel bg-settings-surface p-5">
        <div className="flex items-start gap-3">
          <ShieldCheck className="mt-0.5 h-5 w-5 text-primary" aria-hidden />
          <div>
            <h2 className="text-lg font-semibold">{t("settings.loginSecurity.title")}</h2>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              {t("settings.loginSecurity.description")}
            </p>
          </div>
        </div>
        <label className="mt-5 flex items-center justify-between gap-4 rounded-control border border-border/50 bg-background px-4 py-3">
          <span>
            <span className="block text-sm font-medium">{t("settings.loginSecurity.enable")}</span>
            <span className="text-xs text-muted-foreground">{t("settings.loginSecurity.enableHelp")}</span>
          </span>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(event) => update("enabled", event.target.checked)}
            className="h-5 w-5 accent-primary"
          />
        </label>
      </section>

      <section className="grid gap-4 rounded-panel bg-settings-surface p-5 sm:grid-cols-2">
        <Field label={t("settings.loginSecurity.issuer")}>
          <Input value={form.issuer} onChange={(event) => update("issuer", event.target.value)} placeholder="https://id.example.com" />
        </Field>
        <Field label={t("settings.loginSecurity.clientId")}>
          <Input value={form.client_id} onChange={(event) => update("client_id", event.target.value)} autoComplete="off" />
        </Field>
        <Field label={t("settings.loginSecurity.redirectUri")}>
          <Input value={form.redirect_uri} onChange={(event) => update("redirect_uri", event.target.value)} placeholder="https://nanobot.example.com/auth/oidc/callback" />
        </Field>
        <Field label={t("settings.loginSecurity.clientSecret")}>
          <Input
            type="password"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            placeholder={form.client_secret_configured
              ? t("settings.loginSecurity.secretConfigured")
              : t("settings.loginSecurity.secretOptional")}
            autoComplete="new-password"
          />
        </Field>
        <Field label={t("settings.loginSecurity.authMethod")}>
          <select
            value={form.token_endpoint_auth_method}
            onChange={(event) => update(
              "token_endpoint_auth_method",
              event.target.value as LoginSecuritySettings["token_endpoint_auth_method"],
            )}
            className="h-11 w-full rounded-control border border-input bg-background px-3 text-sm"
          >
            <option value="none">none (PKCE public client)</option>
            <option value="client_secret_basic">client_secret_basic</option>
            <option value="client_secret_post">client_secret_post</option>
          </select>
        </Field>
        <Field label={t("settings.loginSecurity.scopes")}>
          <Input
            value={form.scopes.join(" ")}
            onChange={(event) => update("scopes", event.target.value.split(/\s+/).filter(Boolean))}
            placeholder="openid profile email"
          />
        </Field>
        <Field label={t("settings.loginSecurity.adminSubjects")} wide>
          <Input
            value={form.admin_subjects.join(", ")}
            onChange={(event) => update(
              "admin_subjects",
              event.target.value.split(",").map((value) => value.trim()).filter(Boolean),
            )}
            placeholder={t("settings.loginSecurity.adminSubjectsPlaceholder")}
          />
          <p className="mt-1.5 text-xs text-muted-foreground">{t("settings.loginSecurity.adminSubjectsHelp")}</p>
        </Field>
      </section>

      <section className="grid gap-4 rounded-panel bg-settings-surface p-5 sm:grid-cols-2 lg:grid-cols-4">
        <NumberField label={t("settings.loginSecurity.sessionTtl")} value={form.session_ttl_s} min={60} max={86400} onChange={(value) => update("session_ttl_s", value)} />
        <NumberField label={t("settings.loginSecurity.flowTtl")} value={form.flow_ttl_s} min={30} max={600} onChange={(value) => update("flow_ttl_s", value)} />
        <NumberField label={t("settings.loginSecurity.sessionCapacity")} value={form.session_capacity} min={1} max={10000} onChange={(value) => update("session_capacity", value)} />
        <NumberField label={t("settings.loginSecurity.flowCapacity")} value={form.flow_capacity} min={1} max={10000} onChange={(value) => update("flow_capacity", value)} />
      </section>

      <section className="rounded-panel border border-border/50 bg-settings-surface p-5">
        <div className="flex items-start gap-3">
          <KeyRound className="mt-0.5 h-5 w-5 text-muted-foreground" aria-hidden />
          <p className="text-sm leading-6 text-muted-foreground">
            {t("settings.loginSecurity.oauthSummary")}
          </p>
        </div>
      </section>

      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      {saved ? <p role="status" className="text-sm text-primary">{t("settings.loginSecurity.saved")}</p> : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={saving}>
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : <Save className="mr-2 h-4 w-4" aria-hidden />}
          {saving ? t("settings.loginSecurity.validating") : t("settings.loginSecurity.save")}
        </Button>
      </div>
    </form>
  );
}

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={wide ? "sm:col-span-2" : undefined}>
      <span className="mb-1.5 block text-xs font-medium">{label}</span>
      {children}
    </label>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span className="mb-1.5 block text-xs font-medium">{label}</span>
      <Input
        type="number"
        value={value}
        min={min}
        max={max}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
