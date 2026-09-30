"""Tests for the search prototype.

Run from the repository root:
    python -m unittest discover -s search -p "test_*.py" -v

Most tests build small made-up sources rather than using the real ones, so adding a
source to the scan cannot break them. The few that do read the real meta.json are
there to catch the vocabulary changing shape.
"""
import json
import sys
import unittest
import urllib.request
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
