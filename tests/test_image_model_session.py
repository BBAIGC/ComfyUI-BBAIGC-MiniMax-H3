"""Bounded image-stage reuse changes loader lifetime, never sampling inputs."""
import copy
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import torch

PLUGIN = Path(__file__).resolve().parents[1]


class ImageModelSessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        package = types.ModuleType('image_session_fixture')
        package.__path__ = [str(PLUGIN)]
        folder = types.ModuleType('folder_paths')
        folder.get_output_directory = lambda: self.directory.name
        nodes = types.ModuleType('nodes')
        self.reset = Mock()
        self.raw = types.SimpleNamespace(model=types.SimpleNamespace(
            diffusion_model=types.SimpleNamespace(reset_prefix_cache=self.reset)))
        self.load_model = Mock(return_value=(self.raw,))
        self.load_clip = Mock(return_value=('clip',))
        self.load_vae = Mock(return_value=('vae',))
        nodes.UNETLoader = lambda: types.SimpleNamespace(load_unet=self.load_model)
        nodes.CLIPLoader = lambda: types.SimpleNamespace(load_clip=self.load_clip)
        nodes.VAELoader = lambda: types.SimpleNamespace(load_vae=self.load_vae)
        nodes.VAEDecode = lambda: types.SimpleNamespace(decode=lambda *_: (torch.zeros((1, 64, 64, 3)),))
        nodes.NODE_CLASS_MAPPINGS = {}
        self.mm = types.ModuleType('comfy.model_management')
        self.mm.soft_empty_cache = Mock()
        self.mm.unload_all_models = Mock()
        self.mm.intermediate_device = lambda: 'cpu'
        self.mm.throw_exception_if_processing_interrupted = lambda: None
        comfy = types.ModuleType('comfy')
        comfy.model_management = self.mm
        resources = types.ModuleType(package.__name__ + '.resource_control')
        self.release = Mock()
        resources.release_resources = self.release
        sampling = types.ModuleType(package.__name__ + '.image_sampling')
        self.sample = Mock(return_value=({'samples': torch.zeros((1, 64, 4, 4))},))
        sampling.sample_image2 = self.sample
        self.modules = patch.dict(sys.modules, {package.__name__: package, 'folder_paths': folder,
            'nodes': nodes, 'comfy': comfy, 'comfy.model_management': self.mm,
            resources.__name__: resources, sampling.__name__: sampling})
        self.modules.start()
        self.session_type = importlib.import_module(package.__name__ + '.image_model_session').ImageModelSession
        self.bridge = importlib.import_module(package.__name__ + '.image_bridge')
        self.settings = {**self.bridge.DEFAULT_SETTINGS, 'model': 'qwen_image_2.1_test.safetensors'}

    def tearDown(self):
        self.modules.stop()
        for name in list(sys.modules):
            if name.startswith('image_session_fixture'):
                sys.modules.pop(name, None)
        self.directory.cleanup()

    def test_one_raw_weight_set_is_reused_and_closed_once(self):
        loader = Mock(return_value={'model': self.raw, 'clip': object(), 'vae': object()})
        session = self.session_type()
        first = session.acquire(self.settings, loader)
        second = session.acquire({**self.settings, 'steps': 8, 'cache_dtype': 'int8'}, loader)
        self.assertIs(first['model'], second['model'])
        first['model'] = 'per-image-clone'
        self.assertIs(session.acquire(self.settings, loader)['model'], self.raw)
        self.assertEqual(loader.call_count, 1)
        session.finish_image()
        self.reset.assert_called_with(False)
        self.release.assert_not_called()
        session.close()
        session.close()
        self.release.assert_called_once_with(full=False)
        self.assertIsNone(session._weights)
        with self.assertRaisesRegex(RuntimeError, '已结束'):
            session.acquire(self.settings, loader)

    def test_settings_changes_cannot_accumulate_another_weight_set(self):
        loader = Mock(return_value={'model': self.raw, 'clip': 'clip', 'vae': 'vae'})
        session = self.session_type()
        session.acquire(self.settings, loader)
        for key, value in [('model', 'other.safetensors'), ('clip', 'other_clip'), ('vae', 'other_vae'),
                           ('loras', [{'name': 'style', 'strength': .5}]), ('clip_device', 'cpu')]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '配置已变化'):
                session.acquire({**self.settings, key: value}, loader)
        self.assertEqual(loader.call_count, 1)
        session.close()

    def test_context_manager_releases_weights_after_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'sampling failed'):
            with self.session_type() as session:
                session.acquire(self.settings, lambda: {'model': self.raw, 'clip': 'clip', 'vae': 'vae'})
                raise RuntimeError('sampling failed')
        self.assertIsNone(session._weights)
        self.release.assert_called_once_with(full=False)

    def test_two_images_reuse_loaders_but_rebuild_prompt_and_cache_clone(self):
        clones = []
        prompts = []
        def cache(model, device, dtype):
            self.assertIs(model, self.raw)
            clone = object()
            clones.append(clone)
            return (clone,)
        def encode(**kwargs):
            prompts.append(kwargs['prompt'])
            return (f"positive:{kwargs['prompt']}", 'negative', {})
        classes = {'QwenImage21Cache': types.SimpleNamespace(execute=cache),
                   'TextEncodeQwenImage21': types.SimpleNamespace(execute=encode)}
        original = copy.deepcopy(self.settings)
        with patch.object(self.bridge, '_check_weights'), patch.object(self.bridge, 'normalize_settings', return_value=self.settings), \
             patch.object(self.bridge, '_class', side_effect=classes.__getitem__):
            with self.session_type() as session:
                for index, prompt in enumerate(('first image', 'second image')):
                    self.bridge.generate_reference(prompt, None, 64, 64, seed=index,
                        settings=self.settings, text_only=True, model_session=session)
                self.release.assert_not_called()
                self.assertIs(session._weights['model'], self.raw)
        self.assertEqual((self.load_model.call_count, self.load_clip.call_count, self.load_vae.call_count), (1, 1, 1))
        self.assertEqual(prompts, ['first image', 'second image'])
        self.assertEqual([call.kwargs['model'] for call in self.sample.call_args_list], clones)
        self.assertEqual([call.kwargs['positive'] for call in self.sample.call_args_list],
                         ['positive:first image', 'positive:second image'])
        self.assertEqual(self.settings, original)
        self.mm.unload_all_models.assert_not_called()
        self.release.assert_called_once_with(full=False)

    def test_standalone_images_still_load_and_release_each_call(self):
        classes = {'QwenImage21Cache': types.SimpleNamespace(execute=lambda **kwargs: (object(),)),
                   'TextEncodeQwenImage21': types.SimpleNamespace(execute=lambda **kwargs: ('positive', 'negative', {}))}
        with patch.object(self.bridge, '_check_weights'), patch.object(self.bridge, 'normalize_settings', return_value=self.settings), \
             patch.object(self.bridge, '_class', side_effect=classes.__getitem__):
            for _ in range(2):
                self.bridge.generate_reference('image', None, 64, 64, settings=self.settings, text_only=True)
        self.assertEqual(self.load_model.call_count, 2)
        self.assertEqual(self.release.call_count, 2)


if __name__ == '__main__':
    unittest.main()
