"""Report actual H3 phases and scoped native sampler steps without owning work."""
from contextlib import contextmanager
import math
import threading


def notify_video_phase(bundle, phase, **values):
    callback = bundle.get('_progress_callback')
    if callable(callback):
        callback({'phase': phase, **values})


@contextmanager
def sampling_video_progress(bundle, phase, steps, **values):
    callback = bundle.get('_progress_callback')
    if not callable(callback):
        yield
        return
    import comfy.utils as utils
    notify_video_phase(bundle, phase, value=0, total=steps, percent=0.0, **values)
    original = utils.PROGRESS_BAR_HOOK
    owner = threading.get_ident()
    previous = None

    def proxy(value, total, *args, **kwargs):
        nonlocal previous
        if original is not None:
            original(value, total, *args, **kwargs)
        if threading.get_ident() != owner:
            return
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and isinstance(total, (int, float)) and not isinstance(total, bool)
                and math.isfinite(value) and math.isfinite(total) and total > 0
                and 0 <= value <= total and previous != (value, total)):
            previous = (value, total)
            notify_video_phase(bundle, phase, value=value, total=total,
                               percent=value / total * 100, **values)

    utils.PROGRESS_BAR_HOOK = proxy
    try:
        yield
    finally:
        if utils.PROGRESS_BAR_HOOK is proxy:
            utils.PROGRESS_BAR_HOOK = original
