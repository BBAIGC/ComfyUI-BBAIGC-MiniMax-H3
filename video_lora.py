"""Independent H3 video LoRA slots; inspect headers without loading tensors."""
from __future__ import annotations

import json
import math
from pathlib import Path
import struct


def sampling_profile(slot, default_steps=8):
    """Only explicit per-slot settings request an additional refinement pass."""
    if not any(key in slot for key in ("steps", "cfg")):
        return {}
    steps, cfg = slot.get("steps", default_steps), slot.get("cfg", 1.0)
    if isinstance(steps, bool) or isinstance(cfg, bool):
        raise ValueError("LoRA步数或CFG无效。")
    try:
        steps, cfg = float(steps), float(cfg)
    except (TypeError, ValueError) as exc:
        raise ValueError("LoRA步数或CFG无效。") from exc
    if not math.isfinite(steps) or not steps.is_integer() or not 1 <= steps <= 100:
        raise ValueError("LoRA细化步数应为1～100的整数。")
    if not math.isfinite(cfg) or not 0 <= cfg <= 30:
        raise ValueError("LoRA细化CFG应为0～30。")
    return {"steps": int(steps), "cfg": cfg}


def normalize_video_loras(value="[]", *, verify_files=True, target_architecture=None):
    import folder_paths
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except (TypeError, ValueError) as exc:
            raise ValueError("视频LoRA配置无效，请重新选择。") from exc
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("视频LoRA最多20个槽位。")
    result = []
    for slot in value:
        if not isinstance(slot, dict) or type(slot.get("enabled", False)) is not bool:
            raise ValueError("视频LoRA槽位或开关无效。")
        if not slot.get("enabled", False):
            continue
        name = slot.get("name", "")
        strength = slot.get("strength", 1.0)
        if not isinstance(name, str) or not name or isinstance(strength, bool):
            raise ValueError("启用的视频LoRA须选择文件与有效强度。")
        try:
            strength = float(strength)
        except (TypeError, ValueError) as exc:
            raise ValueError("视频LoRA强度无效。") from exc
        if not math.isfinite(strength) or not -4 <= strength <= 4:
            raise ValueError("视频LoRA强度应在-4～4之间。")
        if not strength:
            continue
        if verify_files:
            if name not in folder_paths.get_filename_list("loras"):
                raise ValueError(f"找不到视频LoRA：{name}")
            path = folder_paths.get_full_path("loras", name)
            if not path or Path(path).suffix.lower() != ".safetensors":
                raise ValueError("视频LoRA须为可验证架构的safetensors文件。")
            with Path(path).open("rb") as stream:
                prefix = stream.read(8)
                size = struct.unpack("<Q", prefix)[0] if len(prefix) == 8 else 0
                if not 2 <= size <= 16 * 1024 * 1024:
                    raise ValueError(f"视频LoRA文件头无效：{name}")
                try:
                    header = json.loads(stream.read(size))
                except (ValueError, UnicodeError) as exc:
                    raise ValueError(f"视频LoRA文件头无效：{name}") from exc
            metadata = header.get("__metadata__", {})
            architecture = " ".join(str(metadata.get(key, "")) for key in
                                    ("base_model", "ss_base_model_version", "modelspec.architecture")).lower()
            keys = [key for key in header if key != "__metadata__"]
            h3_blocks = any("blocks." in key and ".attn.qkv_proj." in key for key in keys)
            if not h3_blocks or not ("minimax" in architecture or "h3" in architecture):
                raise ValueError(f"{name}未标注MiniMax H3架构；图片、LTX或其他模型LoRA不能用于H3视频。")
            if target_architecture == "fl2va" and "ref2va" in architecture:
                raise ValueError(f"{name}属于H3 REF2VA，当前FL2VA基础路线不兼容；请切换匹配模型与LoRA。")
            if "turbo" in name.lower() or "pika_h3_flex" in name.lower():
                raise ValueError("加速LoRA请使用独立的加速开关与4/8步选择，不要在额外视频LoRA重复叠加。")
        result.append({"enabled": True, "name": name, "strength": strength, **sampling_profile(slot)})
    return result


def apply_video_loras(model, slots):
    import nodes
    for slot in slots:
        model = nodes.LoraLoaderModelOnly().load_lora_model_only(model, slot["name"], slot["strength"])[0]
    return model


def refine_video_lora_slots(bundle, positive, latent, seed, sampler_name, scheduler):
    """Apply slot sampling profiles in order on the combined enabled LoRA model.

    Strengths already affect the base model. Each explicit profile adds a real
    low-denoise pass; legacy slots containing only strength keep one base pass.
    """
    profiles = [slot for slot in bundle.get("video_loras", [])
                if slot.get("enabled") and slot.get("strength", 0) and ("steps" in slot or "cfg" in slot)]
    if not profiles:
        return latent, []
    if bundle.get("pika_enabled") or bundle.get("sampling_route") == "pika_h3_flex":
        raise ValueError("Pika固定六步日程不能叠加LoRA细化采样，请关闭额外视频LoRA或切换普通H3。")
    import nodes
    import comfy.model_management as mm
    from comfy_extras.nodes_custom_sampler import (
        BasicGuider, CFGGuider, BasicScheduler, KSamplerSelect, RandomNoise, SamplerCustomAdvanced,
    )
    result = lambda output: output.result if hasattr(output, "result") else output
    receipts = []
    negative = None
    for index, slot in enumerate(profiles):
        mm.throw_exception_if_processing_interrupted()
        profile = sampling_profile(slot)
        if profile["cfg"] == 1:
            guider = result(BasicGuider.execute(bundle["model"], positive))[0]
        else:
            if negative is None:
                negative = nodes.ConditioningZeroOut().zero_out(positive)[0]
            guider = result(CFGGuider.execute(bundle["model"], positive, negative, profile["cfg"]))[0]
        sigmas = result(BasicScheduler.execute(bundle["model"], scheduler, profile["steps"], .25))[0]
        noise = result(RandomNoise.execute(int(seed) + 300003 + index))[0]
        sampler = result(KSamplerSelect.execute(sampler_name))[0]
        latent = result(SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent))[0]
        receipts.append({"name": slot["name"], "steps": profile["steps"], "cfg": profile["cfg"],
                         "denoise": .25, "combined_loras": True})
    return latent, receipts
