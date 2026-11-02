"""Native progress forwarding, phase resets and bounded replay; no inference."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location('progress_fixture_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VideoProgressTests(unittest.TestCase):
    def test_real_native_steps_forward_once_then_decoding_clears_percent(self):
        module = load('video_progress')
        original = Mock()
        utils = types.SimpleNamespace(PROGRESS_BAR_HOOK=original)
        comfy = types.ModuleType('comfy')
        comfy.utils = utils
        events = []
        bundle = {'_progress_callback': events.append}
        with patch.dict(sys.modules, {'comfy': comfy, 'comfy.utils': utils}):
            with module.sampling_video_progress(bundle, 'sampling', 8):
                hook = utils.PROGRESS_BAR_HOOK
                hook(4, 8, 'preview')
                hook(4, 8, 'preview')
                hook(8, 8)
            module.notify_video_phase(bundle, 'video_decoding')
        self.assertIs(utils.PROGRESS_BAR_HOOK, original)
        self.assertEqual(original.call_count, 3)
        self.assertEqual([row.get('percent') for row in events], [0, 50, 100, None])
        self.assertEqual(events[-1], {'phase': 'video_decoding'})

    def test_hook_restored_on_failure_and_later_extension_is_preserved(self):
        module = load('video_progress')
        original = Mock()
        utils = types.SimpleNamespace(PROGRESS_BAR_HOOK=original)
        comfy = types.ModuleType('comfy')
        comfy.utils = utils
        with patch.dict(sys.modules, {'comfy': comfy, 'comfy.utils': utils}):
            with self.assertRaises(RuntimeError):
                with module.sampling_video_progress({'_progress_callback': Mock()}, 'face_sampling', 8):
                    raise RuntimeError('cancelled')
            self.assertIs(utils.PROGRESS_BAR_HOOK, original)
            replacement = Mock()
            with module.sampling_video_progress({'_progress_callback': Mock()}, 'latent_refining', 4):
                utils.PROGRESS_BAR_HOOK = replacement
            self.assertIs(utils.PROGRESS_BAR_HOOK, replacement)

    def test_step_replay_keeps_whole_run_and_rejects_old_execution(self):
        state = load('progress_state')
        state.remember({'run_id': 'run', 'execution_id': 'new', 'progress_seq': 1,
                        'scenes': [{'index': 1, 'status': '生成中'}], 'plan_export': {'kept': True}})
        state.remember({'run_id': 'run', 'execution_id': 'new', 'progress_seq': 2,
                        'scene_index': 1, 'video_progress': {'phase': 'face_sampling', 'value': 4, 'total': 8}},
                       video_partial=True)
        snapshot = state.get_state(run='run')
        self.assertEqual(snapshot['scenes'][0]['video_progress']['value'], 4)
        self.assertEqual(snapshot['plan_export'], {'kept': True})
        state.remember({'run_id': 'run', 'execution_id': 'old', 'progress_seq': 99,
                        'scene_index': 1, 'video_progress': {'phase': 'sampling'}}, video_partial=True)
        self.assertEqual(state.get_state(run='run')['progress_seq'], 2)


if __name__ == '__main__':
    unittest.main()
