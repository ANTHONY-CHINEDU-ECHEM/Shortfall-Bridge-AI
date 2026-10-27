// Bundles the backend's shortfall_bridge package into public/py/shortfall_bridge.json so the static
// site can mount it inside Pyodide. The browser runs exactly this source: there is no JavaScript port
// of the clinical engine. Run automatically by `npm run build:demo` (and therefore by Netlify).
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pkg = resolve(here, "../../backend/shortfall_bridge");
const out = resolve(here, "../public/py/shortfall_bridge.json");
const KEEP = /\.(py|yaml|sql)$/;

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    if (name === "__pycache__" || name.startsWith(".")) return [];
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : KEEP.test(name) ? [p] : [];
  });
}

const files = {};
const hash = createHash("sha256");
for (const f of walk(pkg).sort()) {
  const rel = `shortfall_bridge/${relative(pkg, f).split("\\").join("/")}`;
  const text = readFileSync(f, "utf8");
  files[rel] = text;
  hash.update(rel).update("\0").update(text);
}
const version = /__version__\s*=\s*"([^"]+)"/.exec(files["shortfall_bridge/__init__.py"] ?? "")?.[1] ?? "unknown";
mkdirSync(dirname(out), { recursive: true });
const bundle = { version: `${version}+${hash.digest("hex").slice(0, 10)}`, built_at: new Date().toISOString(), files };
writeFileSync(out, JSON.stringify(bundle));
const kb = Math.round(statSync(out).size / 1024);
console.log(`bundled ${Object.keys(files).length} files (${kb} KB) as ${bundle.version} -> ${relative(process.cwd(), out)}`);
