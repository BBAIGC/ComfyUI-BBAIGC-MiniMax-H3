"""CPU stubs verify prompt-only routing; no model, browser, or server is started."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import torch


PLUGIN = Path(__file__).resolve().parents[1]
WORKFLOWS = Path(os.environ.get("BBAIGC_TEST_WORKFLOW_DIR", Path(__file__).parent / "fixtures"))


def load_module(name):
    spec = importlib.util.spec_from_file_location("prompt_regenerate_fixture." + name, PLUGIN / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PromptOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name)
        package = types.ModuleType("prompt_regenerate_fixture")
        package.__path__ = [str(PLUGIN)]
        self.nodes = types.ModuleType("nodes")
        self.nodes.NODE_CLASS_MAPPINGS = {}
        self.nodes.UNETLoader = lambda: types.SimpleNamespace(load_unet=lambda *a: ("model",))
        self.nodes.CLIPLoader = lambda: types.SimpleNamespace(load_clip=lambda *a: ("clip",))
        self.nodes.VAELoader = lambda: types.SimpleNamespace(load_vae=lambda *a: ("vae",))
        self.nodes.VAEEncode = Mock(side_effect=AssertionError("reference latent must not be encoded"))
        self.nodes.VAEDecode = lambda: types.SimpleNamespace(decode=lambda *a: (torch.zeros((1, 64, 64, 3)),))
        self.folder = types.ModuleType("folder_paths")
        self.folder.get_output_directory = lambda: str(self.output)
        self.folder.get_temp_directory = lambda: str(self.output)
        self.folder.get_full_path = lambda *args: None
        resource = types.ModuleType("prompt_regenerate_fixture.resource_control")
        resource.release_resources = lambda **kwargs: None
        mm = types.ModuleType("comfy.model_management")
        mm.throw_exception_if_processing_interrupted = lambda: None
        mm.intermediate_device = lambda: "cpu"
        comfy = types.ModuleType("comfy")
        comfy.__path__ = []
        comfy.model_management = mm
        self.sample = Mock(side_effect=lambda **kwargs: (kwargs["latent"],))
        sampling = types.ModuleType("prompt_regenerate_fixture.image_sampling")
        sampling.sample_image2 = self.sample
        self.module_patch = patch.dict(sys.modules, {"prompt_regenerate_fixture": package, "nodes": self.nodes, "folder_paths": self.folder,
            "prompt_regenerate_fixture.resource_control": resource, "comfy": comfy, "comfy.model_management": mm,
            "prompt_regenerate_fixture.image_sampling": sampling})
        self.module_patch.start()
        self.bridge = load_module("image_bridge")
        self.tools = load_module("image_tools")
        self.read_reference = Mock(side_effect=AssertionError("reference pixels must not enter generation"))
        self.encoder = Mock(return_value=("positive", "negative"))
        def node_class(name):
            if name == "UnetLoaderGGUF":
                return lambda: types.SimpleNamespace(load_unet=lambda *a: ("model",))
            if name == "QwenImage21Cache":
                return types.SimpleNamespace(execute=lambda **kwargs: (kwargs["model"],))
            if name == "TextEncodeQwenImage21":
                return types.SimpleNamespace(execute=self.encoder)
            raise AssertionError(name)
        self.patches = [patch.object(self.bridge, "_check_weights"), patch.object(self.bridge, "_class", side_effect=node_class),
                        patch.object(self.bridge, "_read_image", self.read_reference), patch.object(self.tools, "_cached_image", return_value=None),
                        patch.object(self.tools, "_save_cached", side_effect=lambda image, key: self.output / "saved.png"),
                        patch.object(self.tools, "load_tensor", return_value=torch.zeros((1, 64, 64, 3))),
                        patch.object(self.tools, "tensor_path", side_effect=AssertionError("generator must not serialize a reference"))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.module_patch.stop()
        for name in list(sys.modules):
            if name.startswith("prompt_regenerate_fixture"):
                sys.modules.pop(name, None)
        self.temporary.cleanup()

    def test_image21_encoder_has_no_reference_and_sampler_starts_empty(self):
        self.tools.CodexImageGenerate().generate("a blue ceramic cup", "Image2.1", 64, 64, 42, 20, 1, "euler", "simple",
                                               参考图=torch.ones((1, 64, 64, 3)), Image2配置={"denoise": .2})
        self.read_reference.assert_not_called()
        self.nodes.VAEEncode.assert_not_called()
        self.assertEqual(self.encoder.call_args.kwargs["images"], {})
        self.assertEqual(self.encoder.call_args.kwargs["prompt"], "a blue ceramic cup")
        sampled = self.sample.call_args.kwargs
        self.assertEqual(tuple(sampled["latent"]["samples"].shape), (1, 64, 4, 4))
        self.assertEqual(torch.count_nonzero(sampled["latent"]["samples"]).item(), 0)
        self.assertEqual(sampled["denoise"], 1)

    def test_zimage_is_plain_clip_text_and_empty_latent(self):
        encode = Mock(return_value=("positive",))
        sample = Mock(return_value=({"samples": torch.zeros((1, 16, 8, 8))},))
        self.nodes.CLIPTextEncode = lambda: types.SimpleNamespace(encode=encode)
        self.nodes.KSampler = lambda: types.SimpleNamespace(sample=sample)
        latent = {"samples": torch.zeros((1, 16, 8, 8))}
        def call(name, **kwargs):
            return ({"ConditioningZeroOut": "negative", "ModelSamplingAuraFlow": "model", "EmptySD3LatentImage": latent}[name],)
        with patch.object(self.tools, "_call", side_effect=call) as native:
            self.tools.CodexImageGenerate().generate("a blue ceramic cup", "Z-Image Turbo", 64, 64, 42, 20, 1, "euler", "simple", 参考图=torch.ones((1, 64, 64, 3)))
        encode.assert_called_once_with("clip", "a blue ceramic cup")
        self.assertIs(sample.call_args.args[8], latent)
        self.assertEqual(sample.call_args.kwargs["denoise"], 1)
        self.assertIn("EmptySD3LatentImage", [c.args[0] for c in native.call_args_list])
        self.nodes.VAEEncode.assert_not_called()

    def test_existing_reference_edit_bridge_still_receives_images(self):
        reference = torch.ones((1, 64, 64, 3))
        with patch.object(self.bridge, "_read_image", return_value=reference) as read:
            self.bridge.generate_reference("keep the cup and change background", "cup.png", 64, 64, settings=None)
        read.assert_called_once()
        self.assertIs(self.encoder.call_args.kwargs["images"]["image_1"], reference)


class WorkflowGraphTests(unittest.TestCase):
    def test_both_reverse_prompt_graphs_have_no_generation_reference_link(self):
        paths = sorted(WORKFLOWS.glob("反推*Codex_V1.1.0.json"))
        if len(paths) != 2:
            self.skipTest("Set BBAIGC_TEST_WORKFLOW_DIR to the matching fixtures.")
        for path in paths:
            workflow = json.loads(path.read_text(encoding="utf-8-sig"))
            nodes = {n["id"]: n for n in workflow["nodes"]}
            self.assertEqual(len(nodes), len(workflow["nodes"]), path.name)
            links = {link[0]: link for link in workflow["links"]}
            self.assertEqual(len(links), len(workflow["links"]), path.name)
            for identifier, source_id, source_slot, target_id, target_slot, _ in links.values():
                self.assertIn(identifier, nodes[source_id]["outputs"][source_slot]["links"])
                self.assertEqual(nodes[target_id]["inputs"][target_slot]["link"], identifier)
            generate = nodes[410]
            self.assertEqual(generate["type"], "CodexImageGenerate")
            self.assertIsNone(next(i for i in generate["inputs"] if i["name"] == "参考图")["link"])
            self.assertIsNotNone(next(i for i in nodes[442]["inputs"] if i["name"] == "参考图")["link"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
