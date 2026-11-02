"""Prompt validation and bounded repair with CPU-only local-model stubs."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


PLUGIN = Path(__file__).resolve().parents[1]
MODEL = "qwen-test:local"
ENGLISH = "A blue ceramic cup sits on a wooden table beside an open book with soft natural light."


def response(text, reason="stop"):
    return {"message": {"content": json.dumps({"prompt": text}, ensure_ascii=False)},
            "done_reason": reason, "eval_count": 40, "prompt_eval_count": 100,
            "load_duration": 10, "prompt_eval_duration": 20, "eval_duration": 30}


class LocalModelStub:
    def __init__(self):
        self.responses = []
        self.calls = []
        self.start = Mock()
        self.unload = Mock()
        self.close_owned = Mock()

    def request(self, endpoint, payload, **kwargs):
        self.calls.append((endpoint, copy.deepcopy(payload), kwargs))
        if not self.responses:
            raise AssertionError("Unexpected extra local-model request")
        return self.responses.pop(0)


class PromptOptimizeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.client = LocalModelStub()
        self.release = Mock()
        package = types.ModuleType("prompt_optimize_fixture")
        package.__path__ = [str(PLUGIN)]
        prompt_nodes = types.ModuleType("prompt_optimize_fixture.prompt_nodes")
        prompt_nodes.LocalOllama = lambda: self.client
        prompt_nodes.local_models = lambda: [MODEL]
        resources = types.ModuleType("prompt_optimize_fixture.resource_control")
        resources.release_resources = self.release
        metadata = types.ModuleType("prompt_optimize_fixture.output_metadata")
        metadata.remember_prompt_result = Mock(side_effect=lambda prompt, node, source, text, **kwargs: source)
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_user_directory = lambda: str(self.root)
        self.modules = patch.dict(sys.modules, {"prompt_optimize_fixture": package,
            prompt_nodes.__name__: prompt_nodes, resources.__name__: resources,
            metadata.__name__: metadata, "folder_paths": folder_paths})
        self.modules.start()
        name = "prompt_optimize_fixture.prompt_optimize"
        spec = importlib.util.spec_from_file_location(name, PLUGIN / "prompt_optimize.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.module
        spec.loader.exec_module(self.module)
        self.node = self.module.CodexPromptOptimize()

    def tearDown(self):
        self.modules.stop()
        self.temporary.cleanup()

    def optimize(self, source="桌上的蓝色陶瓷杯和摊开的书，保持自然光照。", **kwargs):
        options = {"原始提示词": source, "启用优化": True, "语言模型": MODEL, "输出语言": "中文"}
        options.update(kwargs)
        return self.node.optimize(**options)

    def stats(self, result):
        return result["ui"]["codex_prompt_stats"][0]

    def assert_closed(self):
        self.client.unload.assert_called_once_with(MODEL)
        self.client.close_owned.assert_called_once_with()

    def assert_no_cache(self):
        self.assertEqual(list(self.root.rglob("*.json")), [])
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_valid_first_result_normalizes_reference_without_extra_inference(self):
        self.client.responses = [response("图1中的蓝色杯子放在木桌上，旁边是摊开的书。")]
        result = self.optimize("<image1>中的杯子放在木桌上，旁边是摊开的书。")
        self.assertEqual(result["result"], ("<image1>中的蓝色杯子放在木桌上，旁边是摊开的书。",))
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.stats(result)["correction_passes"], 0)
        self.assert_closed()

    def test_language_repair_is_independent_and_preserves_inference_budget(self):
        self.client.responses = [response(ENGLISH), response("蓝色陶瓷杯放在木桌上，旁边是摊开的书，光线柔和自然。")]
        result = self.optimize(前置规则="CUSTOM_OPTIMIZATION_RULE_SENTINEL", 种子=0,
                               温度=.45, top_p=.65, 输出上限=2048, 上下文=12288)
        self.assertEqual(self.stats(result)["correction_passes"], 1)
        repair = self.client.calls[1][1]
        self.assertEqual([m["role"] for m in repair["messages"]], ["system", "user"])
        self.assertNotIn("CUSTOM_OPTIMIZATION_RULE_SENTINEL", json.dumps(repair["messages"], ensure_ascii=False))
        self.assertIn(ENGLISH, json.dumps(json.loads(repair["messages"][1]["content"]), ensure_ascii=False))
        self.assertEqual(repair["options"]["temperature"], 0)
        self.assertEqual(repair["options"]["top_p"], .9)
        self.assertEqual(repair["options"]["seed"], 0)
        self.assertEqual(repair["options"]["num_ctx"], 12288)
        self.assertEqual(repair["options"]["num_predict"], 2048)
        self.assert_closed()

    def test_invented_reference_is_repaired_before_result_is_cached(self):
        source = "<image1>中的杯子放在桌上，保持杯子的颜色。"
        final = "<image1>中的杯子放在桌上，杯子的颜色保持不变。"
        self.client.responses = [response("<image1>中的杯子放在桌上，图3提供木桌。"), response(final)]
        result = self.optimize(source)
        self.assertEqual(result["result"], (final,))
        self.assertEqual(self.stats(result)["correction_passes"], 1)
        cached = list(self.root.rglob("*.json"))
        self.assertEqual(len(cached), 1)
        self.assertEqual(json.loads(cached[0].read_text(encoding="utf-8"))["text"], final)
        self.assert_closed()

    def test_omitted_reference_is_repaired_without_renumbering_the_others(self):
        source = "<image1>中的杯子放在<image2>中的桌子上，保持杯子与桌子的颜色。"
        final = "将<image1>的杯子放在<image2>的桌子上，保持两者的颜色。"
        self.client.responses = [response("<image1>中的杯子放在桌上，颜色保持不变。"), response(final)]
        result = self.optimize(source)
        self.assertEqual(result["result"], (final,))
        self.assertEqual(len(self.client.calls), 2)
        self.assert_closed()

    def test_reference_free_source_rejects_and_repairs_an_invented_slot(self):
        final = "蓝色陶瓷杯放在木桌上，旁边是摊开的书。"
        self.client.responses = [response("<image3>中的蓝色杯子放在木桌上。"), response(final)]
        result = self.optimize("蓝色陶瓷杯放在木桌上，旁边是摊开的书。")
        self.assertEqual(result["result"], (final,))
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(self.stats(result)["correction_passes"], 1)
        self.assert_closed()

    def test_second_repair_handles_a_new_validation_error(self):
        source = "<image1>中的杯子放在桌上，旁边是摊开的书。"
        final = "<image1>中的杯子放在木桌上，旁边是摊开的书，保持自然光照。"
        self.client.responses = [response("<image1> " + ENGLISH),
                                 response("<image1>中的杯子放在图3的桌上，旁边是摊开的书。"), response(final)]
        result = self.optimize(source)
        self.assertEqual(result["result"], (final,))
        self.assertEqual(self.stats(result)["correction_passes"], 2)
        self.assertEqual(len(self.client.calls), 3)
        self.assert_closed()

    def test_repeated_wrong_language_stops_after_bounded_repair_and_never_caches(self):
        self.client.responses = [response(ENGLISH) for _ in range(3)]
        with self.assertRaises(ValueError):
            self.optimize()
        self.assertEqual(len(self.client.calls), 3)
        self.assert_no_cache()
        self.assert_closed()

    def test_repeated_wrong_references_never_escape_to_generation_or_cache(self):
        self.client.responses = [response("<image1>的杯子与图3的书都放在桌上。") for _ in range(3)]
        with self.assertRaises(ValueError):
            self.optimize("<image1>中的杯子放在桌上。")
        self.assertEqual(len(self.client.calls), 3)
        self.assert_no_cache()
        self.assert_closed()

    def test_initial_token_truncation_fails_without_attempting_language_repair(self):
        self.client.responses = [response(ENGLISH, "length")]
        with self.assertRaisesRegex(ValueError, "截断"):
            self.optimize()
        self.assertEqual(len(self.client.calls), 1)
        self.assert_no_cache()
        self.assert_closed()

    def test_repair_token_truncation_is_not_saved_as_a_complete_prompt(self):
        self.client.responses = [response(ENGLISH), response("蓝色陶瓷杯放在木桌上。", "max_tokens")]
        with self.assertRaisesRegex(ValueError, "截断"):
            self.optimize()
        self.assertEqual(len(self.client.calls), 2)
        self.assert_no_cache()
        self.assert_closed()

    def test_validated_cache_reuse_does_not_load_or_release_models_again(self):
        final = "蓝色陶瓷杯放在桌上，旁边是摊开的书，保持自然光照。"
        self.client.responses = [response(final)]
        self.optimize()
        self.client.calls.clear()
        self.client.start.reset_mock()
        self.client.unload.reset_mock()
        self.client.close_owned.reset_mock()
        self.release.reset_mock()
        result = self.optimize()
        self.assertEqual(result["result"], (final,))
        self.assertTrue(self.stats(result)["cached"])
        self.assertEqual(self.stats(result)["model_load_ns"], 0)
        self.assertEqual(self.client.calls, [])
        self.client.start.assert_not_called()
        self.client.unload.assert_not_called()
        self.client.close_owned.assert_not_called()
        self.release.assert_not_called()

    def test_changed_source_does_not_reuse_previous_optimization(self):
        self.client.responses = [response("蓝色陶瓷杯放在桌上。"), response("红色书本放在书架上。")]
        first = self.optimize("蓝色陶瓷杯放在桌上。")
        second = self.optimize("红色书本放在书架上。")
        self.assertNotEqual(first["result"], second["result"])
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(len(list(self.root.rglob("*.json"))), 2)

    def test_reference_polluted_cache_is_regenerated_instead_of_returned(self):
        source = "<image1>中的杯子放在桌上，保持颜色。"
        first = "<image1>中的杯子放在木桌上，保持原有颜色。"
        final = "保持<image1>中杯子的颜色，将杯子放在木桌上。"
        self.client.responses = [response(first), response(final)]
        self.optimize(source)
        cached = next(self.root.rglob("*.json"))
        saved = json.loads(cached.read_text(encoding="utf-8"))
        saved["text"] = "<image1>中的杯子放在图3提供的桌子上。"
        cached.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        result = self.optimize(source)
        self.assertEqual(result["result"], (final,))
        self.assertEqual(len(self.client.calls), 2)
        self.assertFalse(self.stats(result)["cached"])
        self.assertEqual(json.loads(cached.read_text(encoding="utf-8"))["text"], final)
        self.assertEqual(self.client.unload.call_count, 2)
        self.assertEqual(self.client.close_owned.call_count, 2)

    def test_disabled_optimization_passes_the_original_text_verbatim(self):
        source = "  <image1>上的蓝色杯子。\n保持原文。  "
        result = self.optimize(source, 启用优化=False)
        self.assertEqual(result["result"], (source,))
        self.assertFalse(self.stats(result)["optimized"])
        self.assertEqual(self.client.calls, [])
        self.release.assert_not_called()
        self.client.start.assert_not_called()
        self.assert_no_cache()

    def test_literal_english_poster_text_remains_legal_in_a_chinese_prompt(self):
        quote = "The blue ceramic cup sits on a wooden table beside an open book in natural light"
        source = '海报上逐字显示文字"' + quote + '"，背景为蓝色陶瓷杯。'
        final = '海报背景是蓝色陶瓷杯，正中逐字显示"' + quote + '"。'
        self.client.responses = [response(final)]
        result = self.optimize(source)
        self.assertEqual(result["result"], (final,))
        self.assertEqual(len(self.client.calls), 1)
        self.assert_closed()


if __name__ == "__main__":
    unittest.main(verbosity=2)
