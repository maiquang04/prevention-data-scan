"""The search page, served on this computer.

    python search/serve.py

Opens http://127.0.0.1:8765/ask.html in the browser. Needs LM Studio's server running
for the model; without it the page still works, ranking by the words of the question.

The page looks like part of the website because it is served alongside it. Every file
of the real site in the folder above is served exactly as it is on disk, and the search
page's own files come from search/ui/. So the header, the styles, the badges and the
links to each source's page are the site's own, and no file outside this folder is
changed. The one thing added on the way out is an "Ask a question" link in each site
page's menu, so the demonstration can move between the two.

It listens on 127.0.0.1 only, so nothing outside this computer can reach it. Standard
library only, like the rest of the prototype.
"""
import argparse
import json
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import llm
from bm25 import build_index
from config import DATA, LMSTUDIO_URL, MODEL_MAIN, MODEL_SMALL, ROOT, SITE_DIR, TOP_K
from corpus import browse_url, load_studies, source_tags
from map_query import map_query, partial_reasoning
from rank import explain, full_matches, rank
from vocab import GROUPS, indicator_domain, load_meta, load_vocab

UI_DIR = ROOT / "ui"
HOST = "127.0.0.1"
PORT = 8765
MAX_BODY = 64 * 1024
MAX_QUESTION = 500

# The two models this prototype is set up for, by the names shown on the page.
MODEL_NAMES = {MODEL_MAIN: "Qwen3 4B", MODEL_SMALL: "Qwen3 1.7B"}

# Written out rather than taken from the mimetypes module: on Windows that reads the
# registry, where .js is sometimes registered as text/plain.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".ico": "image/x-icon", ".webp": "image/webp", ".woff2": "font/woff2",
    ".pdf": "application/pdf",
}

NAV_LINK = '<a href="ask.html">Ask a question</a>'
_SITE_NAV = re.compile(r'(<nav class="site-nav"[^>]*>.*?)(\s*</nav>)', re.S)


class ClientGone(Exception):
    """The browser went away before the answer finished.

    Deliberately not a ConnectionError: llm.chat turns those into "LM Studio stopped
    answering", which would blame the wrong end.
    """


def say(message):
    print(time.strftime("[%H:%M:%S] ") + message, flush=True)


# ---------- static files ----------

def resolve_static(url_path):
    """The file to send for a URL path, or None.

    The search page's own files are looked for first, then the site's. Nothing
    outside those two folders can be reached, and neither can hidden files such as
    the site repository's .git folder.
    """
    rel = urllib.parse.unquote(url_path or "").replace("\\", "/").lstrip("/")
    if not rel or "\x00" in rel or ":" in rel:
        return None
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts or any(p.startswith(".") for p in parts):
        return None                     # hidden files, and any climb out with ..
    for base in (UI_DIR, SITE_DIR):
        root = base.resolve()
        candidate = root.joinpath(*parts).resolve()
        if not candidate.is_relative_to(root):
            return None
        if candidate.is_file():
            return candidate
    return None


def inject_nav(html):
    """Add the search page to a site page's menu, once."""
    if 'href="ask.html"' in html:
        return html
    return _SITE_NAV.sub(lambda m: m.group(1) + "\n      " + NAV_LINK + m.group(2),
                         html, count=1)


# ---------- the search itself ----------

def load_examples(path=DATA / "handwritten.json"):
    """The demo questions, for the buttons under the search box."""
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f).get("items", [])
    except (OSError, json.JSONDecodeError):
        return []
    return [{"qid": i.get("qid"), "question": i["question"]}
            for i in items if i.get("question")]


class Search:
    """Everything loaded once at start-up and shared by every request.

    Restart the server after regenerating the site's data: it does not notice.
    """

    def __init__(self):
        self.meta = load_meta()
        self.vocab = load_vocab(self.meta)
        self.studies = load_studies()
        self.index = build_index(self.studies)
        self.ind_domain = indicator_domain(self.meta)
        self.tags_of = {s["id"]: source_tags(s, self.meta) for s in self.studies}
        self.examples = load_examples()

    def clean_tags(self, tags):
        """Keep only real tag values, one list per group, in the order given."""
        out = {}
        for group in GROUPS:
            wanted = tags.get(group) if isinstance(tags, dict) else None
            if isinstance(wanted, str):
                wanted = [wanted]
            if not isinstance(wanted, list):
                wanted = []
            kept = []
            for value in wanted:
                if value in self.vocab[group] and value not in kept:
                    kept.append(value)
            out[group] = kept
        return out

    def result(self, question, tags, **extra):
        """Rank for these tags and say, for each source shown, which ones it carries."""
        everything = rank(question, tags, self.studies, self.meta, self.index, k=None)
        chosen = {g: v for g, v in tags.items() if v}
        full = full_matches(everything)
        rows = []
        for r in everything[:TOP_K]:
            hits, misses = explain(self.tags_of[r.id], chosen, self.ind_domain)
            rows.append({"id": r.id, "matched": r.matched, "asked": r.asked,
                         "hits": hits, "misses": misses})
        payload = {"type": "result", "question": question, "tags": chosen,
                   "fullMatches": full,
                   # Relative, so the filters open on the copy of the site served here.
                   "url": browse_url(chosen, self.meta, base="") if chosen and full else None,
                   "results": rows}
        payload.update(extra)
        return payload


def status():
    """Whether the model can be asked, for the line under the search box."""
    configured = [MODEL_MAIN, MODEL_SMALL]
    try:
        available = set(llm.list_models(timeout=5))
    except llm.LLMError as err:
        return {"ok": False, "message": str(err), "default": MODEL_MAIN,
                "models": [{"id": m, "name": MODEL_NAMES[m], "available": False,
                            "loaded": None, "recommended": m == MODEL_MAIN}
                           for m in configured]}
    states = llm.model_states()
    models = [{"id": m, "name": MODEL_NAMES[m], "available": m in available,
               "loaded": (states.get(m) == "loaded") if states else None,
               "recommended": m == MODEL_MAIN} for m in configured]
    ok = any(m["available"] for m in models)
    return {"ok": ok, "default": MODEL_MAIN, "models": models,
            "message": "" if ok else ("LM Studio is running but has neither Qwen model. "
                                      "Download them in LM Studio first.")}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "SearchPrototype/1"
    search = None                       # set once in make_server()

    def log_message(self, fmt, *args):
        pass                            # quiet; the searches are reported by say()

    # -- plumbing --

    def _headers(self, status, content_type, length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        # Never cached: during a demo the page and its data must be what is on disk.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(data))
        self.wfile.write(data)

    def send_text(self, status, text):
        data = text.encode("utf-8")
        self._headers(status, "text/plain; charset=utf-8", len(data))
        self.wfile.write(data)

    def read_json(self):
        """The request body as a dict, or None after sending an error."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.send_json({"error": "That request is too large."}, 413)
            return None
        try:
            body = json.loads((self.rfile.read(length) if length else b"{}").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json({"error": "The request was not valid JSON."}, 400)
            return None
        if not isinstance(body, dict):
            self.send_json({"error": "The request was not a JSON object."}, 400)
            return None
        return body

    # -- routes --

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ("", "/"):
            self.send_response(302)
            self.send_header("Location", "/ask.html")
            self.end_headers()
            return
        if path == "/favicon.ico" and resolve_static(path) is None:
            # Browsers ask for this on every page and the site has none. An empty
            # answer rather than a 404 keeps the console clear during the demo.
            self.send_response(204)
            self.end_headers()
            return
        if path == "/api/status":
            return self.send_json(status())
        if path == "/api/examples":
            return self.send_json({"examples": self.search.examples})
        if path.startswith("/api/"):
            return self.send_json({"error": "No such address."}, 404)
        self.send_static(path)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/api/ask", "/api/rank"):
            return self.send_json({"error": "No such address."}, 404)
        body = self.read_json()
        if body is None:
            return
        question = " ".join(str(body.get("question") or "").split())[:MAX_QUESTION]
        if path == "/api/rank":
            return self.rerank(question, body.get("tags"))
        if not question:
            return self.send_json({"error": "Type a question first."}, 400)
        model = body.get("model") if body.get("model") in MODEL_NAMES else MODEL_MAIN
        self.ask(question, model)

    def send_static(self, url_path):
        file = resolve_static(url_path)
        if file is None:
            return self.send_text(404, "Not found")
        data = file.read_bytes()
        suffix = file.suffix.lower()
        if suffix == ".html" and file.is_relative_to(SITE_DIR.resolve()):
            data = inject_nav(data.decode("utf-8")).encode("utf-8")
        self._headers(200, CONTENT_TYPES.get(suffix, "application/octet-stream"), len(data))
        self.wfile.write(data)

    def rerank(self, question, tags):
        """Rank again for tags the reader has edited. No model involved, so it is instant."""
        clean = self.search.clean_tags(tags or {})
        self.send_json(self.search.result(question, clean))

    def ask(self, question, model):
        """Stream the answer as one JSON object per line.

        {"type": "start"} first, then {"type": "reasoning", "text": ...} each time the
        model's reasoning grows, then one {"type": "result", ...}. If the model cannot
        be reached, the result is ranked by the question's words alone and says why.
        """
        self._headers(200, "application/x-ndjson; charset=utf-8")

        def emit(obj):
            try:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
            except OSError as err:
                raise ClientGone() from err

        shown = {"text": ""}

        def on_text(raw):
            text = partial_reasoning(raw)
            if text and text != shown["text"]:
                shown["text"] = text
                emit({"type": "reasoning", "text": text})

        name = MODEL_NAMES[model]
        try:
            emit({"type": "start", "model": model, "modelName": name})
            mapped = map_query(question, model, self.search.meta, self.search.vocab,
                               on_text=on_text)
            if mapped.error:
                payload = self.search.result(
                    question, {g: [] for g in GROUPS}, model=model, modelName=name,
                    reasoning="", seconds=round(mapped.seconds, 1), usedModel=False,
                    rejected=[], note=mapped.error)
            else:
                payload = self.search.result(
                    question, mapped.tags, model=model, modelName=name,
                    reasoning=mapped.reasoning, seconds=round(mapped.seconds, 1),
                    usedModel=True, rejected=mapped.rejected, note="")
            emit(payload)
        except ClientGone:
            say('stopped: the page was closed, or asked something else, before "%s" '
                "finished" % question[:60])
            return
        say('%s %4.1fs  "%s"  -> %s' % (
            name, mapped.seconds, question[:70],
            "model unavailable, ranked by words" if mapped.error else
            "%d tag%s, %d carry every one" % (
                sum(len(v) for v in payload["tags"].values()),
                "" if sum(len(v) for v in payload["tags"].values()) == 1 else "s",
                payload["fullMatches"])))


def make_server(port=PORT, host=HOST):
    Handler.search = Search()
    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open the page in a browser")
    args = parser.parse_args()

    try:
        httpd = make_server(args.port)
    except OSError as err:
        print("Could not start on port %d (%s).\nIs the search page already running "
              "in another window? If so use that one, or pick another port with "
              "--port %d." % (args.port, err, args.port + 1))
        return 1

    url = "http://%s:%d/ask.html" % (HOST, httpd.server_address[1])
    st = status()
    print("Search page:  " + url)
    if st["ok"]:
        ready = ", ".join("%s (%s)" % (m["name"], "loaded" if m["loaded"] else
                                       "not loaded yet" if m["loaded"] is False else "available")
                          for m in st["models"] if m["available"])
        print("Model:        LM Studio at %s, %s" % (LMSTUDIO_URL, ready))
    else:
        print("Model:        not available. " + st["message"])
        print("              The page still works, ranking by the words of the question.")
    print("Press Ctrl+C to stop.\n", flush=True)

    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
