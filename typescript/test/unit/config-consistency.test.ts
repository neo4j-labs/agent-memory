/**
 * Guards the build/docs configs against silent path drift.
 *
 * TypeDoc does NOT fail on a nonexistent entryPoint: it exits 0, emits no
 * warning naming the missing file, and simply omits that module from the
 * generated documentation. So a rename or a file move can delete a whole
 * module from the published API reference with nothing anywhere going red.
 * That happened once already — `src/integrations/strands.ts` became
 * `src/integrations/strands/index.ts` and typedoc.json kept pointing at the
 * old path, which an automated reviewer caught rather than CI.
 *
 * These tests are that missing red light. They run in the existing unit-test
 * job, so no workflow change is needed.
 */

import { describe, it, expect } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import tsupConfig from "../../tsup.config.js";

const pkgRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function typedocEntryPoints(): string[] {
  const raw = readFileSync(resolve(pkgRoot, "typedoc.json"), "utf8");
  const parsed = JSON.parse(raw) as { entryPoints?: string[] };
  expect(parsed.entryPoints, "typedoc.json must declare entryPoints").toBeDefined();
  return parsed.entryPoints!;
}

/** tsup's `entry` map: output-path key -> source-file value. */
function tsupEntrySources(): string[] {
  const entry = (tsupConfig as { entry?: Record<string, string> }).entry;
  expect(entry, "tsup.config.ts must declare an entry map").toBeDefined();
  return Object.values(entry!);
}

describe("typedoc.json", () => {
  it("points every entryPoint at a file that exists", () => {
    const missing = typedocEntryPoints().filter(
      (entryPoint) => !existsSync(resolve(pkgRoot, entryPoint)),
    );

    expect(
      missing,
      "typedoc silently omits modules whose entryPoint does not resolve, so these " +
        "would vanish from the published API docs with no error:",
    ).toEqual([]);
  });
});

describe("tsup.config.ts", () => {
  it("points every entry at a file that exists", () => {
    const missing = tsupEntrySources().filter(
      (source) => !existsSync(resolve(pkgRoot, source)),
    );

    expect(missing, "tsup entries that do not resolve:").toEqual([]);
  });
});

describe("typedoc and tsup", () => {
  it("document exactly the modules that get built", () => {
    // Both configs enumerate the package's public entry modules, so they must
    // agree. A mismatch means either a new public module ships undocumented,
    // or the docs reference something no longer built.
    expect(new Set(typedocEntryPoints())).toEqual(new Set(tsupEntrySources()));
  });
});
