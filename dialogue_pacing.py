"""Conservative speech windows with room to finish before a cut."""
from __future__ import annotations

import re


def minimum_speech_seconds(text):
    # A feasibility guard, not an assertion that native model speech is exact.
    cjk = len(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    words = len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text))
    return max(0.85, cjk / 5.0 + words / 3.0 + 0.15)


def fit_dialogue_windows(entries, registry, seconds):
    """Preserve valid windows; repair rushed/tail-cut windows inside this shot.

    Identity, order, words and shot assignment never change. If the speech cannot
    fit, fail before loading H3 rather than silently trimming an utterance.
    """
    if not entries:
        return []
    margin, gap = min(0.4, seconds * 0.08), 0.12
    minima = [minimum_speech_seconds(registry[entry["id"]]["text"]) for entry in entries]
    latest = seconds - margin
    if sum(minima) + gap * (len(entries)-1) > latest + 1e-6:
        raise ValueError("本镜对白无法在切镜前自然说完，请增加该镜时长或调整分镜；原台词未截断。")
    good = all(float(entry["end"])-float(entry["start"]) >= minimum-1e-6
               for entry, minimum in zip(entries, minima)) and float(entries[-1]["end"]) <= latest + 1e-6
    if good:
        return []
    original = [(float(entry["start"]), float(entry["end"])) for entry in entries]
    cursor = latest
    repaired = [None] * len(entries)
    for index in range(len(entries)-1, -1, -1):
        start, end = original[index]
        end = min(end, cursor)
        start = min(start, end-minima[index])
        repaired[index] = (start, end)
        cursor = start-gap
    if repaired[0][0] < 0:
        cursor = 0.0
        for index, ((start, end), minimum) in enumerate(zip(repaired, minima)):
            start = max(cursor, start)
            end = max(end, start+minimum)
            repaired[index] = (start, end)
            cursor = end+gap
    changes = []
    for entry, before, (start, end) in zip(entries, original, repaired):
        entry.update(start=round(start, 6), end=round(end, 6))
        if before != (entry["start"], entry["end"]):
            changes.append({"id": entry["id"], "before": list(before),
                            "after": [entry["start"], entry["end"]]})
    return changes
