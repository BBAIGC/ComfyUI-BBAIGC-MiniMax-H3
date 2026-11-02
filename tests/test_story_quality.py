"""Speech, gesture, cloud-planning and architecture isolation regressions; CPU only."""
import importlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1]


class StoryQualityTests(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType("story_quality_fixture")
        package.__path__ = [str(PLUGIN)]
        prompts = types.ModuleType(package.__name__ + ".prompt_nodes")
        prompts.LocalOllama = Mock(side_effect=AssertionError("cloud must not start Ollama"))
        prompts._LOCK = threading.Lock()
        prompts._reference_jpeg = lambda *args: None
        prompts.local_models = lambda: []
        cloud = types.ModuleType(package.__name__ + ".cloud_bridge")
        cloud.chat_json = Mock()
        mm = types.ModuleType("comfy.model_management")
        mm.throw_exception_if_processing_interrupted = lambda: None
        mm.unload_all_models = Mock()
        mm.soft_empty_cache = Mock()
        comfy = types.ModuleType("comfy")
        comfy.model_management = mm
        self.modules = patch.dict(sys.modules, {package.__name__: package,
            prompts.__name__: prompts, cloud.__name__: cloud,
            "comfy": comfy, "comfy.model_management": mm})
        self.modules.start()
        self.cloud = cloud
        self.planner = importlib.import_module(package.__name__ + ".planner")
        self.pacing = importlib.import_module(package.__name__ + ".dialogue_pacing")
        self.motion = importlib.import_module(package.__name__ + ".motion_continuity")
        self.loras = importlib.import_module(package.__name__ + ".video_lora")

    def tearDown(self):
        self.modules.stop()
        for name in list(sys.modules):
            if name.startswith("story_quality_fixture"):
                sys.modules.pop(name, None)

    def test_rushed_line_moves_earlier_and_finishes_before_cut(self):
        entries = [{"id": "D1", "start": 7.6, "end": 8.0}]
        registry = {"D1": {"text": "下午好，请喝水。"}}
        changes = self.pacing.fit_dialogue_windows(entries, registry, 8)
        self.assertTrue(changes)
        self.assertLessEqual(entries[0]["end"], 7.6)
        self.assertGreaterEqual(entries[0]["end"]-entries[0]["start"], 1.3)
        self.assertEqual(entries[0]["id"], "D1")

    def test_long_speech_is_rejected_instead_of_truncated(self):
        with self.assertRaisesRegex(ValueError, "原台词未截断"):
            self.pacing.fit_dialogue_windows([{"id": "D1", "start": 0, "end": 2}],
                {"D1": {"text": "请保持原句完整，不能在对白还没有说完时直接切换到下一镜。"}}, 2)

    def test_existing_feasible_windows_and_order_are_preserved(self):
        entries = [{"id": "D1", "start": 1, "end": 3}, {"id": "D2", "start": 4, "end": 6}]
        before = json.dumps(entries)
        self.assertEqual(self.pacing.fit_dialogue_windows(entries,
            {"D1": {"text": "下午好。"}, "D2": {"text": "请喝水。"}}, 8), [])
        self.assertEqual(json.dumps(entries), before)

    def test_short_gesture_has_one_endpoint_but_requested_repetition_is_preserved(self):
        self.assertIn("一次", self.motion.single_action_note("轻轻点头", "她轻轻点头。"))
        self.assertEqual(self.motion.single_action_note("连续点头", "她连续点头。"), "")
        self.assertEqual(self.motion.single_action_note("点头三次", "她点头三次。"), "")

    def test_ascii_single_quoted_retelling_cannot_add_translated_speech(self):
        cleaned = self.planner._sanitize_description_speech(
            "S2 says 'Thank you, just a bit thirsty.' Then S2 nods once.", ["谢谢，正好有点渴。"], preserve_performance=True)
        self.assertNotIn('Thank you', cleaned)
        self.assertIn('nods once', cleaned)
        lines = self.planner.extract_dialogue("The visitor says 'Don't worry.'")
        self.assertEqual(lines[0]['text'], "Don't worry.")

    def test_source_roles_lock_same_person_across_two_utterances(self):
        lines = self.planner.extract_dialogue("店员看向客人，微笑说：‘下午好。’店员把杯子放下，说：‘请喝水。’客人轻轻点头说：‘谢谢。’")
        self.assertEqual([line['speaker'] for line in lines], ['S1', 'S1', 'S2'])
        self.assertTrue(all(line['speaker_locked'] for line in lines))

    def test_explicit_description_speaker_corrects_unlocked_id(self):
        line = self.planner.extract_dialogue("客人说：‘谢谢。’")[0]
        line['speaker_locked'] = False
        raw = {'continuity': '', 'constraints': '', 'scenes': [{
            'visual': '两人坐在桌边。', 'action': '客人轻轻点头。', 'soundscape': '安静室内。', 'music': 'N/A',
            'dialogues': [{'id': 'D1', 'speaker_id': 'S1', 'speaker_description': 'S2成年客人',
                           'delivery': '自然开口', 'start': 1, 'end': 3}]}]}
        result = self.planner.compile_storyboard(raw, script='客人轻点头说谢谢', count=1,
            seconds=[6], dialogues=[line], voice_mode='自动识别原稿', input_mode='T2VA')
        self.assertEqual(result['scenes'][0]['dialogues'][0]['speaker_id'], 'S2')
        self.assertIn('(S2)', result['compiled'][0]['prompt'])

    def test_cloud_text_storyboard_has_copyable_stills_and_exact_dialogue(self):
        script = "成年女主人坐在客厅，微笑说：‘下午好，请喝水。’随后客人轻轻点头，说：‘谢谢。’"
        lines = self.planner.extract_dialogue(script)
        def scene(action, dialogue, start, end):
            return {"visual": "客厅桌边的成年人物，中景平视。", "action": action,
                "soundscape": "安静室内环境音。", "music": "N/A", "image_prompt": "客厅桌边的人物静态画面。",
                "composition": "中景平视。", "start_state": "人物坐在桌边，杯子在桌面。",
                "end_state": "人物坐在桌边，杯子仍在桌面。", "start_facts": {"杯子": "桌面"},
                "end_facts": {"杯子": "桌面"}, "completed_actions": [], "reference_ids": [],
                "dialogues": [{"id": dialogue, "speaker_id": "S1" if dialogue == "D1" else "S2",
                    "speaker_description": "成年女主人" if dialogue == "D1" else "成年客人",
                    "delivery": "自然开口说话", "start": start, "end": end}]}
        self.cloud.chat_json.return_value = {"continuity": "成年女主人与客人的容貌保持一致。", "constraints": "不增添人物。",
            "scenes": [scene("微笑邀请客人喝水。", "D1", 6.8, 8), scene("客人轻轻点头。", "D2", 1, 3)]}
        plan = self.planner.plan_sequence(script, [8, 6], "cloud-model", "自动识别原稿",
            model_settings={"provider": "bbapi", "profile": "test-local-profile", "base_url": "https://example.invalid", "model": "cloud-model"},
            input_mode="T2VA")
        self.assertTrue(plan["llm_released"])
        self.assertEqual(self.cloud.chat_json.call_count, 1)
        self.assertEqual([d["text"] for d in plan["dialogue_whitelist"]], [d["text"] for d in lines])
        for scene in plan["scenes"]:
            self.assertTrue(scene["image_prompt"])
            self.assertNotIn("参考", scene["image_prompt"])
            self.assertEqual(scene["reference_ids"], [])
        self.assertLessEqual(plan["scenes"][0]["dialogues"][0]["end"], 7.6)
        self.assertIn("短动作只自然完成一次", plan["prompts"][1])
        self.assertIn("下午好，请喝水。", plan["prompts"][0])
        self.assertNotIn("下午好", plan["prompts"][1])

    def test_video_lora_rejects_image_architecture_and_keeps_slots_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for name, architecture in (("h3.safetensors", "minimax_h3"), ("qwen.safetensors", "qwen_image")):
                header = json.dumps({"__metadata__": {"base_model": architecture},
                    "diffusion_model.blocks.0.attn.qkv_proj.lora_A.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
                path = Path(directory)/name
                path.write_bytes(struct.pack("<Q", len(header))+header+b"\0"*4)
                paths[name] = str(path)
            folder = types.SimpleNamespace(get_filename_list=lambda _: list(paths), get_full_path=lambda _, name: paths[name])
            with patch.dict(sys.modules, {"folder_paths": folder}):
                selected = self.loras.normalize_video_loras([{"name": "h3.safetensors", "enabled": True, "strength": .65},
                    {"name": "qwen.safetensors", "enabled": False, "strength": 1}])
                self.assertEqual(selected, [{"name": "h3.safetensors", "enabled": True, "strength": .65}])
                with self.assertRaisesRegex(ValueError, "图片"):
                    self.loras.normalize_video_loras([{"name": "qwen.safetensors", "enabled": True, "strength": 1}])

    def test_video_memory_budget_restores_host_policy_on_failure(self):
        mm = sys.modules['comfy.model_management']
        mm.EXTRA_RESERVED_VRAM = 600 * 1024**2
        memory = importlib.import_module('story_quality_fixture.video_memory')
        original = mm.EXTRA_RESERVED_VRAM
        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            with memory.video_memory_budget(3, True) as receipt:
                self.assertEqual(mm.EXTRA_RESERVED_VRAM, 3 * 1024**3)
                self.assertEqual(receipt['reserved_gb'], 3)
                raise RuntimeError('fixture failure')
        self.assertEqual(mm.EXTRA_RESERVED_VRAM, original)

    def test_scene_specific_script_cannot_leak_through_global_constraints(self):
        text = self.planner._stable_continuity('固定机位。第一镜：店员放杯。第二镜：客人点头。不要增加人物。', constraints=True)
        self.assertNotIn('第一镜', text)
        self.assertNotIn('第二镜', text)
        self.assertIn('固定机位', text)
        self.assertIn('不要增加人物', text)


if __name__ == "__main__":
    unittest.main()
