"""Optional processing must be inert when off and preserve unstiched pixels."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "video_postprocess_fixture"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + ".video_postprocess", ROOT / "video_postprocess.py")
post = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = post
spec.loader.exec_module(post)


class VideoPostprocessTests(unittest.TestCase):
    def test_disabled_passes_keep_exact_objects_and_do_not_sample(self):
        settings = post.normalize_settings()
        original = object()
        with patch.object(post, "_sample", side_effect=AssertionError("must not sample")):
            self.assertIs(post.refine_latent({}, None, original, 1, settings)[0], original)
            self.assertIs(post.refine_faces({}, "", original, 1, settings)[0], original)

    def test_invalid_parameters_fail_before_model_work(self):
        for raw in ({"face_denoise": float("nan")}, {"refine_steps": 0}, {"face_canvas": 10000}):
            with self.assertRaises(ValueError):
                post.normalize_settings(raw)

    def test_distilled_pika_rejects_partial_resampling(self):
        settings = post.normalize_settings({"refine_enabled": True, "face_enabled": True})
        with self.assertRaisesRegex(ValueError, "六步"):
            post.refine_latent({"pika_enabled": True}, None, None, 1, settings)
        with self.assertRaisesRegex(ValueError, "六步"):
            post.refine_faces({"pika_enabled": True}, "", None, 1, settings)

    def test_zero_blend_preserves_every_source_pixel(self):
        import torch
        from importlib import import_module
        stitch = import_module(PACKAGE + ".face_refine_helpers.stitch")
        original = torch.rand((2, 32, 32, 3))
        transform = {"boxes": [(0., 0., 32., 32.)] * 2, "canvas": (32, 32), "src_size": (32, 32)}
        output = stitch.stitch_faces(original, torch.ones_like(original), transform,
                                     {"blend": 0, "colour_match": 0, "feather": 0, "mask_dilation": 0})
        self.assertTrue(torch.equal(output, original))

    def test_cut_boundaries_keep_original_identity(self):
        import torch
        from importlib import import_module
        stitch = import_module(PACKAGE + ".face_refine_helpers.stitch")
        original = torch.zeros((24, 16, 16, 3))
        output = stitch.fade_stitch_at_seams(torch.ones_like(original), original, head_frames=6, tail_frames=6)
        self.assertTrue(torch.equal(output[0], original[0]))
        self.assertTrue(torch.equal(output[10], torch.ones_like(output[10])))
        self.assertLess(float(output[-1].mean()), .1)


if __name__ == "__main__":
    unittest.main()
