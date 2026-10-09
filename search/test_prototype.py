"""Tests for the search prototype.

Run from the repository root:
    python -m unittest discover -s search -p "test_*.py" -v

Most tests build small made-up sources rather than using the real ones, so adding a
source to the scan cannot break them. The few that do read the real meta.json are
there to catch the vocabulary changing shape.
"""
import io
import json
import sys
import tempfile
import unittest
import urllib.request
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bm25
import config
import corpus
import llm
import map_query
import rank
import vocab

# A pretend collection: two health focuses, three measures, enough to test the rules.
FAKE_META = {
    "topics": [{"id": "health-system", "title": "Health system impact",
                "question": "Evidence linking prevention to hospital demand"},
               {"id": "local-government", "title": "Local government measurement",
                "question": "Council-level signals of a healthier local area"}],
    "domains": ["Physical Activity", "Healthy Eating"],
    "indicators": [{"name": "Walkability Index", "domain": "Physical Activity"},
                   {"name": "Walking & cycling counts", "domain": "Physical Activity"},
                   {"name": "Fruit intake", "domain": "Healthy Eating"}],
    "geoTags": ["LGA", "State"],
    "accessValues": ["Public", "Restricted - application"],
    "applicationFeeValues": ["Fee required", "No fee", "Not applicable"],
    "sourceGroups": ["Peer-reviewed", "Published dataset"],
    "regions": ["Queensland", "Australia"],
}


def fake_study(sid, **kw):
    study = {"id": sid, "reference": sid + " reference", "task": "", "metrics": "",
             "inputData": "", "output": "", "dataSources": "", "accessNote": "",
             "scale": "", "keyAttributes": "", "geoLevel": "", "country": "",
             "topics": [], "domains": [], "indicators": [], "geoTags": [],
             "access": "Public", "applicationFee": "Not applicable",
             "sourceGroup": "Peer-reviewed", "regions": []}
    study.update(kw)
    return study


class VocabularyTests(unittest.TestCase):
    """The real meta.json, because its shape is what the prompt depends on."""

    @classmethod
    def setUpClass(cls):
        cls.meta = vocab.load_meta()
        cls.vocab = vocab.load_vocab(cls.meta)

    def test_group_sizes(self):
        self.assertEqual({g: len(self.vocab[g]) for g in vocab.GROUPS},
                         {"topic": 6, "domain": 7, "indicator": 61, "geo": 11,
                          "access": 5, "fee": 3, "type": 5, "region": 3})
        self.assertEqual(sum(len(v) for v in self.vocab.values()), 101)

    def test_fee_excludes_not_applicable(self):
        # The site's fee filter ignores it, so the model must never pick it.
        self.assertIn("Not applicable", self.meta["applicationFeeValues"])
        self.assertNotIn("Not applicable", self.vocab["fee"])

    def test_schema_shape(self):
        schema = vocab.json_schema(self.vocab)
        properties = list(schema["properties"])
        self.assertEqual(properties[0], "reasoning")
        self.assertEqual(properties[1:], vocab.GROUPS)
        self.assertEqual(schema["required"], ["reasoning"] + vocab.GROUPS)
        for group in vocab.GROUPS:
            self.assertEqual(schema["properties"][group]["items"]["enum"],
                             self.vocab[group])

    def test_prompt_block_names_every_value(self):
        block = vocab.vocab_prompt_block(self.meta, self.vocab)
        for group in vocab.GROUPS:
            for value in self.vocab[group]:
                self.assertIn(value, block, "%s missing from the prompt" % value)

    def test_prompt_block_uses_fake_meta_layout(self):
        block = vocab.vocab_prompt_block(FAKE_META, vocab.load_vocab(FAKE_META))
        self.assertIn('Focus area (key "topic"):', block)
        self.assertIn("- Physical Activity: Walkability Index; Walking & cycling counts",
                      block)
        self.assertIn("LGA (Local government area)", block)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.vocab = vocab.load_vocab(FAKE_META)

    def test_clean_json(self):
        raw = json.dumps({"reasoning": "because", "indicator": ["Fruit intake"],
                          "access": ["Public"]})
        tags, reasoning, rejected = map_query.parse_and_validate(raw, self.vocab)
        self.assertEqual(tags["indicator"], ["Fruit intake"])
        self.assertEqual(tags["access"], ["Public"])
        self.assertEqual(tags["topic"], [])
        self.assertEqual(reasoning, "because")
        self.assertEqual(rejected, [])

    def test_invented_value_rejected(self):
        raw = json.dumps({"indicator": ["Fruit intake", "Bicycle happiness"]})
        tags, _, rejected = map_query.parse_and_validate(raw, self.vocab)
        self.assertEqual(tags["indicator"], ["Fruit intake"])
        self.assertEqual(rejected, ["indicator:Bicycle happiness"])

    def test_text_around_the_json(self):
        raw = 'Sure! ```json\n{"access": ["Public"]}\n``` hope that helps'
        tags, _, rejected = map_query.parse_and_validate(raw, self.vocab)
        self.assertEqual(tags["access"], ["Public"])
        self.assertEqual(rejected, [])

    def test_string_instead_of_list(self):
        tags, _, _ = map_query.parse_and_validate('{"access": "Public"}', self.vocab)
        self.assertEqual(tags["access"], ["Public"])

    def test_duplicates_removed(self):
        raw = '{"access": ["Public", "Public"]}'
        tags, _, _ = map_query.parse_and_validate(raw, self.vocab)
        self.assertEqual(tags["access"], ["Public"])

    def test_garbage(self):
        tags, reasoning, rejected = map_query.parse_and_validate("no idea", self.vocab)
        self.assertEqual(tags, {g: [] for g in vocab.GROUPS})
        self.assertEqual(reasoning, "")
        self.assertEqual(rejected, ["<unparseable>"])

    def test_non_string_reasoning_ignored(self):
        _, reasoning, _ = map_query.parse_and_validate('{"reasoning": 7}', self.vocab)
        self.assertEqual(reasoning, "")


class ThinkingTests(unittest.TestCase):
    def test_closed_block_removed(self):
        self.assertEqual(llm.strip_thinking("<think>hmm</think> {}"), "{}")

    def test_unclosed_block_drops_the_rest(self):
        # A model cut off mid-thought: everything after the tag is thinking.
        self.assertEqual(llm.strip_thinking("{} <think>hmm and then"), "{}")

    def test_plain_text_untouched(self):
        self.assertEqual(llm.strip_thinking("  {} "), "{}")


class ChatBodyTests(unittest.TestCase):
    """What actually gets sent to LM Studio."""

    def _send(self, model):
        reply = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = reply
        with mock.patch.object(urllib.request, "urlopen",
                               return_value=response) as urlopen:
            llm.chat([{"role": "user", "content": "hello"}], model=model)
        return json.loads(urlopen.call_args[0][0].data.decode("utf-8"))

    def test_no_think_added_for_hybrid_model(self):
        body = self._send(config.MODEL_SMALL)
        self.assertTrue(body["messages"][-1]["content"].endswith("/no_think"))

    def test_no_think_not_added_for_instruct_model(self):
        body = self._send(config.MODEL_MAIN)
        self.assertEqual(body["messages"][-1]["content"], "hello")

    def test_caller_messages_not_modified(self):
        messages = [{"role": "user", "content": "hello"}]
        reply = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = reply
        with mock.patch.object(urllib.request, "urlopen", return_value=response):
            llm.chat(messages, model=config.MODEL_SMALL)
        self.assertEqual(messages[0]["content"], "hello")


class RetryTests(unittest.TestCase):
    """A request that failed in a way that might pass next time is sent again.

    LM Studio returned 500 for the last 17 questions of one evaluation run, under
    memory pressure, then recovered. Without a retry that run was wasted, and the
    same thing mid-demonstration would show an error instead of an answer.
    """

    def ok(self):
        reply = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = reply
        return response

    def http_error(self, code):
        return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(b"server busy"))

    def test_a_server_error_is_retried_and_can_succeed(self):
        with mock.patch.object(llm, "BACKOFF_S", 0), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=[self.http_error(500), self.ok()]) as urlopen:
            self.assertEqual(llm.chat([{"role": "user", "content": "q"}],
                                      model=config.MODEL_MAIN), "{}")
        self.assertEqual(urlopen.call_count, 2)

    def test_it_gives_up_eventually(self):
        with mock.patch.object(llm, "BACKOFF_S", 0), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=self.http_error(500)) as urlopen:
            with self.assertRaises(llm.LLMError):
                llm.chat([{"role": "user", "content": "q"}], model=config.MODEL_MAIN)
        self.assertEqual(urlopen.call_count, llm.RETRIES)

    def test_our_own_mistake_is_not_retried(self):
        # A 400 means the request is wrong; sending it again wastes time.
        with mock.patch.object(llm, "BACKOFF_S", 0), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=self.http_error(400)) as urlopen:
            with self.assertRaises(llm.LLMError):
                llm.chat([{"role": "user", "content": "q"}], model=config.MODEL_MAIN)
        self.assertEqual(urlopen.call_count, 1)

    def test_a_slow_model_is_not_retried(self):
        # Retrying something that timed out just waits all over again.
        with mock.patch.object(llm, "BACKOFF_S", 0), \
             mock.patch.object(urllib.request, "urlopen",
                               side_effect=TimeoutError()) as urlopen:
            with self.assertRaises(llm.LLMError) as caught:
                llm.chat([{"role": "user", "content": "q"}], model=config.MODEL_MAIN)
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("did not answer within", str(caught.exception))


class SubsetSchemaTests(unittest.TestCase):
    """The staged mapper asks about part of the vocabulary at a time."""

    def setUp(self):
        self.vocab = vocab.load_vocab(FAKE_META)

    def test_only_the_groups_asked_for(self):
        schema = vocab.json_schema(self.vocab, groups=["geo", "access"])
        self.assertEqual(list(schema["properties"]), ["reasoning", "geo", "access"])
        self.assertEqual(schema["required"], ["reasoning", "geo", "access"])

    def test_choices_override_the_allowed_values(self):
        # Stage 3 offers only the measures under the focus already chosen.
        schema = vocab.json_schema(self.vocab, groups=["indicator"],
                                   choices={"indicator": ["Fruit intake"]})
        self.assertEqual(schema["properties"]["indicator"]["items"]["enum"],
                         ["Fruit intake"])

    def test_prompt_block_leaves_out_what_was_not_asked(self):
        block = vocab.vocab_prompt_block(FAKE_META, self.vocab, groups=["access"])
        self.assertIn("Public", block)
        self.assertNotIn("Walkability Index", block)
        self.assertNotIn("Queensland", block)

    def test_measures_under_a_focus(self):
        self.assertEqual(vocab.indicators_under(FAKE_META, ["Healthy Eating"]),
                         ["Fruit intake"])
        self.assertEqual(vocab.indicators_under(FAKE_META, ["Physical Activity"]),
                         ["Walkability Index", "Walking & cycling counts"])
        self.assertEqual(vocab.indicators_under(FAKE_META, []), [])

    def test_every_kind_has_a_meaning_for_stage_one(self):
        import map_staged
        for group in map_staged.KINDS:
            self.assertIn(group, vocab.GROUP_MEANING)
        self.assertNotIn("indicator", map_staged.KINDS)

    def test_the_key_is_shown_beside_the_label(self):
        # Without this the model cannot know "Health focus" is answered as "domain",
        # and in an early run it never chose it at all.
        import map_staged
        block = map_staged._kinds_block()
        for group in map_staged.KINDS:
            self.assertIn('(answer with "%s")' % group, block)


class StagedMapperTests(unittest.TestCase):
    """The three stages, with canned replies instead of a model."""

    def setUp(self):
        import map_staged
        self.ms = map_staged
        self.meta = FAKE_META
        self.vocab = vocab.load_vocab(FAKE_META)

    def run_with(self, replies):
        """Run the mapper against a fixed list of replies, and report the prompts."""
        seen = []

        def fake_chat(messages, model=None, temperature=0, max_tokens=0, schema=None,
                      on_text=None):
            seen.append((messages, schema))
            return replies[len(seen) - 1]

        with mock.patch.object(self.ms.llm, "chat", side_effect=fake_chat):
            got = self.ms.map_staged("a question", "m", self.meta, self.vocab)
        return got, seen

    def test_a_broad_question_stays_at_the_focus(self):
        # Stage 3 answering with nothing is the whole point: the search then uses
        # the focus, rather than a guessed measure.
        got, seen = self.run_with([
            '{"reasoning": "health subject", "kinds": ["domain"]}',
            '{"reasoning": "activity", "domain": ["Physical Activity"]}',
            '{"reasoning": "no single measure", "indicator": []}',
        ])
        self.assertEqual(got.tags["domain"], ["Physical Activity"])
        self.assertEqual(got.tags["indicator"], [])
        self.assertEqual(len(seen), 3)
        self.assertEqual(got.calls(), 3)

    def test_a_specific_question_reaches_the_measure(self):
        got, _ = self.run_with([
            '{"reasoning": "health subject", "kinds": ["domain"]}',
            '{"reasoning": "activity", "domain": ["Physical Activity"]}',
            '{"reasoning": "walkability", "indicator": ["Walkability Index"]}',
        ])
        self.assertEqual(got.tags["indicator"], ["Walkability Index"])

    def test_stage_three_only_offers_measures_under_the_chosen_focus(self):
        _, seen = self.run_with([
            '{"reasoning": "x", "kinds": ["domain"]}',
            '{"reasoning": "x", "domain": ["Healthy Eating"]}',
            '{"reasoning": "x", "indicator": []}',
        ])
        enum = seen[2][1]["properties"]["indicator"]["items"]["enum"]
        self.assertEqual(enum, ["Fruit intake"])        # not the activity measures

    def test_no_kinds_means_no_tags_and_one_call(self):
        got, seen = self.run_with(['{"reasoning": "nothing fits", "kinds": []}'])
        self.assertEqual(got.chosen(), {})
        self.assertEqual(len(seen), 1)
        self.assertEqual(got.error, "")

    def test_stage_three_is_skipped_without_a_focus(self):
        got, seen = self.run_with([
            '{"reasoning": "a place", "kinds": ["geo"]}',
            '{"reasoning": "councils", "geo": ["LGA"]}',
        ])
        self.assertEqual(len(seen), 2)
        self.assertEqual(got.tags["geo"], ["LGA"])
        self.assertEqual(got.tags["indicator"], [])

    def test_stage_two_may_overrule_stage_one(self):
        # Stage 1 guessed a kind; stage 2 found nothing in it that fits.
        got, _ = self.run_with([
            '{"reasoning": "maybe health", "kinds": ["domain"]}',
            '{"reasoning": "nothing fits", "domain": []}',
        ])
        self.assertEqual(got.chosen(), {})

    def test_an_invented_value_is_refused(self):
        got, _ = self.run_with([
            '{"reasoning": "x", "kinds": ["domain"]}',
            '{"reasoning": "x", "domain": ["Dentistry"]}',
        ])
        self.assertEqual(got.tags["domain"], [])
        self.assertIn("domain:Dentistry", got.rejected)

    def test_a_failure_is_reported_not_raised(self):
        def boom(*a, **k):
            raise self.ms.llm.LLMError("server away")
        with mock.patch.object(self.ms.llm, "chat", side_effect=boom):
            got = self.ms.map_staged("q", "m", self.meta, self.vocab)
        self.assertEqual(got.error, "server away")
        self.assertEqual(got.chosen(), {})

    def test_every_stage_is_recorded_for_display(self):
        got, _ = self.run_with([
            '{"reasoning": "health subject", "kinds": ["domain"]}',
            '{"reasoning": "activity", "domain": ["Physical Activity"]}',
            '{"reasoning": "no single measure", "indicator": []}',
        ])
        self.assertEqual([s.number for s in got.stages], [1, 2, 3])
        self.assertEqual(got.stages[0].chose, ["Health focus"])
        self.assertIn("none", got.stages[2].chose[0])


class RankerTests(unittest.TestCase):
    """Word matching first, as suggested on 8 October."""

    def setUp(self):
        self.studies = [
            fake_study("s1", indicators=["Walkability Index"], task="walking walking"),
            fake_study("s2", indicators=["Walkability Index"], task="nothing relevant"),
            fake_study("s3", task="walking"),
        ]
        self.index = bm25.build_index(self.studies)

    def test_word_matching_narrows_the_field(self):
        # s3 shares a word but carries no tag, so a shortlist of one leaves it out.
        tags = {"indicator": ["Fruit intake"]}
        ids = [r.id for r in rank.rank("walking", tags, self.studies, FAKE_META,
                                       self.index, k=None,
                                       strategy="bm25-first", recall=1)]
        self.assertNotIn("s3", ids)

    def test_a_source_carrying_every_tag_is_never_dropped(self):
        """s2 has the tag but shares no wording, and must survive anyway.

        Word matching does no stemming, so "walkable suburbs" matches nothing that
        says "walkability". Without this rule the three public walkability sources
        vanish from the very question they answer.
        """
        tags = {"indicator": ["Walkability Index"]}
        ids = [r.id for r in rank.rank("walking", tags, self.studies, FAKE_META,
                                       self.index, k=None,
                                       strategy="bm25-first", recall=1)]
        self.assertIn("s2", ids)

    def test_a_partial_match_outside_the_shortlist_is_still_dropped(self):
        # Only carrying *every* tag earns a place. Two tags asked, s2 has one.
        tags = {"indicator": ["Walkability Index"], "access": ["Restricted - application"]}
        ids = [r.id for r in rank.rank("walking", tags, self.studies, FAKE_META,
                                       self.index, k=None,
                                       strategy="bm25-first", recall=1)]
        self.assertNotIn("s2", ids)

    def test_tags_still_reorder_the_shortlist(self):
        tags = {"indicator": ["Walkability Index"]}
        ids = [r.id for r in rank.rank("walking", tags, self.studies, FAKE_META,
                                       self.index, k=None,
                                       strategy="bm25-first", recall=3)]
        self.assertEqual(ids[0], "s1")          # the tag lifts it above plain s3
        self.assertIn("s3", ids)

    def test_with_no_tags_it_is_word_matching_exactly(self):
        empty = {g: [] for g in vocab.GROUPS}
        ids = [r.id for r in rank.rank("walking", empty, self.studies, FAKE_META,
                                       self.index, k=None,
                                       strategy="bm25-first", recall=10)]
        self.assertEqual(ids, [d for d, _ in self.index.rank("walking", 10)])

    def test_tags_first_still_sees_everything(self):
        tags = {"indicator": ["Walkability Index"]}
        ids = [r.id for r in rank.rank("walking", tags, self.studies, FAKE_META,
                                       self.index, k=None, strategy="tags-first")]
        self.assertIn("s2", ids)                # kept, unlike under bm25-first

    def test_an_unknown_strategy_is_refused(self):
        with self.assertRaises(ValueError):
            rank.rank("q", {}, self.studies, FAKE_META, self.index, strategy="magic")


class TagTests(unittest.TestCase):
    def test_topic_ids_become_titles(self):
        study = fake_study("a", topics=["health-system"])
        tags = corpus.source_tags(study, FAKE_META)
        self.assertEqual(tags["topic"], {"Health system impact"})

    def test_not_applicable_is_not_a_fee_tag(self):
        study = fake_study("a", applicationFee="Not applicable")
        self.assertEqual(corpus.source_tags(study, FAKE_META)["fee"], set())

    def test_real_fee_is_a_tag(self):
        study = fake_study("a", applicationFee="Fee required")
        self.assertEqual(corpus.source_tags(study, FAKE_META)["fee"], {"Fee required"})

    def test_source_text_includes_measures_and_focuses(self):
        study = fake_study("a", task="counts people",
                           indicators=["Fruit intake"], domains=["Healthy Eating"])
        text = corpus.source_text(study)
        self.assertIn("counts people", text)
        self.assertIn("Fruit intake", text)
        self.assertIn("Healthy Eating", text)


class BrowseUrlTests(unittest.TestCase):
    def test_topic_title_becomes_id(self):
        url = corpus.browse_url({"topic": ["Health system impact"]}, FAKE_META)
        self.assertTrue(url.endswith("browse.html?topic=health-system"), url)

    def test_ampersand_encoded_and_empty_groups_left_out(self):
        url = corpus.browse_url({"indicator": ["Walking & cycling counts"],
                                 "access": []}, FAKE_META)
        self.assertIn("indicator=Walking%20%26%20cycling%20counts", url)
        self.assertNotIn("access=", url)

    def test_no_tags_gives_the_plain_page(self):
        url = corpus.browse_url({}, FAKE_META)
        self.assertTrue(url.endswith("browse.html"), url)

    def test_parent_focus_dropped_when_a_measure_is_chosen(self):
        # The site matches focus OR measure, so sending both would widen the page.
        url = corpus.browse_url({"domain": ["Physical Activity"],
                                 "indicator": ["Walkability Index"]}, FAKE_META)
        self.assertNotIn("domain=", url)
        self.assertIn("indicator=Walkability%20Index", url)

    def test_unrelated_focus_kept(self):
        url = corpus.browse_url({"domain": ["Healthy Eating"],
                                 "indicator": ["Walkability Index"]}, FAKE_META)
        self.assertIn("domain=Healthy%20Eating", url)


class RankTests(unittest.TestCase):
    def setUp(self):
        self.studies = [
            fake_study("s1", indicators=["Walkability Index"], access="Public",
                       task="walking"),
            fake_study("s2", indicators=["Walkability Index"],
                       access="Restricted - application", task="walking"),
            fake_study("s3", indicators=["Fruit intake"], access="Public", task="fruit"),
        ]
        self.index = bm25.build_index(self.studies)

    def test_full_matches_come_first(self):
        tags = {"indicator": ["Walkability Index"], "access": ["Public"]}
        ranked = rank.rank("walking", tags, self.studies, FAKE_META, self.index, k=None)
        self.assertEqual(ranked[0].id, "s1")
        self.assertEqual((ranked[0].matched, ranked[0].asked), (2, 2))
        self.assertEqual(rank.full_matches(ranked), 1)

    def test_partial_matches_still_appear(self):
        tags = {"indicator": ["Walkability Index"], "access": ["Public"]}
        ids = [r.id for r in
               rank.rank("walking", tags, self.studies, FAKE_META, self.index, k=None)]
        self.assertIn("s2", ids)

    def test_non_matching_source_is_dropped(self):
        tags = {"indicator": ["Walkability Index"]}
        ids = [r.id for r in
               rank.rank("walking", tags, self.studies, FAKE_META, self.index, k=None)]
        self.assertNotIn("s3", ids)   # no tag match and no word in common

    def test_no_tags_falls_back_to_word_matching(self):
        ranked = rank.rank("fruit", {g: [] for g in vocab.GROUPS}, self.studies,
                           FAKE_META, self.index, k=None)
        self.assertEqual([r.id for r in ranked],
                         [d for d, _ in self.index.rank("fruit", 10)])
        self.assertEqual(rank.full_matches(ranked), 0)

    def test_health_focus_is_a_tree(self):
        # s1 carries no focus of its own, only a measure filed under one.
        tags = {"domain": ["Physical Activity"]}
        ids = [r.id for r in
               rank.rank("walking", tags, self.studies, FAKE_META, self.index, k=None)]
        self.assertEqual(ids[:2], ["s1", "s2"])

    def test_measure_outweighs_other_groups(self):
        weights = config.GROUP_WEIGHTS
        self.assertGreater(weights["indicator"], weights["access"])

    def test_k_limits_the_list(self):
        tags = {"indicator": ["Walkability Index"]}
        ranked = rank.rank("walking", tags, self.studies, FAKE_META, self.index, k=1)
        self.assertEqual(len(ranked), 1)


class Bm25Tests(unittest.TestCase):
    def test_idf_on_a_toy_corpus(self):
        index = bm25.BM25({"a": "walk walk park", "b": "park food", "c": "food"})
        self.assertEqual(round(index.idf("walk"), 4), 0.9808)

    def test_rare_word_beats_common_one(self):
        index = bm25.BM25({"a": "walk park", "b": "park park park"})
        self.assertEqual(index.rank("walk")[0][0], "a")

    def test_unknown_word_scores_nothing(self):
        index = bm25.BM25({"a": "walk park"})
        self.assertEqual(index.rank("helicopter"), [])

    def test_tokenize_drops_punctuation(self):
        self.assertEqual(bm25.tokenize("Walk, don't run!"), ["walk", "don", "t", "run"])


class FreezeTests(unittest.TestCase):
    """--freeze: check the questions, score them again, and lock the file.

    make_testset writes to one fixed path, so each test points that path, and the
    abstracts beside it, at a temporary folder.
    """

    def setUp(self):
        import make_testset
        self.mt = make_testset
        quiet = mock.patch("builtins.print")   # keep the test output readable
        quiet.start()
        self.addCleanup(quiet.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        out = Path(self.dir.name) / "testset.json"
        for name, value in (("OUT", out),
                            ("ABSTRACTS", Path(self.dir.name) / "abstracts.json")):
            patch = mock.patch.object(self.mt, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.out = out
        # Two real sources, so describe() has something to compare a question with.
        self.studies = corpus.load_studies()[:2]
        patch = mock.patch.object(self.mt, "load_studies", lambda: self.studies)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, items, frozen=False):
        payload = {"frozen": frozen, "generator": "test", "generated": "2026-10-04",
                   "items": items}
        with open(self.out, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return payload

    def read(self):
        with open(self.out, encoding="utf-8") as f:
            return json.load(f)

    def item(self, qid, target, question, leak=0.5):
        return {"qid": qid, "target": target, "question": question,
                "basis": "summary", "leak": leak, "attempts": 1}

    def test_freezes_and_stamps_the_day(self):
        self.write([self.item("t001", self.studies[0]["id"], "Does prevention save money?")])
        self.assertEqual(self.mt.freeze(), 0)
        saved = self.read()
        self.assertTrue(saved["frozen"])
        self.assertEqual(saved["frozen_on"], date.today().isoformat())

    def test_rescores_an_edited_question(self):
        # The recorded score describes the old wording; freezing must redo it.
        self.write([self.item("t001", self.studies[0]["id"],
                              "Does prevention save money?", leak=0.999)])
        self.mt.freeze()
        self.assertLess(self.read()["items"][0]["leak"], 0.9)

    def test_score_matches_a_direct_calculation(self):
        study = self.studies[0]
        question = "Does spending on prevention save the health system money?"
        self.write([self.item("t001", study["id"], question, leak=0.0)])
        self.mt.freeze()
        description, _ = self.mt.describe(study, {})
        self.assertAlmostEqual(self.read()["items"][0]["leak"],
                               round(self.mt.leak_score(question, description), 3))

    def test_already_frozen_is_left_alone(self):
        self.write([self.item("t001", self.studies[0]["id"], "A question?", leak=0.2)],
                   frozen=True)
        self.assertEqual(self.mt.freeze(), 0)
        self.assertEqual(self.read()["items"][0]["leak"], 0.2)   # untouched

    def test_refuses_an_empty_question(self):
        self.write([self.item("t001", self.studies[0]["id"], "   ")])
        self.assertEqual(self.mt.freeze(), 1)
        self.assertFalse(self.read()["frozen"])

    def test_refuses_an_unknown_source(self):
        self.write([self.item("t001", "app9-99", "A question?")])
        self.assertEqual(self.mt.freeze(), 1)
        self.assertFalse(self.read()["frozen"])

    def test_refuses_a_repeated_qid(self):
        self.write([self.item("t001", self.studies[0]["id"], "One?"),
                    self.item("t001", self.studies[1]["id"], "Two?")])
        self.assertEqual(self.mt.freeze(), 1)
        self.assertFalse(self.read()["frozen"])

    def test_tidies_whitespace(self):
        self.write([self.item("t001", self.studies[0]["id"], "  Does  it\nwork? ")])
        self.mt.freeze()
        self.assertEqual(self.read()["items"][0]["question"], "Does it work?")

    def test_no_file(self):
        self.assertEqual(self.mt.freeze(), 1)


class LoadRunsTests(unittest.TestCase):
    """--summary and --compare read the newest run of each method only."""

    def setUp(self):
        import evaluate
        self.ev = evaluate
        quiet = mock.patch("builtins.print")   # keep the test output readable
        quiet.start()
        self.addCleanup(quiet.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.results = Path(self.dir.name)
        patch = mock.patch.object(self.ev, "RESULTS", self.results)
        patch.start()
        self.addCleanup(patch.stop)

    def run_file(self, method, date_, model=None, r5=0.5):
        name = "%s-%s%s.json" % (date_, method, "-" + model.replace("/", "-") if model else "")
        payload = {"meta": {"method": method, "model": model, "date": date_},
                   "summary": {"R@5": r5}, "testset": [], "examples": []}
        with open(self.results / name, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_keeps_only_the_newest_of_each(self):
        self.run_file("bm25", "2026-09-29", r5=0.1)
        self.run_file("bm25", "2026-10-06", r5=0.9)
        runs = self.ev.load_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["meta"]["date"], "2026-10-06")

    def test_variants_of_one_model_are_kept_apart(self):
        # Without this a staged run would silently replace a flat one of the same day.
        for mapper in ("flat", "staged"):
            for ranker in ("tags-first", "bm25-first"):
                payload = {"meta": {"method": "llm", "model": "qwen/a",
                                    "date": "2026-10-09", "mapper": mapper,
                                    "ranker": ranker},
                           "summary": {"R@5": 0.5}, "testset": [], "examples": []}
                name = "2026-10-09-llm-qwen-a-%s-%s.json" % (mapper, ranker)
                with open(self.results / name, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
        self.assertEqual(len(self.ev.load_runs()), 4)

    def test_the_label_names_the_variant(self):
        run = {"meta": {"method": "llm", "model": "qwen/qwen3-4b", "mapper": "staged",
                        "ranker": "bm25-first"}}
        self.assertEqual(self.ev.label(run), "qwen3-4b staged bm25-first")
        self.assertEqual(self.ev.label({"meta": {"method": "bm25", "model": None}}),
                         "bm25")

    def test_models_are_counted_separately(self):
        self.run_file("llm", "2026-10-06", model="qwen/a")
        self.run_file("llm", "2026-10-06", model="qwen/b")
        self.assertEqual(len(self.ev.load_runs()), 2)

    def test_all_dates_keeps_everything(self):
        self.run_file("bm25", "2026-09-29")
        self.run_file("bm25", "2026-10-06")
        self.assertEqual(len(self.ev.load_runs(all_dates=True)), 2)

    def test_part_finished_and_scratch_files_ignored(self):
        self.run_file("bm25", "2026-10-06")
        for name in ("2026-10-06-llm.partial.json", "_keyword_in.json"):
            with open(self.results / name, "w", encoding="utf-8") as f:
                json.dump({"meta": {"method": "x", "model": None, "date": "2026-10-06"}}, f)
        self.assertEqual(len(self.ev.load_runs()), 1)

    def test_a_stray_json_file_is_skipped_not_crashed_on(self):
        self.run_file("bm25", "2026-10-06")
        with open(self.results / "notes.json", "w", encoding="utf-8") as f:
            json.dump({"something": "else"}, f)
        with open(self.results / "broken.json", "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual(len(self.ev.load_runs()), 1)

    def test_runs_older_than_the_freeze_are_named(self):
        frozen = {"frozen": True, "frozen_on": "2026-10-05", "items": []}
        path = Path(self.dir.name) / "testset.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(frozen, f)
        with mock.patch.object(self.ev, "TESTSET", path):
            runs = [{"meta": {"method": "bm25", "model": None, "date": "2026-09-29"},
                     "summary": {"R@5": 0.5}},
                    {"meta": {"method": "keyword", "model": None, "date": "2026-10-06"},
                     "summary": {"R@5": 0.0}}]
            stale = self.ev.stale_runs(runs)
            self.assertEqual([r["meta"]["method"] for r in stale], ["bm25"])
            lines = []
            self.assertTrue(self.ev.warn_about_dates(runs, lines))
            self.assertIn("not comparable", "\n".join(lines))

    def test_no_warning_when_every_run_is_recent(self):
        frozen = {"frozen": True, "frozen_on": "2026-10-05", "items": []}
        path = Path(self.dir.name) / "testset.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(frozen, f)
        with mock.patch.object(self.ev, "TESTSET", path):
            runs = [{"meta": {"method": "bm25", "model": None, "date": "2026-10-06"},
                     "summary": {"R@5": 0.5}}]
            lines = []
            self.assertFalse(self.ev.warn_about_dates(runs, lines))
            self.assertEqual(lines, [])


class ModelTableTests(unittest.TestCase):
    """--models: the two models side by side."""

    def setUp(self):
        import evaluate
        self.ev = evaluate
        quiet = mock.patch("builtins.print")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.results = Path(self.dir.name) / "results"
        self.results.mkdir()
        # Beside the results folder, as data/testset.json is beside it for real.
        testset = Path(self.dir.name) / "testset.json"
        with open(testset, "w", encoding="utf-8") as f:
            json.dump({"frozen": True, "items": [
                {"qid": "t001", "leak": 0.10},     # genuinely vague
                {"qid": "t002", "leak": 0.50},     # reuses its source's wording
            ]}, f)
        for name, value in (("RESULTS", self.results), ("TESTSET", testset)):
            patch = mock.patch.object(self.ev, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def write_llm(self, model, r5, tags, out_of_scope_tags, secs=(2.0, 4.0)):
        payload = {
            "meta": {"method": "llm", "model": model, "date": "2026-10-06"},
            "summary": {"R@1": 0.1, "R@5": r5, "R@10": 0.8, "MRR@10": 0.3,
                        "empty": 0.0, "sec": secs[0], "tags": tags, "no_tags": 0.0,
                        "rejected": 0, "errors": 0, "too_narrow": 0.3},
            # t001 found, t002 missed, so the vague band scores 1.0 and the other 0.0
            "testset": [{"qid": "t001", "rank_of_target": 2, "seconds": secs[0]},
                        {"qid": "t002", "rank_of_target": None, "seconds": secs[1]}],
            "examples": [{"qid": "e7", "tags": out_of_scope_tags}],
        }
        name = "2026-10-06-llm-%s.json" % model.replace("/", "-")
        with open(self.results / name, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def table(self):
        self.assertEqual(self.ev.write_models(), 0)
        with open(self.results / "models.md", encoding="utf-8") as f:
            return f.read()

    def test_needs_two_models(self):
        self.write_llm("qwen/big", 0.6, 4.5, {})
        self.assertEqual(self.ev.write_models(), 1)

    def test_better_model_goes_in_the_first_column(self):
        self.write_llm("qwen/small", 0.3, 5.8, {"domain": ["Mental Wellbeing"]})
        self.write_llm("qwen/big", 0.6, 4.5, {})
        header = [l for l in self.table().splitlines() if l.startswith("| | qwen")][0]
        self.assertLess(header.index("qwen/big"), header.index("qwen/small"))

    def test_scores_are_all_to_three_places(self):
        self.write_llm("qwen/small", 0.3, 5.8, {})
        self.write_llm("qwen/big", 0.7, 4.5, {})
        row = [l for l in self.table().splitlines() if l.startswith("| In the top ten")][0]
        self.assertIn("0.800", row)          # not "0.8"

    def test_out_of_scope_behaviour_is_named(self):
        self.write_llm("qwen/small", 0.3, 5.8,
                       {"domain": ["Mental Wellbeing"], "indicator": ["Park utilisation"]})
        self.write_llm("qwen/big", 0.6, 4.5, {})
        row = [l for l in self.table().splitlines() if "Out-of-scope" in l][0]
        self.assertIn("chose nothing, correctly", row)
        self.assertIn("chose 2 tags anyway", row)

    def test_vague_band_is_scored_separately(self):
        self.write_llm("qwen/small", 0.3, 5.8, {})
        self.write_llm("qwen/big", 0.6, 4.5, {})
        text = self.table()
        vague = [l for l in text.splitlines() if l.startswith("| genuinely vague")][0]
        reuses = [l for l in text.splitlines() if l.startswith("| reuses")][0]
        self.assertIn("(1)", vague)          # one question in that band
        self.assertIn("1.000", vague)        # t001 was found by both
        self.assertIn("0.000", reuses)       # t002 was missed by both

    def test_other_methods_are_left_out(self):
        self.write_llm("qwen/small", 0.3, 5.8, {})
        self.write_llm("qwen/big", 0.6, 4.5, {})
        with open(self.results / "2026-10-06-bm25.json", "w", encoding="utf-8") as f:
            json.dump({"meta": {"method": "bm25", "model": None, "date": "2026-10-06"},
                       "summary": {"R@5": 0.9}, "testset": [], "examples": []}, f)
        self.assertNotIn("bm25", self.table())


def server_up():
    try:
        llm.list_models()
        return True
    except llm.LLMError:
        return False


class LiveModelTests(unittest.TestCase):
    """Needs LM Studio running with the main model loaded; skipped otherwise."""

    @unittest.skipUnless(server_up(), "LM Studio not running")
    def test_walkability_question_maps_to_real_tags(self):
        meta = vocab.load_meta()
        real = vocab.load_vocab(meta)
        result = map_query.map_query(
            "what public data is available for walkability in this region",
            config.MODEL_MAIN, meta, real)
        self.assertEqual(result.error, "")
        self.assertEqual(result.rejected, [])
        self.assertTrue(result.chosen(), "the model chose no tags at all")
        for group, values in result.chosen().items():
            for value in values:
                self.assertIn(value, real[group])


if __name__ == "__main__":
    unittest.main()
