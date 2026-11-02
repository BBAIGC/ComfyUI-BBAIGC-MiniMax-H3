"""Prompt guidance for internal boundaries of an explicitly continuous take.

This does not blend clips, create motion conditioning, or change frame clocks.
"""
from __future__ import annotations

import re


_CONTINUOUS = re.compile(
    r"一镜到底|长镜头|(?:一个|单个)?连续镜头|连续拍摄|"
    r"不(?:要)?切镜|不(?:要)?切换镜头|无剪辑|无切镜|"
    r"\b(?:single|one)[ -](?:continuous[ -])?(?:take|shot)\b|"
    r"\b(?:unbroken|continuous)[ -](?:take|shot)\b|\bno[ -]cuts\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(
    r"(?:不要|不要求|不需要|不必|无需|避免|取消|并非|不是)\s*"
    r"(?:一镜到底|长镜头|(?:一个|单个)?连续镜头|连续拍摄)|"
    r"\b(?:not|avoid|without)\s+(?:a\s+)?(?:single|one|continuous|unbroken)"
    r"[ -](?:continuous[ -])?(?:take|shot)\b",
    re.IGNORECASE,
)


def is_continuous_take(raw_script):
    """Enable guidance only from the user's explicit continuous-take request."""
    if not isinstance(raw_script, str):
        return False
    return bool(_CONTINUOUS.search(_NEGATED.sub("", raw_script)))


def single_action_note(action, raw_script):
    """Give a finite gesture an endpoint without adding another story event."""
    clauses = []
    for gesture, ending in (("点头", "头部自然回到舒适位置"),
                            ("摇头", "头部自然回到舒适位置"),
                            ("挥手", "手臂自然落回原位")):
        if gesture not in str(action):
            continue
        if re.search(re.escape(gesture) + r"[^。；;]{0,20}(?:一次|一回|\bonce\b)|(?:一次|一回)[^。；;]{0,20}" +
                     re.escape(gesture), str(action), re.I):
            continue
        if re.search(r"(?:反复|重复|不停|持续|一直|多次|连续)\s*(?:地|轻轻|微微)?" + gesture +
                     "|" + gesture + r"\s*(?:[两二三四五六七八九十\d]+次|不止)", str(raw_script)):
            continue
        clauses.append(f"这个短动作只自然完成一次，随后{ending}，余下时间保持自然呼吸与眼神反应")
    return "；".join(clauses) + "。" if clauses else ""


def finite_gesture_timing(action, raw_script, seconds):
    """Bound a source-backed short gesture to a single early motion interval.

    Merely saying 'once' in an eight-second motion description can be interpreted
    as an eight-second loop. A separate neutral remainder gives H3 an endpoint.
    Explicit source repetition and explicit source timestamps remain untouched.
    """
    text = str(action)
    if float(seconds) < 3 or re.search(r"\d+(?:\.\d+)?\s*(?:秒|s\b)", text, re.I):
        return text
    gestures = [word for word in ('点头', '摇头', '挥手') if word in text]
    if len(gestures) != 1:
        return text
    gesture = gestures[0]
    if gesture not in str(raw_script):
        return text
    # A scene containing a gesture and another event must keep its own clock;
    # do not compress hat removal, cup handover, etc. into a short-gesture slot.
    if re.search(r'摘|脱|穿|拿起|端起|放下|放在|递|走|跑|跳|站起|坐下|打开|关上', text):
        return text
    if re.search(r'(?:第\s*\d+(?:\.\d+)?\s*秒|\d+(?:\.\d+)?\s*[-–至到～]\s*\d+(?:\.\d+)?\s*秒)'
                 r'[^。；;\n]{0,20}' + re.escape(gesture), str(raw_script)):
        return text
    if gesture and re.search(r"(?:反复|重复|不停|持续|一直|多次|连续)[^。；;]{0,12}" + gesture +
            "|" + gesture + r"\s*(?:[两二三四五六七八九十\d]+次|不止)", str(raw_script)):
        return text
    finish = min(2.0, float(seconds)/3)
    resting = ('头部回到自然中立位置，目光放松，保持自然呼吸与眨眼' if gesture in ('点头', '摇头')
               else '手臂自然回到原位，目光放松，保持自然呼吸与眨眼')
    return f"第0.5至{finish:g}秒：{text} 第{finish:g}至{float(seconds):g}秒：{resting}。"


def motion_continuity_note(raw_script, scene_index, scene_count, language="zh"):
    """Return one neutral video-body note; scene_index is zero based.

    Explicit story pauses and final holds remain authoritative. No new action,
    time, dialogue, editing effect, or technical speaker label is introduced.
    """
    if (type(scene_count) is not int or type(scene_index) is not int
            or scene_count < 1 or not 0 <= scene_index < scene_count):
        raise ValueError("分镜索引或总数无效。")
    if language not in ("zh", "en"):
        raise ValueError("连续动作说明语言应为zh或en。")
    if scene_count == 1 or not is_continuous_take(raw_script):
        return ""
    if scene_index == scene_count - 1:
        if language == "en":
            return (
                "This is the final segment of one continuous take. Continue the existing action and camera "
                "movement naturally from the preceding segment, without a fresh start. Preserve any pause "
                "or final hold explicitly requested by the original script; otherwise let the existing "
                "movement continue through the final frame rather than stopping early."
            )
        return (
            "本段是同一次连续拍摄的最后一段，承接前段原有动作和运镜，不重新起势。"
            "保留原剧本明确要求的停顿或最终停留；未要求停留时，原有运动自然持续至最后一帧，"
            "不要因片段即将结束而提前停住。"
        )
    if language == "en":
        return (
            "This segment is part of one continuous take. Its ending state is an instantaneous frame "
            "within the ongoing action, not a cue to settle into a held pose. Carry the action and camera "
            "movement already requested by the script through the boundary, with their direction and pace "
            "continuing into the next segment. Begin from the preceding instant without restarting the "
            "action. Preserve explicit story pauses and intentional stillness; introduce no extra action."
        )
    return (
        "本段属于同一次连续拍摄。结束状态是进行中动作的瞬间画面，不表示提前完成动作并保持姿势。"
        "原剧本已有的动作与运镜自然穿过分段边界，方向和节奏衔接下一段；"
        "开场承接上一瞬间，不重新起势。保留剧情明确要求的停顿与静止，不额外添加动作。"
    )


def apply_motion_continuity(fields, raw_script, scene_index, scene_count):
    """Return native video fields with one idempotent, non-spoken motion note.

    Global indices are mandatory: a planning batch's last clip is not necessarily
    the full video's last clip. Existing exact helper notes are removed outside
    dialogue blocks before inserting the current note; spoken text is untouched.
    """
    note = motion_continuity_note(raw_script, scene_index, scene_count)
    result = dict(fields)
    field = ("integrated_multimodal_description" if "integrated_multimodal_description" in result
             else "detailed_description" if "detailed_description" in result else None)
    if field is None:
        raise ValueError("视频描述字段缺失，无法添加连续动作说明。")
    body = result[field]
    if not isinstance(body, str):
        raise ValueError("视频描述字段必须是文本。")
    generated_notes = [motion_continuity_note("一镜到底", index, 2, language)
                       for language in ("zh", "en") for index in (0, 1)]
    parts = re.split(r"(<d>.*?</d>)", body, flags=re.S)
    for index, part in enumerate(parts):
        if part.startswith("<d>"):
            continue
        for previous in generated_notes:
            part = part.replace(previous, "")
        parts[index] = part
    body = "".join(parts).strip()
    if note:
        opening = re.match(r"\[Shot 1\]\s*", body)
        if opening:
            body = "[Shot 1] " + note + " " + body[opening.end():].lstrip()
        else:
            body = note + " " + body
    result[field] = body
    return result
