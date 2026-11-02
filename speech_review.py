"""Conservative CPU speech comparison; never changes or replaces model audio.

An ASR match is only a text comparison, not a guarantee of sound quality.
Uncertain/mismatching recognition stays available for human review. The existing
SenseVoice engine retains at most its one bounded CPU session; this module does
not unload it out from under microphone recognition or download any weights.
"""
from __future__ import annotations

import io
import math
import time
import unicodedata
import wave

import numpy as np

MAX_AUDIO_SECONDS = 15.5
ASR_SAMPLE_RATE = 16000


def _comparison_text(value):
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(char for char in normalized if unicodedata.category(char)[0] in "LNM")


def compare_recognized_speech(recognized, expected_texts):
    """Keep exact words; tolerate punctuation/case, never extra spoken prefixes."""
    expected = [expected_texts] if isinstance(expected_texts, str) else list(expected_texts)
    if any(not isinstance(value, str) for value in expected):
        raise ValueError("待复核对白必须是文本。")
    if not isinstance(recognized, str):
        raise ValueError("语音识别结果必须是文本。")
    expected_key = _comparison_text("".join(expected))
    recognized_key = _comparison_text(recognized)
    matches = expected_key == recognized_key
    reason = ("recognized_text_matches" if matches else
              "unexpected_speech" if not expected_key else
              "speech_not_recognized" if not recognized_key else "recognized_text_differs")
    return {"status": "matched" if matches else "needs_review", "expected": expected,
            "recognized": recognized, "reason": reason,
            "message": "对白识别一致" if matches else "对白待复核"}


def summarize_reviews(scenes):
    """Summarize existing video receipts without treating untested media as OK."""
    needs_review, unavailable, checked = [], [], 0
    for position, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or not (scene.get("video") or scene.get("video_path") or
                                               scene.get("status") in ("完成", "completed")):
            continue
        receipt = scene.get("speech_review")
        if not isinstance(receipt, dict):
            report = scene.get("report")
            receipt = report.get("speech_review") if isinstance(report, dict) else None
        if not isinstance(receipt, dict) or receipt.get("status") not in ("matched", "needs_review", "unavailable"):
            continue
        checked += 1
        try:
            index = int(scene.get("index", position))
        except (TypeError, ValueError):
            index = position
        if receipt["status"] == "needs_review":
            needs_review.append(index)
        elif receipt["status"] == "unavailable":
            unavailable.append(index)
    if not checked:
        return None
    return {"status": "needs_review" if needs_review else "unavailable" if unavailable else "matched",
            "needs_review": needs_review, "unavailable": unavailable}


def _wav_for_recognition(audio):
    if not isinstance(audio, dict):
        raise ValueError("视频音频数据无效。")
    sample_rate = audio.get("sample_rate")
    if (not isinstance(sample_rate, (int, float)) or isinstance(sample_rate, bool) or
            not math.isfinite(sample_rate) or not 8000 <= sample_rate <= 192000 or
            int(sample_rate) != sample_rate):
        raise ValueError("视频音频采样率无效。")
    sample_rate = int(sample_rate)
    waveform = audio.get("waveform")
    shape = getattr(waveform, "shape", ())
    if len(shape) == 3:
        if shape[0] != 1:
            raise ValueError("对白复核每次只接受一个镜头。")
        shape = shape[1:]
    elif len(shape) != 2:
        raise ValueError("视频音频需要单镜头的声道与采样数据。")
    channels, samples = shape
    if channels not in (1, 2) or not sample_rate // 10 <= samples <= math.ceil(MAX_AUDIO_SECONDS * sample_rate):
        raise ValueError("对白复核只接受0.1至15.5秒的单声道或双声道音频。")
    # Validate the shape before any potentially expensive CPU copy.
    if hasattr(waveform, "detach"):
        import torch
        waveform = waveform.detach().to(device="cpu", dtype=torch.float32).numpy()
    values = np.asarray(waveform, dtype=np.float32).reshape(channels, samples)
    if not np.isfinite(values).all():
        raise ValueError("视频音频包含无效采样值。")
    mono = values.mean(axis=0, dtype=np.float32)
    if sample_rate != ASR_SAMPLE_RATE:
        # PyAV is already part of ComfyUI; use its proper anti-alias resampler
        # rather than dropping samples or adding another audio dependency.
        import av
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(mono[None, :]),
                                           format="fltp", layout="mono")
        frame.sample_rate = sample_rate
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=ASR_SAMPLE_RATE)
        chunks = [value.to_ndarray().reshape(-1) for value in resampler.resample(frame)]
        chunks.extend(value.to_ndarray().reshape(-1) for value in resampler.resample(None))
        mono = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
    pcm = np.rint(np.clip(mono, -1, 1) * 32767).astype("<i2")
    destination = io.BytesIO()
    with wave.open(destination, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(ASR_SAMPLE_RATE)
        writer.writeframes(pcm.tobytes())
    return destination.getvalue(), samples / sample_rate


def review_model_audio(audio, expected_texts):
    """Recognize one native model soundtrack on CPU, preserving its source.

    Callers skip explicit silence and user-provided authoritative dialogue
    tracks. A missing model or recognition failure is ``unavailable``, never a
    successful check. This function does not sample, trim, mute or retry video.
    """
    started = time.perf_counter()
    expected = [expected_texts] if isinstance(expected_texts, str) else list(expected_texts)
    result = {"status": "unavailable", "expected": expected, "recognized": "",
              "reason": "recognition_unavailable", "message": "对白未复核",
              "engine": "SenseVoice Small", "duration_seconds": None}
    try:
        if len(expected) > 100 or sum(len(value) for value in expected) > 10000:
            raise ValueError("待复核对白超过单镜头范围。")
        compare_recognized_speech("", expected)
        wav_bytes, duration = _wav_for_recognition(audio)
        result["duration_seconds"] = round(duration, 4)
        from .speech_engine import transcribe
        recognition = transcribe(wav_bytes, language="auto", normalize=False, threads=4)
        result.update(compare_recognized_speech(recognition["text"], expected))
    except Exception as exc:
        # A check failure must not discard a valid original video or be
        # misrepresented as a successful speech/quality verification.
        result["error"] = str(exc)[:500]
    result["elapsed_seconds"] = round(time.perf_counter() - started, 4)
    return result
