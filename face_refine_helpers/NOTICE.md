# 来源

`track.py`、`stitch.py`、`inject.py` 来自 AIMixer/ComfyUI_MiniMaxH3_Director，commit `4c6245e518dd8a494f3afc4f3cccd53f22d82d10`，Apache-2.0（LICENSE-AIMixer）。上游注明这些实现改编自 Carasibana/ComfyUI-H3-FaceRefine，MIT（LICENSE-Carasibana）。

本工程调整：画布对齐在本模块完成；检测器最多缓存一个且运行结束释放；逐帧检测和贴回跟随CPU源帧，避免与H3争用显存；检测支持取消；粘贴数值允许显式零值。采样由本工程 `video_postprocess.py` 连接现有 FL2VA 模型，未复制上游导演台、模型或工作流。
