"""Tests for the search page's server, and the streaming it relies on.

Run from the repository root with the other tests:
    python -m unittest discover -s search -p "test_*.py" -v

None of these needs LM Studio: the model is replaced by a stand-in wherever one is
asked. The page itself is checked in a real browser by ui_check.mjs.
"""
import http.client
import io
import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import corpus
import llm
import map_query
import rank
import serve
import vocab
from test_prototype import FAKE_META, fake_study


class StaticFileTests(unittest.TestCase):
    def test_search_page_files_come_from_ui(self):
        self.assertEqual(serve.resolve_static("/ask.html"), (serve.UI_DIR / "ask.html").resolve())
        self.assertEqual(serve.resolve_static("/assets/ask.js"),
                         (serve.UI_DIR / "assets" / "ask.js").resolve())

    def test_site_files_come_from_the_site(self):
        self.assertEqual(serve.resolve_static("/browse.html"),
                         (config.SITE_DIR / "browse.html").resolve())
        self.assertEqual(serve.resolve_static("/data/studies.json"),
                         config.STUDIES_JSON.resolve())

    def test_nothing_outside_the_two_folders(self):
        for attempt in ["/../AGENTS.md", "/%2e%2e/AGENTS.md", "/assets/../../AGENTS.md",
                        "/..%2f..%2fAGENTS.md", "/..\\AGENTS.md", "/C:/Windows/win.ini",
                        "/index.html::$DATA", "/%00index.html"]:
            self.assertIsNone(serve.resolve_static(attempt), attempt)

    def test_hidden_files_refused(self):
        # The site folder is a git repository; its .git folder must not be served.
        self.assertIsNone(serve.resolve_static("/.git/config"))
        self.assertIsNone(serve.resolve_static("/assets/.hidden"))

    def test_missing_file(self):
        self.assertIsNone(serve.resolve_static("/nothing-here.html"))
        self.assertIsNone(serve.resolve_static("/"))


class NavTests(unittest.TestCase):
    SITE_PAGE = ('<nav class="site-nav" aria-label="Main">\n'
                 '      <a href="index.html">Overview</a>\n'
                 '      <a href="browse.html">Browse sources</a>\n'
                 '    </nav>\n<p><a href="browse.html">Browse sources</a></p>')

    def test_link_added_inside_the_menu(self):
        html = serve.inject_nav(self.SITE_PAGE)
        menu = html.split("</nav>")[0]
        self.assertIn('<a href="ask.html">Ask a question</a>', menu)
        self.assertEqual(html.count("ask.html"), 1)

    def test_added_once(self):
        once = serve.inject_nav(self.SITE_PAGE)
        self.assertEqual(serve.inject_nav(once), once)

    def test_every_real_site_page_gets_it(self):
        for page in ["index.html", "browse.html", "application.html", "study.html"]:
            html = (config.SITE_DIR / page).read_text(encoding="utf-8")
            self.assertIn('href="ask.html"', serve.inject_nav(html), page)


class PartialReasoningTests(unittest.TestCase):
    def test_nothing_yet(self):
        self.assertEqual(map_query.partial_reasoning(""), "")
        self.assertEqual(map_query.partial_reasoning('{"reas'), "")

    def test_growing_string(self):
        self.assertEqual(map_query.partial_reasoning('{"reasoning": "The user wa'),
                         "The user wa")

    def test_finished_string_stops_at_the_quote(self):
        raw = '{"reasoning": "Done.", "topic": ["Health'
        self.assertEqual(map_query.partial_reasoning(raw), "Done.")

    def test_half_an_escape_is_trimmed(self):
        self.assertEqual(map_query.partial_reasoning('{"reasoning": "a \\'), "a ")
        self.assertEqual(map_query.partial_reasoning('{"reasoning": "caf\\u00'), "caf")
        self.assertEqual(map_query.partial_reasoning('{"reasoning": "say \\"hi\\" t'),
                         'say "hi" t')

    def test_thinking_hides_everything(self):
        self.assertEqual(map_query.partial_reasoning('<think>hmm {"reasoning": "x'), "")
        self.assertEqual(map_query.partial_reasoning('<think>a</think>{"reasoning": "b'), "b")


class ExplainTests(unittest.TestCase):
    def setUp(self):
        self.ind_domain = vocab.indicator_domain(FAKE_META)

    def test_hits_and_misses(self):
        src = corpus.source_tags(fake_study("a", indicators=["Walkability Index"],
                                            access="Public"), FAKE_META)
        hits, misses = rank.explain(src, {"indicator": ["Walkability Index"],
                                          "access": ["Public"], "region": ["Queensland"]},
                                    self.ind_domain)
        self.assertEqual(hits, {"indicator": ["Walkability Index"], "access": ["Public"]})
        self.assertEqual(misses, {"region": ["Queensland"]})

    def test_domain_through_a_measure(self):
        src = corpus.source_tags(fake_study("a", indicators=["Walkability Index"]), FAKE_META)
        hits, _ = rank.explain(src, {"domain": ["Physical Activity"]}, self.ind_domain)
        self.assertEqual(hits, {"domain": ["Physical Activity"]})

    def test_only_the_values_carried_are_hits(self):
        src = corpus.source_tags(fake_study("a", regions=["Australia"]), FAKE_META)
        hits, misses = rank.explain(src, {"region": ["Queensland", "Australia"]},
                                    self.ind_domain)
        self.assertEqual(hits, {"region": ["Australia"]})
        self.assertEqual(misses, {})

    def test_agrees_with_the_score(self):
        # explain() and rank() must never disagree about how many groups matched.
        meta, studies = vocab.load_meta(), corpus.load_studies()
        index = __import__("bm25").build_index(studies)
        tags = {"indicator": ["Walkability Index"], "access": ["Public"],
                "region": ["Queensland"]}
        ind_domain = vocab.indicator_domain(meta)
        by_id = corpus.study_index(studies)
        for r in rank.rank("walkability", tags, studies, meta, index, k=None):
            hits, misses = rank.explain(corpus.source_tags(by_id[r.id], meta), tags, ind_domain)
            self.assertEqual(len(hits), r.matched, r.id)
            self.assertEqual(len(hits) + len(misses), r.asked, r.id)


class RelativeLinkTests(unittest.TestCase):
    def test_relative_link_for_the_local_site(self):
        url = corpus.browse_url({"access": ["Public"]}, FAKE_META, base="")
        self.assertEqual(url, "browse.html?access=Public")

    def test_live_link_by_default(self):
        url = corpus.browse_url({"access": ["Public"]}, FAKE_META)
        self.assertTrue(url.startswith(config.SITE_URL))


class StreamingTests(unittest.TestCase):
    """llm.chat when asked to stream, with LM Studio replaced by canned lines."""

    LINES = [b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n', b"\n",
             b'data: {"choices":[{"delta":{"content":"{\\"reasoning\\": \\"Hel"}}]}\n',
             b'data: {"choices":[{"delta":{"content":"lo\\"}"}}]}\n',
             b"data: [DONE]\n"]

    def _response(self, lines):
        response = mock.MagicMock()
        response.__enter__.return_value = iter(lines)
        return response

    def test_pieces_are_joined_and_reported_as_they_come(self):
        seen = []
        with mock.patch.object(urllib.request, "urlopen", return_value=self._response(self.LINES)) as op:
            text = llm.chat([{"role": "user", "content": "q"}], model=config.MODEL_MAIN,
                            on_text=seen.append)
        self.assertEqual(text, '{"reasoning": "Hello"}')
        self.assertEqual(seen, ['{"reasoning": "Hel', '{"reasoning": "Hello"}'])
        body = json.loads(op.call_args[0][0].data.decode("utf-8"))
        self.assertTrue(body["stream"])

    def test_not_streamed_unless_asked(self):
        reply = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = reply
        with mock.patch.object(urllib.request, "urlopen", return_value=response) as op:
            llm.chat([{"role": "user", "content": "q"}], model=config.MODEL_MAIN)
        self.assertNotIn("stream", json.loads(op.call_args[0][0].data.decode("utf-8")))

    def test_a_raise_in_on_text_reaches_the_caller(self):
        # This is how the server stops the model when the page goes away. It must not
        # be mistaken for LM Studio failing.
        def stop(_):
            raise serve.ClientGone()
        with mock.patch.object(urllib.request, "urlopen", return_value=self._response(self.LINES)):
            with self.assertRaises(serve.ClientGone):
                llm.chat([{"role": "user", "content": "q"}], model=config.MODEL_MAIN,
                         on_text=stop)


class ServerTests(unittest.TestCase):
    """The real server on a spare port, with the model replaced where it is asked."""

    @classmethod
    def setUpClass(cls):
        cls.quiet = mock.patch.object(serve, "say")   # keep the test output readable
        cls.quiet.start()
        cls.httpd = serve.make_server(port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.quiet.stop()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        conn.request(method, path, body=data,
                     headers={"Content-Type": "application/json"} if data else {})
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response, payload

    def test_home_goes_to_the_search_page(self):
        response, _ = self.request("GET", "/")
        self.assertEqual(response.status, 302)
        self.assertEqual(response.getheader("Location"), "/ask.html")

    def test_pages_and_types(self):
        for path, kind in [("/ask.html", "text/html"), ("/assets/ask.js", "text/javascript"),
                           ("/assets/style.css", "text/css"),
                           ("/data/meta.json", "application/json")]:
            response, _ = self.request("GET", path)
            self.assertEqual(response.status, 200, path)
            self.assertTrue(response.getheader("Content-Type").startswith(kind), path)
            self.assertEqual(response.getheader("Cache-Control"), "no-store", path)

    def test_site_pages_link_to_the_search(self):
        _, html = self.request("GET", "/browse.html")
        self.assertIn(b'<a href="ask.html">Ask a question</a>', html)

    def test_refused_paths(self):
        for path in ["/.git/config", "/%2e%2e/AGENTS.md", "/api/unknown"]:
            response, _ = self.request("GET", path)
            self.assertEqual(response.status, 404, path)

    def test_no_favicon_is_not_an_error(self):
        response, _ = self.request("GET", "/favicon.ico")
        self.assertEqual(response.status, 204)

    def test_status_answers_either_way(self):
        response, payload = self.request("GET", "/api/status")
        body = json.loads(payload)
        self.assertEqual(response.status, 200)
        self.assertIn(body["ok"], (True, False))
        self.assertEqual([m["id"] for m in body["models"]],
                         [config.MODEL_MAIN, config.MODEL_SMALL])

    def test_examples(self):
        _, payload = self.request("GET", "/api/examples")
        examples = json.loads(payload)["examples"]
        self.assertEqual(len(examples), 8)
        self.assertTrue(all(e["question"] for e in examples))

    def test_rank_keeps_only_real_tags(self):
        response, payload = self.request("POST", "/api/rank", {
            "question": "walkability",
            "tags": {"indicator": ["Walkability Index", "Invented"], "access": "Public",
                     "nonsense": ["x"]}})
        body = json.loads(payload)
        self.assertEqual(response.status, 200)
        self.assertEqual(body["tags"], {"indicator": ["Walkability Index"], "access": ["Public"]})
        self.assertEqual(body["fullMatches"], 3)
        self.assertEqual(body["url"], "browse.html?indicator=Walkability%20Index&access=Public")
        top = body["results"][:3]
        self.assertEqual({r["id"] for r in top}, {"app6-01", "app6-02", "app6-04"})
        self.assertTrue(all(r["matched"] == r["asked"] == 2 for r in top))

    def test_bad_requests(self):
        response, _ = self.request("POST", "/api/ask", {"question": "   "})
        self.assertEqual(response.status, 400)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/rank", body=b"not json",
                     headers={"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def _events(self, payload):
        return [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]

    def test_ask_streams_reasoning_then_the_result(self):
        def fake_map(question, model, meta, vocab_, on_text=None):
            for piece in ['{"reasoning": "Walk', '{"reasoning": "Walkability, public."']:
                on_text(piece)
            return map_query.Mapping(question, model,
                                     {**{g: [] for g in vocab.GROUPS},
                                      "indicator": ["Walkability Index"], "access": ["Public"]},
                                     "Walkability, public.", seconds=1.23)
        with mock.patch.object(serve, "map_query", side_effect=fake_map):
            response, payload = self.request("POST", "/api/ask",
                                             {"question": "walkable suburbs",
                                              "model": config.MODEL_MAIN})
        events = self._events(payload)
        self.assertTrue(response.getheader("Content-Type").startswith("application/x-ndjson"))
        self.assertEqual([e["type"] for e in events], ["start", "reasoning", "reasoning", "result"])
        self.assertEqual(events[2]["text"], "Walkability, public.")
        result = events[-1]
        self.assertTrue(result["usedModel"])
        self.assertEqual(result["modelName"], "Qwen3 4B")
        self.assertEqual(result["seconds"], 1.2)
        self.assertEqual(result["fullMatches"], 3)
        self.assertEqual(result["results"][0]["hits"],
                         {"indicator": ["Walkability Index"], "access": ["Public"]})

    def test_ask_falls_back_to_words_when_the_model_is_away(self):
        def failing_map(question, model, meta, vocab_, on_text=None):
            return map_query.Mapping(question, model, {g: [] for g in vocab.GROUPS}, "",
                                     seconds=0.01, error=llm.NOT_RUNNING)
        with mock.patch.object(serve, "map_query", side_effect=failing_map):
            _, payload = self.request("POST", "/api/ask", {"question": "walkability index"})
        result = self._events(payload)[-1]
        self.assertEqual(result["type"], "result")
        self.assertFalse(result["usedModel"])
        self.assertEqual(result["note"], llm.NOT_RUNNING)
        self.assertEqual(result["tags"], {})
        self.assertIsNone(result["url"])
        self.assertTrue(result["results"], "word matching should still find something")

    def test_unknown_model_falls_back_to_the_main_one(self):
        seen = {}

        def record(question, model, meta, vocab_, on_text=None):
            seen["model"] = model
            return map_query.Mapping(question, model, {g: [] for g in vocab.GROUPS}, "")
        with mock.patch.object(serve, "map_query", side_effect=record):
            self.request("POST", "/api/ask", {"question": "x", "model": "someone/else"})
        self.assertEqual(seen["model"], config.MODEL_MAIN)


if __name__ == "__main__":
    unittest.main()
