#!/usr/bin/env node
/**
 * Standalone TypeScript check for the single-file UIs — no npm required.
 *
 * Every Zodiac Bank UI is a single self-contained HTML file (the offline
 * evaluation harness asserts on strings inside those files, so the JS cannot
 * be split out). Type safety is still enforced: this script extracts each
 * inline <script>, writes it to a temp file, and runs the TypeScript compiler
 * in checkJs mode (tsc --noEmit --allowJs --checkJs). No runtime dependency,
 * no bundler, no build step — the shipped HTML stays vanilla JS.
 *
 * It needs the `tsc` binary, resolved in this order:
 *   1. $TSC                  (explicit path, e.g. a TypeScript install)
 *   2. tsc on $PATH          (global/standalone TypeScript)
 *
 * Usage:
 *   node scripts/check_ui_types.mjs            # check every UI
 *   node scripts/check_ui_types.mjs --file apps/aurora/index.html
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const UIS = [
  "training-challenges/index.html",
  "apps/aurora/index.html",
  "apps/phoenix/index.html",
  "apps/assistant/index.html",
];

/** Extract every inline <script> (no src attribute) body from an HTML file. */
function extractScripts(htmlPath) {
  const html = readFileSync(htmlPath, "utf-8");
  const bodies = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let match;
  while ((match = re.exec(html)) !== null) {
    if (match[1] && match[1].trim()) bodies.push(match[1]);
  }
  return bodies;
}

/** Locate the TypeScript compiler: $TSC, then tsc on PATH. */
function findTsc() {
  if (process.env.TSC) return process.env.TSC;
  try {
    execFileSync("tsc", ["--version"], { stdio: "ignore" });
    return "tsc";
  } catch {
    console.error("tsc not found — install TypeScript or set $TSC to the tsc binary.");
    process.exit(2);
  }
}

const files = [];
const argFile = process.argv.indexOf("--file");
if (argFile !== -1 && process.argv[argFile + 1]) {
  files.push(process.argv[argFile + 1]);
} else {
  files.push(...UIS);
}

const tsc = findTsc();
const tmp = mkdtempSync(join(tmpdir(), "zb-ui-types-"));
let failed = false;

try {
  for (const rel of files) {
    const abs = resolve(ROOT, rel);
    const scripts = extractScripts(abs);
    if (!scripts.length) {
      console.log(`[skip] ${rel} — no inline scripts found`);
      continue;
    }
    scripts.forEach((body, i) => {
      const out = join(tmp, `${rel.replace(/[\\/]/g, "_")}.${i}.js`);
      writeFileSync(out, body + "\n");
      try {
        execFileSync(tsc, [
          "--noEmit", "--allowJs", "--checkJs",
          "--target", "ES2020", "--lib", "ES2020,DOM,DOM.Iterable",
          "--module", "ESNext", "--moduleResolution", "bundler",
          "--skipLibCheck",
          out,
        ], { stdio: ["ignore", "pipe", "pipe"] });
        console.log(`[ok]   ${rel}${scripts.length > 1 ? ` [${i + 1}/${scripts.length}]` : ""}`);
      } catch (error) {
        failed = true;
        console.error(`[FAIL] ${rel}${scripts.length > 1 ? ` [${i + 1}/${scripts.length}]` : ""}`);
        const detail = error.stdout ? Buffer.from(error.stdout).toString("utf-8") : String(error.message);
        console.error(detail.trim());
      }
    });
  }
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

process.exit(failed ? 1 : 0);
