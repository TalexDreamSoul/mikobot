import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  EXTENSION_KIND_VALUES,
  EXTENSION_LIFECYCLE_VALUES,
  EXTENSION_SOURCE_VALUES,
  EXTENSION_TRUST_VALUES,
} from "@/components/settings/extensions/extensionLabels";

/**
 * Read the raw locale files, not the merged i18n resources.
 *
 * `mergeLocaleMessages` deep-merges English underneath every other locale, so a
 * missing Simplified Chinese key resolves to the English string at runtime and a
 * resource-shape check can never see it. The files are the only place the gap shows.
 */
function rawLocale(locale: string): Record<string, unknown> {
  const path = resolve(__dirname, `../i18n/locales/${locale}/common.json`);
  return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}

function flatten(value: unknown, prefix = ""): Map<string, string> {
  const flat = new Map<string, string>();
  if (typeof value === "string") {
    flat.set(prefix, value);
    return flat;
  }
  if (value && typeof value === "object") {
    for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
      for (const [path, leaf] of flatten(nested, prefix ? `${prefix}.${key}` : key)) {
        flat.set(path, leaf);
      }
    }
  }
  return flat;
}

function extensionsBlock(locale: string): Map<string, string> {
  const settings = rawLocale(locale).settings as Record<string, unknown>;
  expect(settings.extensions, `${locale} is missing settings.extensions`).toBeDefined();
  return flatten(settings.extensions);
}

const EN = extensionsBlock("en");
const ZH = extensionsBlock("zh-CN");

// Chinese has one plural category, so English `_one` variants have no counterpart.
const ENGLISH_ONLY_SUFFIXES = ["_one"];

// Bare product and protocol names stay identical in both locales on purpose.
const SHARED_ACROSS_LOCALES = new Set(["kind.mcp_server"]);

describe("Extensions locale coverage", () => {
  it("declares a Simplified Chinese entry for every English key", () => {
    const missing = [...EN.keys()].filter(
      (key) => !ZH.has(key) && !ENGLISH_ONLY_SUFFIXES.some((suffix) => key.endsWith(suffix)),
    );
    expect(missing).toEqual([]);
  });

  it("declares no Simplified Chinese key the English surface does not use", () => {
    const orphaned = [...ZH.keys()].filter((key) => !EN.has(key));
    expect(orphaned).toEqual([]);
  });

  it("actually translates every entry instead of copying the English string", () => {
    const untranslated = [...ZH.entries()]
      .filter(([key, value]) => !SHARED_ACROSS_LOCALES.has(key) && EN.get(key) === value)
      .map(([key]) => key);
    expect(untranslated).toEqual([]);
  });

  it("keeps every interpolation placeholder that the English string declares", () => {
    const placeholders = (text: string) => (text.match(/\{\{[a-zA-Z_]+\}\}/g) ?? []).sort();
    const mismatched = [...ZH.entries()]
      .filter(([key, value]) => {
        const english = EN.get(key);
        if (english === undefined) return false;
        return placeholders(english).join("|") !== placeholders(value).join("|");
      })
      .map(([key]) => key);
    expect(mismatched).toEqual([]);
  });

  it("names every canonical enum value the gateway can send", () => {
    for (const [group, values] of [
      ["lifecycle", EXTENSION_LIFECYCLE_VALUES],
      ["trust", EXTENSION_TRUST_VALUES],
      ["source", EXTENSION_SOURCE_VALUES],
      ["kind", EXTENSION_KIND_VALUES],
    ] as const) {
      for (const value of values) {
        expect(EN.get(`${group}.${value}`), `en ${group}.${value}`).toBeTruthy();
        expect(ZH.get(`${group}.${value}`), `zh-CN ${group}.${value}`).toBeTruthy();
      }
    }
  });

  it("labels the extensions navigation entry in both locales", () => {
    const en = flatten((rawLocale("en").settings as Record<string, unknown>).nav);
    const zh = flatten((rawLocale("zh-CN").settings as Record<string, unknown>).nav);
    expect(en.get("extensions")).toBe("Extensions");
    expect(zh.get("extensions")).toBeTruthy();
    expect(zh.get("extensions")).not.toBe(en.get("extensions"));
  });
});
