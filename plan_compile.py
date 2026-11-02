"""Refresh saved native prompts without replanning or replacing storyboard images."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

PROMPT_COMPILER_SCHEMA = "h3-prompt-compiler/2026-11-03-1"
_COMPILER_FILES = ("planner.py", "contract.py", "motion_continuity.py", "prompt_formats.py",
                   "plan_edits.py", "plan_compile.py")


def prompt_compiler_identity():
    directory = Path(__file__).parent
    digest = hashlib.sha256(PROMPT_COMPILER_SCHEMA.encode("utf-8"))
    for name in _COMPILER_FILES:
        digest.update(name.encode("utf-8"))
        digest.update((directory / name).read_bytes().replace(b"\r\n", b"\n"))
    return {"schema": PROMPT_COMPILER_SCHEMA, "implementation": digest.hexdigest()}


def refresh_plan_compilation(plan, *, saved_edits=None, preserve_legacy_native=False):
    """Recompile authoritative structured scenes, keeping explicit native edits.

    Imported legacy plans lack native-edit provenance. Their supplied native
    prompts remain authoritative; future edits record their origin explicitly.
    No file, image, language model or generation service is touched here.
    """
    identity = prompt_compiler_identity()
    if plan.get("prompt_compiler") == identity:
        return plan, None
    result = copy.deepcopy(plan)
    previous = result.get("prompt_compiler")
    if result.get("native_prompts_authoritative") or preserve_legacy_native and previous is None:
        result["native_prompts_authoritative"] = True
        result["prompt_compiler"] = identity
        return result, {"previous": previous, "current": identity,
                        "native_prompts_preserved": True, "prompts_changed": False}

    from .planner import compile_storyboard
    scenes = result["scenes"]
    native_edits = {}
    for index, scene in enumerate(scenes, 1):
        if scene.get("native_prompt_override"):
            native_edits[str(index)] = {"video_prompt": scene["native_prompt_override"]}
        elif scene.get("video_text"):
            native_edits[str(index)] = {"video_text": scene["video_text"]}
    for index, values in (saved_edits or {}).items():
        explicit = {key: values[key] for key in ("video_prompt", "video_text") if key in values}
        if explicit:
            native_edits[str(index)] = explicit
    compiled = compile_storyboard(
        {"continuity": result.get("continuity", ""), "constraints": result.get("constraints", ""),
         **({"environment_anchor": result["environment_anchor"]} if "environment_anchor" in result else {}),
         "scenes": scenes},
        script=result.get("raw_script", ""), count=len(scenes), seconds=result["scene_seconds"],
        dialogues=result.get("dialogue_whitelist", []), voice_mode=result["speech_mode"],
        input_mode=result.get("input_mode", "I2V"))
    result["compiled_prompts"] = [item["json"] for item in compiled["compiled"]]
    result["prompts"] = [item["prompt"] for item in compiled["compiled"]]
    for scene, normalized, prompt in zip(scenes, compiled["scenes"], result["prompts"]):
        # The compiler may repair a rushed clock or resolve the source speaker.
        # Keep consumers of the structured plan on that same speech assignment;
        # all image, reference and state fields remain the user's saved values.
        scene["dialogues"] = copy.deepcopy(normalized["dialogues"])
        for key in ("dialogue_timing_repairs", "speaker_assignment_repairs"):
            if key in normalized:
                scene[key] = copy.deepcopy(normalized[key])
        scene["video_prompt"] = prompt
    if "structured_scenes" in result:
        result["structured_scenes"] = copy.deepcopy(scenes)
    if native_edits:
        from .plan_edits import apply_plan_edits
        result = apply_plan_edits(result, {"scenes": native_edits})
    if "dialogue_pacing" in result:
        from .planner import _dialogue_pacing_summary
        result["dialogue_pacing"] = _dialogue_pacing_summary(
            result["scenes"], result["scene_seconds"], result["dialogue_pacing"].get("outline_review"))
    result["prompt_compiler"] = identity
    return result, {"previous": previous, "current": identity,
                    "native_prompts_preserved": bool(native_edits),
                    "prompts_changed": result["compiled_prompts"] != plan.get("compiled_prompts")}
