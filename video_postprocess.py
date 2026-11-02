"""Optional H3 latent resampling and tracked face refinement; disabled by default."""
from __future__ import annotations

import math
import time


def _number(value, default, low, high):
    value = default if value is None else float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"视频精修参数应在 {low}～{high} 范围内。")
    return value


def normalize_settings(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    return {
        "refine_enabled": bool(raw.get("refine_enabled", False)),
        "refine_steps": int(_number(raw.get("refine_steps"), 4, 1, 30)),
        "refine_denoise": _number(raw.get("refine_denoise"), .25, .02, .8),
        "face_enabled": bool(raw.get("face_enabled", False)),
        "face_detector": str(raw.get("face_detector") or "face_yolov8m.pt"),
        "face_steps": int(_number(raw.get("face_steps"), 8, 1, 30)),
        "face_denoise": _number(raw.get("face_denoise"), .4, .02, .8),
        "face_canvas": int(_number(raw.get("face_canvas"), 512, 256, 768)) // 32 * 32,
        "face_confidence": _number(raw.get("face_confidence"), .35, .05, .95),
        "face_crop_factor": _number(raw.get("face_crop_factor"), 2.5, 1.2, 5),
        "face_feather": int(_number(raw.get("face_feather"), 24, 0, 128)),
        "face_blend": _number(raw.get("face_blend"), 1, 0, 1),
    }


def _result(output):
    return output.result if hasattr(output, "result") else output


def validate_available(settings):
    """Fail before base sampling when an enabled face dependency is missing."""
    if not settings["face_enabled"]:
        return
    import importlib.util
    from pathlib import Path
    import folder_paths
    if importlib.util.find_spec("ultralytics") is None:
        raise ValueError("视频脸修需要在当前ComfyUI Python环境安装ultralytics。")
    name = settings["face_detector"]
    if Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("脸部检测器应选择models目录内的模型文件。")
    for category in ("ultralytics_bbox", "ultralytics"):
        try:
            if folder_paths.get_full_path(category, name):
                return
        except (KeyError, ValueError):
            pass
    root = Path(folder_paths.models_dir)
    if any((root / location / name).is_file() for location in
           ("ultralytics/bbox", "ultralytics", "ultralytics/segm")):
        return
    raise ValueError(f"缺少脸部检测模型{name}，请放入models/ultralytics/bbox/。")


def _sample(model, positive, latent, *, steps, denoise, seed):
    from comfy_extras.nodes_custom_sampler import (
        BasicGuider, BasicScheduler, KSamplerSelect, RandomNoise, SamplerCustomAdvanced,
    )
    import comfy.model_management as mm
    mm.throw_exception_if_processing_interrupted()
    guider = _result(BasicGuider.execute(model, positive))[0]
    sigmas = _result(BasicScheduler.execute(model, "simple", int(steps), float(denoise)))[0]
    noise = _result(RandomNoise.execute(int(seed)))[0]
    sampler = _result(KSamplerSelect.execute("euler"))[0]
    return _result(SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent))[0]


def refine_latent(bundle, positive, samples, seed, settings):
    """Refine the joint AV latent; callers preserve the original audio stream."""
    if not settings["refine_enabled"]:
        return samples, {"enabled": False}
    if bundle.get("pika_enabled") or bundle.get("sampling_preset") == "Pika H3 Flex6":
        raise ValueError("Pika固定六步日程暂不支持二次采样，请关闭二采后重试。")
    started = time.perf_counter()
    from .video_progress import sampling_video_progress
    with sampling_video_progress(bundle, 'latent_refining', settings['refine_steps']):
        result = _sample(bundle["model"], positive, samples,
                         steps=settings["refine_steps"], denoise=settings["refine_denoise"], seed=seed + 100003)
    return result, {"enabled": True, "steps": settings["refine_steps"],
                    "denoise": settings["refine_denoise"], "seconds": round(time.perf_counter() - started, 3)}


def refine_faces(bundle, prompt, frames, seed, settings):
    if not settings["face_enabled"]:
        return frames, {"enabled": False}
    if bundle.get("pika_enabled") or bundle.get("sampling_preset") == "Pika H3 Flex6":
        raise ValueError("Pika固定六步日程暂不支持裁剪脸修，请关闭脸修后重试。")
    import torch
    import nodes
    import comfy.model_management as mm
    from comfy_extras.nodes_minimax_h3 import MiniMaxH3ImageToVideo
    from .face_refine_helpers.track import track_and_crop, _DETECTOR_CACHE
    from .face_refine_helpers.inject import inject_video_latent
    from .face_refine_helpers.stitch import stitch_faces, fade_stitch_at_seams
    from .video_progress import notify_video_phase, sampling_video_progress
    started = time.perf_counter()
    canvas = settings["face_canvas"]
    pack = {"detector": settings["face_detector"], "confidence": settings["face_confidence"],
            "crop_factor": settings["face_crop_factor"], "select": "largest_face",
            "canvas_mode": "manual", "canvas_width": canvas, "canvas_height": canvas,
            "paste_region": "face_ellipse", "mask_dilation": 8,
            "feather": settings["face_feather"], "blend": settings["face_blend"], "colour_match": 1.0}
    try:
        mm.throw_exception_if_processing_interrupted()
        base = frames.detach().cpu().float()
        notify_video_phase(bundle, 'face_detection')
        crops, transform, note = track_and_crop(base, pack)
        if crops is None:
            return frames, {"enabled": True, "applied": False, "note": note,
                            "seconds": round(time.perf_counter() - started, 3)}
        n_frames = len(crops)
        generated_frames = max(5, math.ceil((n_frames - 5) / 17) * 17 + 5)
        if n_frames < generated_frames:
            crops = torch.cat((crops, crops[-1:].expand(generated_frames - n_frames, -1, -1, -1)))
        notify_video_phase(bundle, 'face_encoding')
        positive, latent = _result(MiniMaxH3ImageToVideo.execute(
            clip=bundle["clip"], vae=bundle["vae"],
            prompt="同一人物脸部近景，保持原有身份、表情、朝向和说话动作。" + str(prompt),
            width=canvas, height=canvas, length=generated_frames, first_frame=crops[:1]))
        latent = inject_video_latent(latent, crops, bundle["vae"])
        with sampling_video_progress(bundle, 'face_sampling', settings['face_steps']):
            sampled = _sample(bundle["model"], positive, latent,
                              steps=settings["face_steps"], denoise=settings["face_denoise"], seed=seed + 200003)
        notify_video_phase(bundle, 'face_decoding')
        repaired = nodes.VAEDecode().decode(bundle["vae"], sampled)[0][:n_frames].detach().cpu().float()
        if len(repaired) != n_frames:
            raise RuntimeError("脸部修复帧数不足，原成片未被覆盖。")
        notify_video_phase(bundle, 'face_stitching')
        stitched = stitch_faces(base, repaired, transform, pack)
        stitched = fade_stitch_at_seams(stitched, base, head_frames=6, tail_frames=6)
        return stitched, {"enabled": True, "applied": True, "note": note,
                          "steps": settings["face_steps"], "denoise": settings["face_denoise"],
                          "seconds": round(time.perf_counter() - started, 3)}
    finally:
        _DETECTOR_CACHE.clear()
        mm.soft_empty_cache()
