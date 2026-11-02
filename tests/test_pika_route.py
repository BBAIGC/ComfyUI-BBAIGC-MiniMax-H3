"""CPU adapter check against the published Pika H3 Flex sampling contract."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(importlib.util.find_spec('torch'), 'requires the ComfyUI Python environment')
class PikaRouteTests(unittest.TestCase):
    def setUp(self):
        import torch  # Import before patch.dict snapshots sys.modules.
        self.nodes = types.ModuleType('nodes')
        self.nodes.UNETLoader = lambda: types.SimpleNamespace(load_unet=Mock(return_value=('base',)))
        self.loader = Mock(return_value=('pika-patched',))
        self.nodes.LoraLoaderModelOnly = lambda: types.SimpleNamespace(load_lora_model_only=self.loader)
        self.nodes.CLIPLoader = lambda: types.SimpleNamespace(load_clip=Mock(return_value=('clip',)))
        self.nodes.VAELoader = lambda: types.SimpleNamespace(load_vae=Mock(return_value=('vae',)))
        self.folder = types.SimpleNamespace(get_filename_list=lambda _: ['pika_h3_flex_fl2va_v1_bf16.safetensors',
            'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors'])
        native = types.ModuleType('comfy_extras.nodes_minimax_h3')
        self.shift = Mock(side_effect=AssertionError('official Pika template has no sigma shift'))
        native.MiniMaxH3SigmaShift = types.SimpleNamespace(execute=self.shift)
        self.modules = patch.dict(sys.modules, {'nodes': self.nodes, 'folder_paths': self.folder,
            'comfy_extras.nodes_minimax_h3': native})
        self.modules.start()
        path = Path(__file__).resolve().parents[1]/'shot_nodes.py'
        spec = importlib.util.spec_from_file_location('pika_shot_fixture', path)
        self.shots = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.shots)
        self.plan = json.dumps({'status': 'ready', 'llm_released': True})

    def tearDown(self):
        self.modules.stop()

    def test_dedicated_adapter_six_official_sigmas_and_no_turbo_shift(self):
        bundle = self.shots.H3VideoModelBundle().load(self.plan, turbo_enabled=False,
            pika_enabled=True, low_vram=False, sparse_attention=True, input_mode='T2VA')[0]
        self.loader.assert_called_once_with('base', 'pika_h3_flex_fl2va_v1_bf16.safetensors', 1.0)
        self.assertEqual(bundle['turbo_steps'], 6)
        self.assertEqual(bundle['manual_sigmas'], (1.0, .972973, .923077, .8, .571429, .333333, 0.0))
        self.assertEqual(bundle['cfg'], 1)
        self.assertFalse(bundle['sparse_attention'])
        self.assertIsNone(bundle['sigma_shift_video'])
        self.shift.assert_not_called()

    def test_pika_and_turbo_cannot_be_stacked(self):
        with self.assertRaisesRegex(ValueError, '不能与Turbo'):
            self.shots.H3VideoModelBundle().load(self.plan, pika_enabled=True, turbo_enabled=True)
        self.loader.assert_not_called()

    def test_missing_adapter_never_falls_back(self):
        self.folder.get_filename_list = lambda _: []
        with self.assertRaisesRegex(ValueError, '缺少Pika'):
            self.shots.H3VideoModelBundle().load(self.plan, pika_enabled=True, turbo_enabled=False)
        self.loader.assert_not_called()

    def test_custom_six_and_twelve_steps_keep_selected_eight_step_weights(self):
        self.shift.side_effect = None
        self.shift.return_value = ('shifted-model',)
        for steps in (6, 12):
            with self.subTest(steps=steps):
                bundle = self.shots.H3VideoModelBundle().load(self.plan, turbo_enabled=True,
                    accelerated_steps=steps, turbo_lora_steps=8, low_vram=False)[0]
                self.assertEqual(bundle['turbo_steps'], steps)
                self.assertEqual(bundle['turbo_lora_steps'], 8)
                self.assertEqual(bundle['turbo_lora'], 'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors')
                self.assertEqual(self.shots.H3SamplingSteps().select(True, steps, 20), (steps,))

    def test_custom_steps_reach_native_scheduler_unchanged(self):
        native = types.ModuleType('comfy_extras.nodes_custom_sampler')
        execute = Mock(return_value=('sigmas',))
        native.BasicScheduler = types.SimpleNamespace(execute=execute)
        with patch.dict(sys.modules, {native.__name__: native}):
            for steps in (6, 12):
                self.assertEqual(self.shots._sampling_sigmas({'model': 'video-model'}, 'simple', steps), 'sigmas')
                execute.assert_called_with('video-model', 'simple', steps, 1.0)


if __name__ == '__main__':
    unittest.main()
