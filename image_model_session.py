"""One execution-owned Qwen weight set, never a global model or image cache."""
from __future__ import annotations

import gc
import json


class ImageModelSession:
    """Reuse only raw loaders within a bounded storyboard-image stage.

    The caller must close this scope before video/restoration work and in its
    outer finally block. Per-image conditioning, pixels and cache wrappers stay
    with generate_reference; ComfyUI still owns GPU placement and offloading.
    """

    def __init__(self):
        self._key = None
        self._weights = None
        self._closed = False

    def acquire(self, settings, loader):
        if self._closed:
            raise RuntimeError("分镜图片模型会话已结束。")
        key = json.dumps({name: settings[name] for name in
                          ("model", "clip", "vae", "loras", "clip_device")},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if self._key is not None and self._key != key:
            raise ValueError("分镜图片模型配置已变化，请使用新的图片模型会话。")
        if self._weights is None:
            weights = loader()
            if not isinstance(weights, dict) or set(weights) != {"model", "clip", "vae"}:
                raise RuntimeError("分镜图片模型加载结果无效。")
            self._weights = dict(weights)
            self._key = key
        # The per-image QwenImage21Cache clone must never replace this raw set.
        return dict(self._weights)

    def finish_image(self):
        if self._weights is not None:
            diffusion = getattr(getattr(self._weights["model"], "model", None), "diffusion_model", None)
            reset = getattr(diffusion, "reset_prefix_cache", None)
            if callable(reset):
                reset(False)
        gc.collect()
        import comfy.model_management as mm
        mm.soft_empty_cache()

    def close(self):
        if self._closed:
            return
        try:
            self.finish_image()
        finally:
            self._weights = None
            self._key = None
            self._closed = True
            from .resource_control import release_resources
            release_resources(full=False)

    def __enter__(self):
        if self._closed:
            raise RuntimeError("分镜图片模型会话已结束。")
        return self

    def __exit__(self, *_):
        self.close()
