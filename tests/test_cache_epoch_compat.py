"""Exercise the installed ComfyUI validation code on CPU; never execute models."""
import asyncio
import copy
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

CORE = Path(os.environ.get("COMFYUI_CORE", Path(__file__).resolve().parents[3])).resolve()
if not (CORE / "execution.py").is_file():
    raise unittest.SkipTest("Set COMFYUI_CORE to run the installed core validation checks.")
PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))
with patch.object(sys, "argv", ["epoch_compat_offline", "--cpu"]):
    import execution
import nodes


def load_plugin_part(name):
    spec = importlib.util.spec_from_file_location("epoch_compat_fixture." + name, PLUGIN / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


package = types.ModuleType("epoch_compat_fixture")
package.__path__ = [str(PLUGIN)]
sys.modules[package.__name__] = package
resource = types.ModuleType(package.__name__ + ".resource_control")
resource.release_resources = lambda **kwargs: None
sys.modules[resource.__name__] = resource
prompt_nodes = types.ModuleType(package.__name__ + ".prompt_nodes")
prompt_nodes.local_models = lambda: ["qwen3.5-fixture"]
prompt_nodes.LocalOllama = lambda: (_ for _ in ()).throw(AssertionError("no model must start"))
sys.modules[prompt_nodes.__name__] = prompt_nodes
cache = load_plugin_part("description_cache")
tools = load_plugin_part("image_tools")


def normalize(prompt):
    return cache.normalize_prompt_epochs({'prompt': prompt})['prompt']


class FixtureImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}
    RETURN_TYPES = ("IMAGE",)


class FixtureEpoch:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}
    RETURN_TYPES = ("INT",)


class EpochCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.mapping = patch.dict(nodes.NODE_CLASS_MAPPINGS, {"EpochFixtureImage": FixtureImage, "EpochFixtureInt": FixtureEpoch,
            "CodexReferenceDescribe": tools.CodexReferenceDescribe, "CodexJoyCaptionDescribe": tools.CodexJoyCaptionDescribe})
        self.mapping.start()

    def tearDown(self):
        self.mapping.stop()

    def prompt(self, epoch, joy=False):
        if joy:
            inputs = {"参考图": ["source", 0], "模型": "fixture", "显存模式": "Maximum Savings (4-bit)", "保持加载": False,
                      "system_prompt": "visible facts", "user_query": "describe", "max_new_tokens": 768, "temperature": .35, "top_p": .9, "top_k": 0}
            class_type = "CodexJoyCaptionDescribe"
        else:
            inputs = {"参考图": ["source", 0], "模型": "qwen3.5-fixture", "前置规则": "visible facts", "补充要求": "", "读取长边": 1024,
                      "输出上限": 1024, "上下文": 8192, "温度": .15, "top_p": .9}
            class_type = "CodexReferenceDescribe"
        inputs["清除缓存批次"] = epoch
        return {"source": {"class_type": "EpochFixtureImage", "inputs": {}}, "400": {"class_type": class_type, "inputs": inputs}}

    def validate(self, prompt):
        # Actual installed execution.validate_inputs, not a copy or a validation mock.
        self.assertEqual(Path(execution.__file__).resolve(), CORE / "execution.py")
        return asyncio.run(execution.validate_inputs("offline-epoch-test", prompt, "400", {}))

    def test_reproduce_raw_legacy_failure_then_real_core_accepts_normalized(self):
        for joy in (False, True):
            with self.subTest(joy=joy):
                prompt = self.prompt([False, True], joy)
                with self.assertRaises((KeyError, TypeError, IndexError)):
                    self.validate(copy.deepcopy(prompt))
                fixed = normalize(prompt)
                self.assertEqual(fixed["400"]["inputs"]["清除缓存批次"], 0)
                result = self.validate(fixed)
                self.assertTrue(result[0], result[1])

    def test_typed_wrapper_reproduces_invalid_int_then_passes(self):
        prompt = self.prompt({"__value__": [False, True]})
        result = self.validate(copy.deepcopy(prompt))
        self.assertFalse(result[0])
        self.assertIn("invalid_input_type", [error["type"] for error in result[1]])
        result = self.validate(normalize(prompt))
        self.assertTrue(result[0], result[1])

    def test_normalized_integer_and_numeric_string_preserve_epoch(self):
        for value, expected in ((0, 0), (17, 17), ("23", 23), ([29], 29), ({"__value__": ["31"]}, 31), (None, 0), (True, 0)):
            with self.subTest(value=value):
                prompt = normalize(self.prompt(value))
                self.assertEqual(prompt["400"]["inputs"]["清除缓存批次"], expected)
                self.assertTrue(self.validate(prompt)[0])

    def test_unknown_values_fail_clearly_in_actual_core(self):
        for value in ([3, 7], [True, False], [], {"unknown": 1}, -1, .5, "unknown", 2147483648):
            with self.subTest(value=value):
                result = self.validate(normalize(self.prompt(value)))
                self.assertFalse(result[0])
                self.assertIn("invalid_input_type", [error["type"] for error in result[1]])

    def test_real_links_and_other_node_inputs_unchanged(self):
        prompt = self.prompt(["epoch", 0])
        prompt["epoch"] = {"class_type": "EpochFixtureInt", "inputs": {}}
        prompt["other"] = {"class_type": "Unrelated", "inputs": {"清除缓存批次": [False, True]}}
        normalize(prompt)
        self.assertEqual(prompt["400"]["inputs"]["清除缓存批次"], ["epoch", 0])
        self.assertEqual(prompt["other"]["inputs"]["清除缓存批次"], [False, True])
        self.assertTrue(self.validate(prompt)[0])

    def test_other_inputs_still_use_core_type_range_and_combo_validation(self):
        for name, value, error_type in (("读取长边", 4, "value_smaller_than_min"), ("读取长边", "bad", "invalid_input_type"), ("模型", "missing", "value_not_in_list")):
            prompt = self.prompt([False, True])
            prompt["400"]["inputs"][name] = value
            result = self.validate(normalize(prompt))
            self.assertFalse(result[0])
            self.assertIn(error_type, [error["type"] for error in result[1]])

    def test_cached_execution_and_ui_result_use_same_normalized_epoch(self):
        with patch.object(tools, "read_description", return_value="cached visible facts"), patch.object(tools, "tensor_path", side_effect=AssertionError("cache hit must not read image")):
            result = tools.CodexReferenceDescribe().describe(None, "fixture", "rules", "", 1024, 1024, 8192, .15, .9, [False, True])
            self.assertEqual(result["ui"]["codex_description_cache"][0]["epoch"], 0)
            result = tools.CodexJoyCaptionDescribe().describe(None, "fixture", "4bit", False, "rules", "query", 768, .35, .9, 0, [41])
            self.assertEqual(result["ui"]["codex_description_cache"][0]["epoch"], 41)
            self.assertTrue(result["ui"]["codex_description_cache"][0]["cache_hit"])
        self.assertEqual(cache.description_result("cached", {"__value__": [False, True]}, True)["ui"]["codex_description_cache"][0]["epoch"], 0)

    def test_handler_registration_is_idempotent(self):
        instance = types.SimpleNamespace(add_on_prompt_handler=lambda handler: handlers.append(handler))
        handlers = []
        fake_server = types.ModuleType("server")
        fake_server.PromptServer = types.SimpleNamespace(instance=instance)
        with patch.dict(sys.modules, {"server": fake_server}):
            cache.register_epoch_normalizer()
            cache.register_epoch_normalizer()
        self.assertEqual(handlers, [cache.normalize_prompt_epochs])


if __name__ == "__main__":
    unittest.main(verbosity=2)
