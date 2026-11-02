"""Per-slot settings must reach real sampler calls; legacy/disabled slots stay inert."""
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class LoraSamplingProfilesTests(unittest.TestCase):
    def setUp(self):
        self.package = types.ModuleType("lora_profile_fixture")
        self.package.__path__ = [str(ROOT)]
        self.folder = types.ModuleType("folder_paths")
        self.nodes = types.ModuleType("nodes")
        self.zero = Mock(return_value=("negative",))
        self.nodes.ConditioningZeroOut = lambda: types.SimpleNamespace(zero_out=self.zero)
        self.mm = types.ModuleType("comfy.model_management")
        self.mm.throw_exception_if_processing_interrupted = Mock()
        self.comfy = types.ModuleType("comfy")
        self.comfy.model_management = self.mm
        self.scheduler = Mock(side_effect=lambda model, scheduler, steps, denoise: (f"sigmas-{steps}",))
        self.basic = Mock(return_value=("basic-guider",))
        self.cfg = Mock(return_value=("cfg-guider",))
        self.sample = Mock(side_effect=lambda *args: (f"sample-{self.sample.call_count}",))
        self.samplers = types.ModuleType("comfy_extras.nodes_custom_sampler")
        self.samplers.BasicGuider = types.SimpleNamespace(execute=self.basic)
        self.samplers.CFGGuider = types.SimpleNamespace(execute=self.cfg)
        self.samplers.BasicScheduler = types.SimpleNamespace(execute=self.scheduler)
        self.samplers.KSamplerSelect = types.SimpleNamespace(execute=lambda name: (name,))
        self.samplers.RandomNoise = types.SimpleNamespace(execute=lambda seed: (seed,))
        self.samplers.SamplerCustomAdvanced = types.SimpleNamespace(execute=self.sample)
        resources = types.ModuleType(self.package.__name__ + ".resource_control")
        resources.release_resources = Mock()
        self.image_sample = Mock(side_effect=lambda **kwargs: (f"image-{self.image_sample.call_count}",))
        image_sampling = types.ModuleType(self.package.__name__ + ".image_sampling")
        image_sampling.sample_image2 = self.image_sample
        self.modules = patch.dict(sys.modules, {self.package.__name__: self.package,
            "folder_paths": self.folder, "nodes": self.nodes, "comfy": self.comfy,
            "comfy.model_management": self.mm, "comfy_extras.nodes_custom_sampler": self.samplers,
            resources.__name__: resources, image_sampling.__name__: image_sampling})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.video = importlib.import_module(self.package.__name__ + ".video_lora")
        self.image = importlib.import_module(self.package.__name__ + ".image_bridge")
        for name in list(sys.modules):
            if name.startswith(self.package.__name__ + "."):
                self.addCleanup(sys.modules.pop, name, None)

    def test_profile_normalization_keeps_legacy_slots_without_new_pass(self):
        old = {"enabled": True, "name": "h3.safetensors", "strength": .6}
        self.assertEqual(self.video.normalize_video_loras([old], verify_files=False), [old])
        new = {**old, "steps": 12, "cfg": 3.5}
        self.assertEqual(self.video.normalize_video_loras([new], verify_files=False), [new])
        for profile in ({"steps": 0}, {"steps": 2.5}, {"cfg": float('nan')}, {"cfg": 31}):
            with self.assertRaises(ValueError):
                self.video.normalize_video_loras([{**old, **profile}], verify_files=False)

    def test_video_slots_reach_scheduler_cfg_and_chain_in_order(self):
        slots = [{"enabled": True, "name": "one", "strength": .7, "steps": 6, "cfg": 1},
                 {"enabled": True, "name": "two", "strength": .5, "steps": 12, "cfg": 4}]
        result, receipts = self.video.refine_video_lora_slots({"model": "combined", "video_loras": slots},
            "positive", "initial", 42, "res_multistep", "simple")
        self.assertEqual(result, "sample-2")
        self.assertEqual([call.args[2:] for call in self.scheduler.call_args_list], [(6, .25), (12, .25)])
        self.basic.assert_called_once_with("combined", "positive")
        self.cfg.assert_called_once_with("combined", "positive", "negative", 4)
        self.assertEqual(self.sample.call_args_list[0].args[-1], "initial")
        self.assertEqual(self.sample.call_args_list[1].args[-1], "sample-1")
        self.assertEqual([(row["steps"], row["cfg"]) for row in receipts], [(6,1), (12,4)])

    def test_disabled_legacy_and_zero_strength_video_slots_do_no_sampling(self):
        slots = [{"enabled": False, "name": "one", "strength": 1, "steps": 8, "cfg": 2},
                 {"enabled": True, "name": "two", "strength": 0, "steps": 8, "cfg": 2},
                 {"enabled": True, "name": "old", "strength": .7}]
        original = object()
        result, receipts = self.video.refine_video_lora_slots({"video_loras": slots}, None, original, 1, 'euler','simple')
        self.assertIs(result, original)
        self.assertEqual(receipts, [])
        self.sample.assert_not_called()

    def test_pika_explicit_slot_profile_fails_without_resampling(self):
        bundle = {"pika_enabled": True, "video_loras": [
            {"enabled": True, "name": "one", "strength": 1, "steps": 8, "cfg": 1}]}
        with self.assertRaisesRegex(ValueError, 'Pika'):
            self.video.refine_video_lora_slots(bundle, None, None, 1, 'lcm','simple')
        self.sample.assert_not_called()

    def test_image_slot_steps_cfg_and_latent_reach_sampler_in_order(self):
        slots = [{"enabled": True, "name": "first", "strength": .5, "steps": 18, "cfg": 2.5},
                 {"enabled": False, "name": "off", "strength": 1, "steps": 50, "cfg": 8},
                 {"enabled": True, "name": "second", "strength": .3, "steps": 8, "cfg": 1}]
        result, receipts = self.image.refine_image_lora_slots('combined','positive','negative','initial',slots,42,
            {"sampler":"euler","scheduler":"simple","model":"qwen-image-2.1-turbo.safetensors"})
        self.assertEqual(result, 'image-2')
        first, second = [call.kwargs for call in self.image_sample.call_args_list]
        self.assertEqual((first['steps'],first['cfg'],first['latent'],first['denoise']), (18,2.5,'initial',.25))
        self.assertEqual((second['steps'],second['cfg'],second['latent']), (8,1,'image-1'))
        self.assertEqual(len(receipts), 2)

    def test_disabled_image_slots_keep_base_result_without_new_pass(self):
        result, receipts = self.image.refine_image_lora_slots(None,None,None,'initial',[
            {"enabled":False,"name":"off","strength":1,"steps":25,"cfg":2},
            {"enabled":True,"name":"legacy","strength":.7}],42,{})
        self.assertEqual((result,receipts),('initial',[]))
        self.image_sample.assert_not_called()


if __name__ == '__main__':
    unittest.main()
