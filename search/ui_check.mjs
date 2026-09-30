/* Walk through the demo in a real browser, and save a screenshot of each step.

     python search/serve.py        (in one window, and leave it running)
     node search/ui_check.mjs      (in another)

   Drives a headless Chrome or Edge through the demo questions: asks, waits for the
   model's answer, removes a tag and puts it back, opens a result, and leaves for a
   source's page and comes back. Screenshots go to results/screens/.

   Fails, with the reason, if the page throws a script error, if an answer never
   arrives, or if what arrives is not what the step expects. Worth running the
   morning of the demo: it catches a stopped model before anyone is watching.

   Talks to the browser over the DevTools protocol with Node's built-in WebSocket,
   so it needs no packages. */
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.join(HERE, "results", "screens");
const BASE = process.env.ASK_URL || "http://127.0.0.1:8765/";
const BROWSERS = [
  process.env.CHROME,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
].filter(Boolean);

const problems = [];
const log = (...a) => console.log(...a);

// ---------- the browser ----------

async function launch() {
  const exe = BROWSERS.find((p) => fs.existsSync(p));
  if (!exe) throw new Error("No Chrome or Edge found. Set CHROME to its path.");
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "ask-check-"));
  const child = spawn(exe, ["--headless=new", "--disable-gpu", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
    `--user-data-dir=${profile}`, "about:blank"], { stdio: "ignore" });

  // With port 0 the browser picks a free port and writes it to this file.
  const portFile = path.join(profile, "DevToolsActivePort");
  for (let i = 0; i < 100 && !fs.existsSync(portFile); i++) await sleep(100);
  if (!fs.existsSync(portFile)) throw new Error("The browser did not start.");
  const port = fs.readFileSync(portFile, "utf8").split("\n")[0].trim();

  let target;
  for (let i = 0; i < 50 && !target; i++) {
    const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    target = list.find((t) => t.type === "page");
    if (!target) await sleep(100);
  }
  return { child, profile, wsUrl: target.webSocketDebuggerUrl };
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let next = 0;
  const waiting = new Map();
  const listeners = [];
  ws.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && waiting.has(msg.id)) {
      const { resolve, reject } = waiting.get(msg.id);
      waiting.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    } else if (msg.method) {
      listeners.forEach((fn) => fn(msg));
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++next;
    waiting.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const opened = new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve);
    ws.addEventListener("error", reject);
  });
  return { ws, send, on: (fn) => listeners.push(fn), opened };
}

// ---------- steps ----------

let cdp;

async function evaluate(expression) {
  const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error("In the page: " + r.exceptionDetails.text);
  return r.result.value;
}

async function waitFor(expression, what, timeout = 60000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await evaluate(expression)) return;
    await sleep(150);
  }
  throw new Error(`Timed out after ${timeout / 1000}s waiting for ${what}.`);
}

function nextLoad() {
  return new Promise((resolve) => cdp.on((m) => { if (m.method === "Page.loadEventFired") resolve(); }));
}

async function open(url) {
  const loaded = nextLoad();
  await cdp.send("Page.navigate", { url });
  await loaded;
}

async function back() {
  const { currentIndex, entries } = await cdp.send("Page.getNavigationHistory");
  const loaded = nextLoad();
  await cdp.send("Page.navigateToHistoryEntry", { entryId: entries[currentIndex - 1].id });
  // A page restored from the back-forward cache fires no load event, so do not wait
  // on it for ever.
  await Promise.race([loaded, sleep(3000)]);
}

async function shot(name) {
  const { cssContentSize } = await cdp.send("Page.getLayoutMetrics");
  const height = Math.ceil(Math.min(cssContentSize.height, 6000));
  const { data } = await cdp.send("Page.captureScreenshot", {
    format: "png", captureBeyondViewport: true,
    clip: { x: 0, y: 0, width: 1280, height, scale: 1 },
  });
  const file = path.join(SHOTS, name);
  fs.writeFileSync(file, Buffer.from(data, "base64"));
  log(`   saved ${path.relative(process.cwd(), file)}`);
}

const FINISHED = `!document.getElementById("clock") &&
  (!document.getElementById("answer").hidden || !!document.querySelector("#reading .error"))`;

const READ = `(() => ({
  tags: [...document.querySelectorAll(".ask-tags dd")].map(d => d.previousElementSibling.textContent + ": " +
          [...d.querySelectorAll(".ask-chip")].map(c => c.firstChild.textContent).join(" or ")),
  bar: document.getElementById("resultbar").textContent.trim(),
  ids: [...document.querySelectorAll("#results .result__head")].map(b => b.dataset.id),
  first: (document.querySelector("#results .ask-match") || {}).textContent || "",
  reasoning: (document.querySelector(".ask-why p") || {}).textContent || "",
  meta: (document.querySelector(".ask-meta") || {}).textContent || "",
  error: (document.querySelector("#reading .error") || {}).textContent || ""
}))()`;

async function askExample(fragment) {
  const found = await evaluate(`(() => {
    const b = [...document.querySelectorAll(".ask-example")].find(x => x.textContent.includes(${JSON.stringify(fragment)}));
    if (!b) return false; b.click(); return true; })()`);
  if (!found) throw new Error(`No example button containing "${fragment}".`);
  await waitFor(`!!document.getElementById("clock")`, "the question to start", 10000);
}

/* Two kinds of check. expect() is about the page: if it fails, something is broken.
   note() is about what the model answered: it varies from run to run even at
   temperature 0, so a miss there is something to know before presenting, not a
   fault in the page. */
const notes = [];

function expect(condition, message) {
  if (!condition) problems.push(message);
  log(`   ${condition ? "ok  " : "FAIL"} ${message}`);
}

function note(condition, message) {
  if (!condition) notes.push(message);
  log(`   ${condition ? "ok  " : "NOTE"} ${message}`);
}

async function chipCount() {
  return evaluate(`document.querySelectorAll(".ask-chip").length`);
}

async function removeChip(group, value) {
  const count = await chipCount();
  const clicked = await evaluate(`(() => {
    const x = [...document.querySelectorAll(".ask-chip__x")].find(b =>
      b.dataset.group === ${JSON.stringify(group)} && b.dataset.value === ${JSON.stringify(value)});
    if (!x) return false; x.click(); return true; })()`);
  if (!clicked) throw new Error(`No ${group} tag "${value}" to remove.`);
  await waitFor(`document.querySelectorAll(".ask-chip").length === ${count - 1}`,
    `the ${value} tag to go`, 10000);
}

async function run() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const status = await fetch(BASE + "api/status").then((r) => r.json()).catch(() => null);
  if (!status) throw new Error(`The search page is not running at ${BASE}. Start serve.py first.`);
  if (!status.ok) log(`Note: the model is not available (${status.message}). Answers will be ranked by words.`);

  const browser = await launch();
  cdp = connect(browser.wsUrl);
  await cdp.opened;
  cdp.on((m) => {
    if (m.method === "Runtime.exceptionThrown") {
      problems.push("Script error: " + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
    } else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      problems.push("Console error: " + m.params.args.map((a) => a.value ?? a.description).join(" "));
    } else if (m.method === "Log.entryAdded" && m.params.entry.level === "error") {
      problems.push("Browser error: " + m.params.entry.text + (m.params.entry.url ? " " + m.params.entry.url : ""));
    }
  });
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Log.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });

  try {
    log("1. The empty page");
    await open(BASE + "ask.html");
    await waitFor(`!document.getElementById("examples").hidden`, "the example questions");
    const nav = await evaluate(`document.querySelector('.site-nav a[href="ask.html"]').getAttribute("aria-current")`);
    expect(nav === "page", `"Ask a question" is the highlighted menu item`);
    await shot("01-start.png");

    log("2. Walkability, while the model is writing");
    await askExample("walkability");
    await waitFor(`!!document.querySelector(".ask-caret")`, "the reasoning to start streaming", 30000);
    await sleep(700);
    await shot("02-streaming.png");

    log("3. Walkability, answered");
    await waitFor(FINISHED, "the answer", 90000);
    let seen = await evaluate(READ);
    log(`   tags: ${seen.tags.join(" | ")}`);
    log(`   ${seen.bar}`);
    log(`   ${seen.meta}`);
    expect(!seen.error, "no error shown");
    const walk = ["app6-01", "app6-02", "app6-04"];
    note(seen.tags.some((t) => t.includes("Walkability Index")), "the model chose Walkability Index");
    note(walk.every((id) => seen.ids.slice(0, 3).includes(id)),
      "the three public Walkability Index sources are the top three");
    await shot("03-walkability.png");

    /* What to do live when the model reads "this region" as a place. The walkability
       example's wording usually gets Queensland and a council or small-area tag, which
       no public walkability source carries. Taking those off is the correction the
       page exists to allow. */
    log("4. Take off the place tags, then put them back");
    const before = { bar: seen.bar, ids: seen.ids.join(","), chips: await chipCount() };
    const places = await evaluate(`[...document.querySelectorAll(".ask-chip__x")]
      .filter(x => x.dataset.group === "geo" || x.dataset.group === "region")
      .map(x => [x.dataset.group, x.dataset.value])`);
    if (!places.length) {
      log("   the model chose no place tags this time, so taking off the first tag instead");
      const first = await evaluate(`(() => { const x = document.querySelector(".ask-chip__x");
        return [x.dataset.group, x.dataset.value]; })()`);
      places.push(first);
    }
    for (const [group, value] of places) {
      await removeChip(group, value);
      seen = await evaluate(READ);
      log(`   without ${value}: ${seen.bar}`);
    }
    expect(await chipCount() === before.chips - places.length, "each click took off exactly one tag");
    expect(seen.ids.length > 0, "the list was ranked again and is not empty");
    note(walk.every((id) => seen.ids.slice(0, 3).includes(id)) && /^3 sources carry every tag/.test(seen.bar),
      "without the place tags, the three public Walkability Index sources carry every tag");
    await shot("04-place-tags-removed.png");
    await evaluate(`document.getElementById("restore").click()`);
    await waitFor(`!document.getElementById("restore")`, "the model's tags to come back", 10000);
    seen = await evaluate(READ);
    expect(seen.bar === before.bar && seen.ids.join(",") === before.ids,
      "putting the tags back restores the original list");

    log("5. The public transport question");
    await askExample("cost effectiveness");
    await waitFor(FINISHED, "the answer", 90000);
    seen = await evaluate(READ);
    log(`   tags: ${seen.tags.join(" | ")}`);
    log(`   ${seen.bar}`);
    log(`   top three: ${seen.ids.slice(0, 3).join(", ")}`);
    expect(!seen.error && seen.ids.length > 0, "an answer came back");
    await shot("05-public-transport.png");

    log("6. The paywall question");
    await askExample("pay a fee");
    await waitFor(FINISHED, "the answer", 90000);
    seen = await evaluate(READ);
    log(`   ${seen.bar}`);
    const fee = ["app1-03", "app1-04", "app2-02", "app2-09", "app3-01", "app3-08", "app4-04", "app6-12"];
    note(fee.every((id) => seen.ids.slice(0, 8).includes(id)), "all eight fee-required sources are the top eight");
    await shot("06-fee.png");

    log("7. A question the scan cannot answer");
    await askExample("car parking");
    await waitFor(FINISHED, "the answer", 90000);
    seen = await evaluate(READ);
    log(`   tags: ${seen.tags.join(" | ") || "none"}`);
    log(`   ${seen.bar}`);
    expect(!seen.error, "no error shown");
    await shot("07-out-of-scope.png");

    log("8. Open a result, then its own page, then come back");
    await askExample("children's physical activity");
    await waitFor(FINISHED, "the answer", 90000);
    await evaluate(`document.querySelector("#results .result__head").click()`);
    await waitFor(`!!document.querySelector("#results .detail:not([hidden]) dl")`, "the result to open", 5000);
    await shot("08-result-open.png");
    const firstId = await evaluate(`document.querySelector("#results .result__head").dataset.id`);
    await open(BASE + "study.html?id=" + firstId);
    await waitFor(`!!document.querySelector("#record h1")`, "the source's page", 10000);
    expect(true, `the source's own page opens (${firstId})`);
    await back();
    // Either the saved answer is drawn at once, or the page starts asking the model
    // again and shows its clock. Whichever appears first says which happened.
    await waitFor(`!!document.querySelector("#results .result") || !!document.getElementById("clock")`,
      "the page to settle", 10000);
    const reasked = await evaluate(`!!document.getElementById("clock")`);
    expect(!reasked, "coming back shows the saved answer instead of asking the model again");

    log("9. The site's own pages link to the search page");
    await open(BASE + "browse.html");
    const link = await evaluate(`!!document.querySelector('.site-nav a[href="ask.html"]')`);
    expect(link, "browse.html's menu has an Ask a question link");
  } finally {
    try { cdp.ws.close(); } catch { /* already gone */ }
    browser.child.kill();
    await sleep(500);
    try { fs.rmSync(browser.profile, { recursive: true, force: true }); } catch { /* the browser may still hold it */ }
  }
}

try {
  await run();
} catch (err) {
  problems.push(err.message);
}
if (notes.length) {
  log("\nBefore the demo, the model answered differently from what the demo expects:");
  notes.forEach((n) => log(" - " + n));
}
if (problems.length) {
  log("\nProblems with the page:");
  problems.forEach((p) => log(" - " + p));
  process.exit(1);
}
log("\nThe page worked at every step." + (notes.length ? " See the notes above." : ""));
