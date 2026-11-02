"""Native soundtrack checks flag uncertain speech without editing media; CPU only."""
import importlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class SpeechReviewTests(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType("speech_review_fixture")
        package.__path__ = [str(ROOT)]
        engine = types.ModuleType(package.__name__ + ".speech_engine")
        engine.transcribe = Mock(return_value={"text": "请喝水。"})
        self.modules = patch.dict(sys.modules, {package.__name__: package, engine.__name__: engine})
        self.modules.start()
        self.engine = engine
        self.review = importlib.import_module(package.__name__ + ".speech_review")

    def tearDown(self):
        self.modules.stop()
        for name in list(sys.modules):
            if name.startswith("speech_review_fixture"):
                sys.modules.pop(name, None)

    def test_punctuation_case_and_fullwidth_differences_are_allowed(self):
        result = self.review.compare_recognized_speech("下午好！请喝水。HELLO", ["下午好，请喝水。", "Ｈｅｌｌｏ"])
        self.assertEqual(result["status"], "matched")

    def test_prefix_suffix_missing_words_and_wrong_words_need_review(self):
        for recognized in ("奇怪杂音请喝水", "请喝水随后乱说", "请喝", "请核实", ""):
            with self.subTest(recognized=recognized):
                result = self.review.compare_recognized_speech(recognized, ["请喝水。"])
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(result["recognized"], recognized)

    def test_no_dialogue_still_detects_unexpected_recognized_speech(self):
        self.assertEqual(self.review.compare_recognized_speech("", [])["status"], "matched")
        result = self.review.compare_recognized_speech("下午好", [])
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["reason"], "unexpected_speech")

    def test_summary_only_uses_existing_videos_and_prioritizes_review(self):
        scenes = [{"index": 1, "video_path": "first.mp4", "report": {"speech_review": {"status": "matched"}}},
                  {"index": 2, "video": {"filename": "second.mp4"}, "speech_review": {"status": "needs_review"}},
                  {"index": 3, "status": "完成", "speech_review": {"status": "unavailable"}},
                  {"index": 4, "status": "等待", "speech_review": {"status": "needs_review"}}]
        self.assertEqual(self.review.summarize_reviews(scenes),
                         {"status": "needs_review", "needs_review": [2], "unavailable": [3]})
        self.assertEqual(self.review.summarize_reviews(scenes[:1]),
                         {"status": "matched", "needs_review": [], "unavailable": []})
        self.assertEqual(self.review.summarize_reviews(scenes[2:3]),
                         {"status": "unavailable", "needs_review": [], "unavailable": [3]})
        self.assertIsNone(self.review.summarize_reviews(scenes[3:]))
        self.assertIsNone(self.review.summarize_reviews([{"video_path": "legacy.mp4"}]))

    def test_actual_waveform_is_unmodified_and_only_cpu_asr_is_requested(self):
        waveform = np.full((1, 2, 16000), .25, dtype=np.float32)
        before = waveform.copy()
        result = self.review.review_model_audio({"waveform": waveform, "sample_rate": 16000}, ["请喝水。"])
        self.assertEqual(result["status"], "matched")
        self.assertTrue(np.array_equal(waveform, before))
        args, kwargs = self.engine.transcribe.call_args
        self.assertEqual(kwargs, {"language": "auto", "normalize": False, "threads": 4})
        with wave.open(io.BytesIO(args[0]), "rb") as reader:
            self.assertEqual((reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes()),
                             (1, 2, 16000, 16000))

    def test_resampling_preserves_real_duration(self):
        waveform = np.zeros((1, 2, 32000), dtype=np.float32)
        result = self.review.review_model_audio({"waveform": waveform, "sample_rate": 32000}, ["请喝水。"])
        self.assertEqual(result["status"], "matched")
        with wave.open(io.BytesIO(self.engine.transcribe.call_args.args[0]), "rb") as reader:
            self.assertEqual(reader.getnframes(), 16000)
        self.assertEqual(result["duration_seconds"], 1)

    def test_missing_model_is_unavailable_not_matched_or_rewritten(self):
        self.engine.transcribe.side_effect = RuntimeError("SenseVoice模型文件不完整")
        result = self.review.review_model_audio({"waveform": np.zeros((1, 1, 16000)), "sample_rate": 16000}, ["请喝水。"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("模型文件不完整", result["error"])
        self.assertEqual(result["recognized"], "")

    def test_invalid_or_unbounded_audio_never_starts_recognition(self):
        invalid = ({"waveform": np.zeros((1, 1, 16000 * 16)), "sample_rate": 16000},
                   {"waveform": np.zeros((2, 1, 16000)), "sample_rate": 16000},
                   {"waveform": np.zeros((1, 3, 16000)), "sample_rate": 16000},
                   {"waveform": np.zeros((1, 1, 16000)), "sample_rate": float("nan")},
                   {"waveform": np.full((1, 1, 16000), np.nan), "sample_rate": 16000})
        for audio in invalid:
            with self.subTest(shape=audio["waveform"].shape, rate=audio["sample_rate"]):
                self.assertEqual(self.review.review_model_audio(audio, ["请喝水。"])['status'], 'unavailable')
        self.engine.transcribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
