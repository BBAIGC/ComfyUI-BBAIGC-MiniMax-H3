"""Scoped GPU headroom for Windows WDDM; restore host policy after each shot."""
from contextlib import contextmanager
import math
import os
import threading

_LOCK = threading.RLock()


@contextmanager
def video_memory_budget(requested=0, low_vram=True):
    import comfy.model_management as mm
    import torch
    if isinstance(requested, bool):
        raise ValueError('显存预留应为0～12GB，0为自动。')
    requested = float(requested)
    if not math.isfinite(requested) or not 0 <= requested <= 12:
        raise ValueError('显存预留应为0～12GB，0为自动。')
    with _LOCK:
        original = mm.EXTRA_RESERVED_VRAM
        reserve = requested * 1024**3
        if not reserve and low_vram and os.name == 'nt' and torch.cuda.is_available():
            total = torch.cuda.get_device_properties(torch.cuda.current_device()).total_memory
            if total <= 20 * 1024**3:
                reserve = 3 * 1024**3
        reserve = max(original, reserve)
        try:
            mm.EXTRA_RESERVED_VRAM = reserve
            yield {'reserved_gb': round(reserve / 1024**3, 3), 'automatic': requested == 0}
        finally:
            mm.EXTRA_RESERVED_VRAM = original
