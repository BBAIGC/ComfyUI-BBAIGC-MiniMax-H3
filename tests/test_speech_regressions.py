"""A real leaked-planner prompt must not become untimed speech guidance."""
import importlib
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1]


class SpeechRegressionTests(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType('speech_regression_fixture')
        package.__path__ = [str(PLUGIN)]
        prompts = types.ModuleType(package.__name__ + '.prompt_nodes')
        prompts.LocalOllama = Mock(side_effect=AssertionError('CPU test must not start a model'))
        prompts._LOCK = threading.Lock()
        prompts._reference_jpeg = lambda *args: None
        prompts.local_models = lambda: []
        self.modules = patch.dict(sys.modules, {package.__name__: package, prompts.__name__: prompts})
        self.modules.start()
        self.planner = importlib.import_module(package.__name__ + '.planner')
        self.motion = importlib.import_module(package.__name__ + '.motion_continuity')

    def tearDown(self):
        self.modules.stop()
        for name in list(sys.modules):
            if name.startswith('speech_regression_fixture'):
                sys.modules.pop(name, None)

    def test_hat_and_cup_action_cannot_be_rewritten_as_standing_cup_hold(self):
        action = '人物坐着，双手摘下帽子放在桌上，随后右手端起杯子向前递出一点。'
        self.assertEqual(self.motion.finite_gesture_timing(action, '先摘帽，再递杯。', 10.5), action)
        cup = '坐着将杯子放在桌面，右手放回膝上。'
        self.assertEqual(self.motion.finite_gesture_timing(cup, cup, 8), cup)

    def test_only_a_source_requested_single_gesture_gets_a_clock(self):
        self.assertEqual(self.motion.finite_gesture_timing('轻轻点头。', '人物安静坐着。', 8), '轻轻点头。')
        self.assertIn('第0.5至2秒', self.motion.finite_gesture_timing('轻轻点头。', '人物轻轻点头。', 8))
        action = '轻轻点头，然后拿起杯子。'
        self.assertEqual(self.motion.finite_gesture_timing(action, action, 8), action)
        self.assertEqual(self.motion.finite_gesture_timing('轻轻点头。', '第5秒轻轻点头。', 8), '轻轻点头。')

    def test_machine_metadata_is_removed_but_natural_limits_survive(self):
        text = ('场景数量：2，时长：[10.5, 5.5]，总计：16s，参考 ID：[]，参考角色：{}，'
                '语音模式：自动， D2]，场景时间线：[0-10.5, 10.5-16.0]，锚点：源自素材的身份。'
                '人物身份、空间和材质保持；固定机位；不增加人物。')
        cleaned = self.planner._without_planner_metadata(text)
        for token in ('场景数量', '10.5', '总计', '参考', '语音模式', 'D2]', '场景时间线', '锚点', '源自素材'):
            self.assertNotIn(token, cleaned)
        for phrase in ('人物身份', '固定机位', '不增加人物'):
            self.assertIn(phrase, cleaned)

    def test_compiler_keeps_one_timed_utterance_and_no_early_generic_speech(self):
        script = '人物坐在桌前，摘下帽子放到桌上，说“下午好。”随后把杯子递近。'
        lines = self.planner.extract_dialogue(script)
        raw = {'continuity': '场景数量：2，锚点：源自素材的身份。人物肤色自然。',
            'constraints': '时长：[10.5, 5.5]，参考 ID：[]， D2]，语音模式：自动。固定机位，不增加人物。',
            'scenes': [{'visual': '客厅中景，人物坐在桌前。',
                'action': '坐着摘下帽子放在桌面，右手端起杯子递出，同时自然开口说话。',
                'start_state': '人物坐着，头戴帽子，手持杯子。',
                'end_state': '帽子在桌上，人物坐着持杯。', 'composition': '中景平视。',
                'soundscape': '衣物摩擦声。', 'music': 'N/A',
                'dialogues': [{'id': 'D1', 'speaker_id': 'S1', 'speaker_description': '成年朋友',
                    'delivery': '温和亲切，嘴唇自然开合', 'start': 2, 'end': 3.5}]}]}
        result = self.planner.compile_storyboard(raw, script=script, count=1, seconds=[10.5],
            dialogues=lines, voice_mode='自动识别原稿', input_mode='T2VA')
        body = result['compiled'][0]['fields']['integrated_multimodal_description']
        self.assertEqual(body.count('<d>[Chinese] 下午好。</d>'), 1)
        self.assertIn('第2至3.5秒', body)
        self.assertNotIn('同时自然开口说话', body)
        self.assertNotIn('第0.5至2秒', body)
        self.assertNotIn('双臂放松停在身侧', body)
        for token in ('场景数量', '锚点', '源自素材', '语音模式', 'D2]'):
            self.assertNotIn(token, body)
        self.assertIn('固定机位', body)
        self.assertNotIn('温和亲切，嘴唇自然开合', body)
        self.assertEqual(result['scenes'][0]['dialogues'][0]['delivery'], '温和亲切，嘴唇自然开合')

    def test_planner_field_rules_survive_custom_presets(self):
        system, _, schema = self.planner.planning_request('人物说“下午好。”', 1, [8],
            self.planner.extract_dialogue('人物说“下午好。”'), '自动识别原稿')
        self.assertIn('constraints只写原剧本', system)
        self.assertIn('不能增加text或自拟台词', system)
        self.assertFalse(schema['properties']['scenes']['items']['properties']['dialogues']['items']['additionalProperties'])

    def test_final_renderer_cannot_add_an_utterance(self):
        script = '人物坐在桌前，说“下午好。”'
        lines = self.planner.extract_dialogue(script)
        scene = {'visual': '客厅中景。', 'action': '人物坐着。', 'soundscape': '安静环境声。',
            'music': 'N/A', 'start_state': '人物坐着。', 'end_state': '人物仍坐着。',
            'composition': '平视中景。', 'dialogues': [{'id': 'D1', 'speaker_id': 'S1',
                'speaker_description': '人物', 'delivery': '自然', 'start': 2, 'end': 4}]}
        render = self.planner.render_h3_prompt
        with patch.object(self.planner, 'render_h3_prompt', side_effect=lambda *a, **k: render(*a, **k) + '\n<d>[Chinese] 额外台词</d>'):
            with self.assertRaisesRegex(ValueError, '已确认对白不同'):
                self.planner.compile_storyboard({'continuity': '', 'constraints': '', 'scenes': [scene]},
                    script=script, count=1, seconds=[8], dialogues=lines, voice_mode='自动识别原稿', input_mode='T2VA')


if __name__ == '__main__':
    unittest.main()
