/* The site's current search box, run over a list of questions, for comparison.

   It runs the site's real browse.js, so there is no second copy of the search that
   could drift from it. That search keeps a source only when every word of the query
   appears in it, so most questions asked in ordinary words find nothing. That is the
   problem the prototype is trying to solve, and this is how it gets measured.

   node search/keyword_baseline.mjs <questions.json> <out.json>
     questions.json: [{"qid": "...", "question": "..."}]
     out.json:       [{"qid": "...", "ids": [...up to 10], "total": N}]  */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/* browse.js expects a browser. This gives it just enough of one: elements that
   remember the HTML written into them, a location carrying the query string, and a
   fetch that reads the data files off disk. Nothing is drawn; the rendered HTML is
   read back as text. */
async function browse(search) {
  const html = {}, nodes = {};
  const mk = (id) => ({
    id, _h: "", value: "", dataset: {}, textContent: "", attrs: {},
    set innerHTML(v) { this._h = v; html[id] = v; },
    get innerHTML() { return this._h; },
    addEventListener() {},
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    focus() {},
    getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
    style: { setProperty() {}, removeProperty() {} },
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    scrollIntoView() {}, insertAdjacentHTML() {}, closest() { return null; },
    parentNode: { removeChild() {} },
    querySelector() { return null; }, querySelectorAll() { return []; },
  });
  const document = {
    addEventListener: (e, f) => { if (e === "DOMContentLoaded") f(); },
    getElementById: (id) => (nodes[id] ||= mk(id)),
    querySelector: () => null,
    querySelectorAll: (s) => (s === "[data-generated]" ? [mk("g")] : []),
    title: "",
  };
  const win = {
    document,
    location: { protocol: "http:", pathname: "/browse.html", search },
    history: { replaceState() {} },
    addEventListener() {}, removeEventListener() {},
    URLSearchParams, URL, setTimeout: (f) => f(), clearTimeout() {}, console,
    fetch: async (u) => ({
      ok: true, status: 200,
      json: async () => JSON.parse(fs.readFileSync(path.join(SITE, u), "utf8")),
    }),
  };
  win.window = win;
  const ctx = vm.createContext(win);
  for (const file of ["assets/common.js", "assets/browse.js"]) {
    new vm.Script(fs.readFileSync(path.join(SITE, file), "utf8"), { filename: file })
      .runInContext(ctx);
  }
  await new Promise((r) => globalThis.setTimeout(r, 200));
  if (html["results"] === undefined) {
    throw new Error("browse.html rendered nothing for " + JSON.stringify(search));
  }
  return {
    // How many matched, which is not how many are drawn: the list shows one page.
    total: Number(nodes["resultbar"].getAttribute("data-total")),
    ids: [...(html["results"] || "").matchAll(/data-id="([^"]+)"/g)].map((m) => m[1]),
  };
}

const [, , inFile, outFile] = process.argv;
if (!inFile || !outFile) {
  console.error("usage: node keyword_baseline.mjs <questions.json> <out.json>");
  process.exit(1);
}

const questions = JSON.parse(fs.readFileSync(inFile, "utf8"));
const out = [];
for (const { qid, question } of questions) {
  const r = await browse("?q=" + encodeURIComponent(question));
  out.push({ qid, ids: r.ids.slice(0, 10), total: r.total });
}
fs.writeFileSync(outFile, JSON.stringify(out, null, 2));
console.log(`keyword baseline: ${out.length} questions, ${out.filter((o) => o.total === 0).length} found nothing`);
