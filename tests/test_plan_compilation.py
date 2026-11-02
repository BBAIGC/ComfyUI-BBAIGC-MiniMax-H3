"""Saved plans receive compiler fixes without rerunning the LLM or losing edits."""
import copy
import importlib
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

PLUGIN = Path(__file__).resolve().parents[1]


class PlanCompilationTests(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType("plan_compilation_fixture")
        package.__path__ = [str(PLUGIN)]
        prompts = types.ModuleType(package.__name__ + ".prompt_nodes")
        prompts.LocalOllama = Mock(side_effect=AssertionError("saved-plan refresh must not start an LLM"))
        prompts._LOCK = threading.Lock()
        prompts._reference_jpeg = lambda *args: None
        prompts.local_models = lambda: []
        self.modules = patch.dict(sys.modules, {package.__name__: package, prompts.__name__: prompts})
        self.modules.start()
        self.planner = importlib.import_module(package.__name__ + ".planner")
        self.compiler = importlib.import_module(package.__name__ + ".plan_compile")
        self.edits = importlib.import_module(package.__name__ + ".plan_edits")
        self.cache = importlib.import_module(package.__name__ + ".stage_cache")

    def tearDown(self):
        self.modules.stop()
        for name in list(sys.modules):
            if name.startswith("plan_compilation_fixture"):
                sys.modules.pop(name, None)

    def plan(self):
        script = "朋友坐在桌前，说“请喝水。”"
        lines = self.planner.extract_dialogue(script)
        scene = {"visual": "朋友坐在明亮客厅的桌前。", "action": "右手把杯子递近。",
                 "start_state": "朋友坐着，杯子在桌上。", "end_state": "朋友坐着持杯。",
                 "image_prompt": "保留用户确认的分镜图提示词。", "reference_ids": ["图1"],
                 "composition": "中景。", "soundscape": "杯子轻响。", "music": "N/A",
                 "dialogues": [{"id": "D1", "speaker_id": "S1", "speaker_description": "成年朋友",
                                "delivery": "自然温和", "start": 2, "end": 3.5}]}
        result = self.planner.compile_storyboard({"continuity": "", "constraints": "", "scenes": [scene]}, script=script, count=1,
                    seconds=[5.5], dialogues=lines, voice_mode="自动识别原稿")
        scenes = result["scenes"]
        scenes[0]["video_prompt"] = result["compiled"][0]["prompt"]
        return {"raw_script": script, "scene_seconds": [5.5], "speech_mode": "自动识别原稿",
                "dialogue_whitelist": lines, "scenes": scenes,
                "compiled_prompts": [result["compiled"][0]["json"]],
                "prompts": [result["compiled"][0]["prompt"]],
                "final_image_prompt": "用户确认的结束帧。"}

    def test_old_compiled_text_is_refreshed_without_changing_story_or_images(self):
        plan = self.plan()
        plan["compiled_prompts"][0] = plan["compiled_prompts"][0].replace("杯子轻响", "旧版本污染内容")
        original = copy.deepcopy(plan)
        refreshed, receipt = self.compiler.refresh_plan_compilation(plan)
        self.assertTrue(receipt["prompts_changed"])
        self.assertNotIn("旧版本污染内容", refreshed["compiled_prompts"][0])
        for key in ("start_state", "end_state", "image_prompt", "reference_ids", "dialogues"):
            self.assertEqual(refreshed["scenes"][0][key], original["scenes"][0][key])
        self.assertEqual(refreshed["final_image_prompt"], original["final_image_prompt"])
        self.assertEqual(plan, original)
        self.assertEqual(refreshed["prompt_compiler"], self.compiler.prompt_compiler_identity())

    def test_current_compilation_is_reused_without_mutation(self):
        refreshed, _ = self.compiler.refresh_plan_compilation(self.plan())
        with patch.object(self.planner, "compile_storyboard", side_effect=AssertionError("already current")):
            same, receipt = self.compiler.refresh_plan_compilation(refreshed)
        self.assertIs(same, refreshed)
        self.assertIsNone(receipt)

    def test_repaired_speech_clock_is_shared_by_native_prompt_and_scene(self):
        plan = self.plan()
        scene = plan["scenes"][0]
        scene["dialogues"][0].update(start=5.2, end=5.5)
        scene["start_facts"], scene["end_facts"] = {"杯子": "桌上"}, {"杯子": "手中"}
        original = copy.deepcopy(scene)
        plan["structured_scenes"] = copy.deepcopy(plan["scenes"])
        plan["dialogue_pacing"] = {"outline_review": {"status": "reviewed"}}
        refreshed, _ = self.compiler.refresh_plan_compilation(plan)
        dialogue = refreshed["scenes"][0]["dialogues"][0]
        self.assertLess(dialogue["start"], 5.2)
        self.assertLessEqual(dialogue["end"], 5.1)
        body = json.loads(refreshed["compiled_prompts"][0])["integrated_multimodal_description"]
        self.assertIn(f"第{dialogue['start']:g}至{dialogue['end']:g}秒", body)
        self.assertEqual(refreshed["structured_scenes"][0]["dialogues"], refreshed["scenes"][0]["dialogues"])
        self.assertTrue(refreshed["scenes"][0]["dialogue_timing_repairs"])
        self.assertEqual(refreshed["dialogue_pacing"]["trailing_silence_seconds"], 0.4)
        self.assertEqual(refreshed["dialogue_pacing"]["outline_review"], {"status": "reviewed"})
        for key in ("start_state", "end_state", "image_prompt", "reference_ids", "start_facts", "end_facts"):
            self.assertEqual(refreshed["scenes"][0][key], original[key])

    def test_resolved_speaker_is_shared_by_native_prompt_and_scene(self):
        plan = self.plan()
        plan["dialogue_whitelist"][0]["speaker_locked"] = False
        plan["scenes"][0]["dialogues"][0]["speaker_description"] = "S2成年朋友"
        refreshed, _ = self.compiler.refresh_plan_compilation(plan)
        self.assertEqual(refreshed["scenes"][0]["dialogues"][0]["speaker_id"], "S2")
        self.assertIn("(S2)", refreshed["compiled_prompts"][0])
        self.assertTrue(refreshed["scenes"][0]["speaker_assignment_repairs"])

    def test_explicit_native_override_survives_future_compiler_refresh(self):
        plan = self.edits.apply_plan_edits(self.plan(), {"1": {"video_prompt": "朋友把蓝色杯子递近。"}})
        refreshed, receipt = self.compiler.refresh_plan_compilation(plan)
        self.assertTrue(receipt["native_prompts_preserved"])
        self.assertIn("蓝色杯子", refreshed["compiled_prompts"][0])
        self.assertEqual(refreshed["scenes"][0]["native_prompt_override"], "朋友把蓝色杯子递近。")
        self.assertIn("<d>[Chinese] 请喝水。</d>", refreshed["compiled_prompts"][0])

    def test_saved_legacy_native_edit_is_reapplied_after_base_compile(self):
        plan = self.plan()
        refreshed, _ = self.compiler.refresh_plan_compilation(
            plan, saved_edits={"1": {"video_text": "朋友将白色杯子递近。"}})
        self.assertIn("白色杯子", refreshed["compiled_prompts"][0])
        self.assertEqual(refreshed["scenes"][0]["video_text"], "朋友将白色杯子递近。")

    def test_later_structure_edit_replaces_prior_native_override(self):
        plan = self.edits.apply_plan_edits(self.plan(), {"1": {"video_prompt": "朋友把蓝色杯子递近。"}})
        plan = self.edits.apply_plan_edits(plan, {"1": {"action": "右手将绿色杯子放到桌面。"}})
        refreshed, _ = self.compiler.refresh_plan_compilation(plan)
        self.assertNotIn("native_prompt_override", refreshed["scenes"][0])
        self.assertIn("绿色杯子", refreshed["compiled_prompts"][0])
        self.assertNotIn("蓝色杯子", refreshed["compiled_prompts"][0])

    def test_legacy_imported_native_prompt_is_preserved(self):
        plan = self.plan()
        plan["compiled_prompts"][0] = plan["compiled_prompts"][0].replace("杯子轻响", "用户自己的声景")
        refreshed, receipt = self.compiler.refresh_plan_compilation(plan, preserve_legacy_native=True)
        self.assertEqual(refreshed["compiled_prompts"], plan["compiled_prompts"])
        self.assertTrue(refreshed["native_prompts_authoritative"])
        self.assertFalse(receipt["prompts_changed"])

    def test_compiler_revision_invalidates_only_video_stage(self):
        kwargs = {"剧本": "朋友说请喝水", "随机种子": 42, "生成模式": "AI生成分镜"}
        options = {"image_settings": {}, "width": 640, "height": 480}
        before = self.cache.stage_fingerprints(kwargs, [], [5.5], **options)
        with patch.object(self.compiler, "prompt_compiler_identity", return_value={"schema": "next", "implementation": "changed"}):
            after = self.cache.stage_fingerprints(kwargs, [], [5.5], **options)
        self.assertEqual(self.cache.changed_stages(before, after), ["video"])


if __name__ == "__main__":
    unittest.main()
