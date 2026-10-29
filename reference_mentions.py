"""Normalize clear source-image mentions without rewriting spoken quotes."""
from __future__ import annotations

import re

_QUOTE = re.compile(r'(<d>.*?</d>|“[^”]*”|「[^」]*」|『[^』]*』|‘[^’]*’|"[^"\n]*")', re.S)
_NUMBERS = {word: index for index, word in enumerate(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
     "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen"))}
_CHINESE = {word: index for index, word in enumerate("零一二三四五六七八九")}
_NUMBER = r"(?:\d+|十[一二三四五六七八九]?|[一二三四五六七八九])"
_CHINESE_MENTION = re.compile(r"(?<![@A-Za-z0-9构])@?(?:参考)?(?:图片|图)\s*(" + _NUMBER + r")(?!\d)")
_ENGLISH_MENTION = re.compile(
    r"(?<![A-Za-z0-9])(?:image|picture|photo|reference(?:\s+image)?|number)\s*(?:no\.?\s*)?#?\s*"
    r"(\d+|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r")(?![A-Za-z0-9])", re.I)
_EXPLICIT = re.compile(r"@图(\d+)(?!\d)")


def _number(text):
    if text.isdecimal():
        return int(text)
    if text.lower() in _NUMBERS:
        return _NUMBERS[text.lower()]
    if text.startswith("十"):
        return 10 + (_CHINESE.get(text[1:], 0) if len(text) > 1 else 0)
    return _CHINESE[text]


def normalize_reference_mentions(script, count):
    """Return effective planner script; keep the user's draft untouched."""
    if not isinstance(script, str) or type(count) is not int or not 0 <= count <= 101:
        raise ValueError("参考图数量或剧本内容无效。")
    if count == 0:
        return script
    parts = _QUOTE.split(script)
    for index in range(0, len(parts), 2):
        parts[index] = _CHINESE_MENTION.sub(lambda match: f"@图{_number(match.group(1))}", parts[index])
        parts[index] = _ENGLISH_MENTION.sub(lambda match: f"@图{_number(match.group(1))}", parts[index])
    result = "".join(parts)
    mentions = [int(value) for value in _EXPLICIT.findall(result)]
    if any(not 1 <= value <= count for value in mentions):
        raise ValueError(f"参考图编号超出范围；当前只有{count}张参考图。")
    if not mentions:
        if count == 1:
            return "@图1\n" + result
        raise ValueError("有多张参考图，请在剧本中用 @ 指定图片。")
    return result
