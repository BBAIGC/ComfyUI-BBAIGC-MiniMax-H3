# MiniMax H3 小陈工作台 · 模型与依赖

本页文件大小采用十进制 GB（1 GB = 10⁹ 字节），来自已下载文件或上游 Hugging Face 文件元数据；下载体积不等于显存占用。作者设备为 NVIDIA RTX 5060 Ti 16GB、Intel Core i7-12700KF、64GB 内存，Windows 11。其他设备的速度与最大尺寸取决于驱动、PyTorch/CUDA、分辨率、步数与内存卸载。无需一次下载所有可选路线。

ComfyUI 应使用支持这些模型的版本；作者现有核心为 0.43.0、前端 1.57.4。安装时先核对 `/object_info` 中所需节点，不能只凭版本号判断。RTX 50 系需要支持 Blackwell 的 NVIDIA 驱动及 PyTorch CUDA 构建；先执行 `python -c "import torch; print(torch.__version__,torch.cuda.is_available(),torch.cuda.get_device_name(0))"` 核验。保留现有可工作的 PyTorch，切勿盲目降级。

## 视频最小模型组合

全部来自 [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3)。对应的模型说明见 [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3)。

| 文件 | 大小 | 放置位置 | 用途 |
|---|---:|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 20.970 GB | `models/diffusion_models/` | 基础图生视频/首尾帧路线 |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15.687 GB | `models/text_encoders/` | H3 专用文本编码器 |
| `minimax_h3_video_vae_int8_convrot.safetensors` | 2.811 GB | `models/vae/` | 视频 VAE |
| `minimax_h3_audio_vae_fp32.safetensors` | 0.605 GB | `models/vae/` | 音频 VAE |
| `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors` | 1.956 GB | `models/loras/` | 可选官方 8 步加速 |
| `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors` | 1.956 GB | `models/loras/` | 可选官方 4 步加速 |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 20.970 GB | `models/diffusion_models/` | 可选参考视频路线 |

基础四件套约 40.07 GB，加入一份加速 LoRA 约 42.03 GB。磁盘还需为 Ollama、临时视频、输出及内存卸载保留空间。不要把总磁盘大小当作显存要求。作者自用的 mystic07 RefDelta Turbo8 融合权重约 20.980 GB，当前未核实稳定公开下载地址，不作为开箱默认必需项。公开默认使用基础模型与官方加速 LoRA。

```powershell
$ComfyRoot = '你的ComfyUI路径'
hf download Comfy-Org/MiniMax-H3 diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors vae/minimax_h3_video_vae_int8_convrot.safetensors vae/minimax_h3_audio_vae_fp32.safetensors loras/minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors --local-dir "$ComfyRoot/models"
```

## 一句话分镜与可选生图

Ollama 提供故事规划、分镜与提示词优化；使用 [Ollama](https://ollama.com/download) 并执行 `ollama pull qwen3.5:9b`。模型名称以 `ollama list` 为准。作者的自定义别名不是公共发行名。

自动分镜生图使用 Qwen Image 2.1：`qwen-image-2.1-UC-Q6_K.gguf` 5.877 GB（[作者来源](https://huggingface.co/KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF)）、`qwen3vl_8b_int8_convrot.safetensors` 9.351 GB、`qwen_image_2.1_vae_bf16.safetensors` 0.676 GB（后两者：[Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)）。位置依次是 `models/diffusion_models`、`models/text_encoders`、`models/vae`。也可在节点中选择官方基础 INT8 7.257 GB，保持模型与编码器/VAE配套。已有参考图且不启用本地分镜生图时，无需重复下载图片模型。

## 插件与可选加速

- 共享插件：[BBAIGC/ComfyUI-BBAIGC-MiniMax-H3](https://github.com/BBAIGC/ComfyUI-BBAIGC-MiniMax-H3)。图片与视频工作台只装一份。
- GGUF：[city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF)，只在使用 GGUF 图片模型时安装。
- 分块前馈低显存节点 `MiniMaxChunkFeedForward` 来自 [kijai/ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes)。若已有同名节点，不要再注册第二份。
- 可选稀疏注意力 `H3SLAAttention`：[ethanfel/ComfyUI-PlagueKind-Nodes-only-sparse](https://github.com/ethanfel/ComfyUI-PlagueKind-Nodes-only-sparse)，需要按上游说明匹配硬件与编译环境；基础部署不强制开启。
- 可选参考视频预处理：[ostris/ai-toolkit](https://github.com/ostris/ai-toolkit) 的 H3 对齐方式，本地配套 `ComfyUI-AIToolkit-MiniMaxH3` 保持24fps与模型帧网格。不是普通图生视频的必装依赖。
- 可选本地配音：[flybirdxx/ComfyUI-Qwen-TTS](https://github.com/flybirdxx/ComfyUI-Qwen-TTS) 与 [Qwen/Qwen3-TTS](https://huggingface.co/Qwen) 的 CustomVoice/Tokenizer。Windows 系统音色可先使用，无需为基础视频生成安装整套 TTS。
- 音视频封装按 ComfyUI 自身依赖安装 PyAV/FFmpeg；不要用不匹配的系统 Python 安装到另一环境。

NVFP4 编码器为本机 Blackwell 环境的选型；其他 NVIDIA 架构应核对 ComfyUI 与权重支持，必要时改选同仓库兼容编码器，不保证所有显卡通用。

## 性能参考

**以下是作者提供的暂估，尚未按固定参数实测：加速方案生成约 1 分钟视频，大约 30 多分钟；未加速方案生成约 15 秒视频，大约 13 分钟。** 两项不是同一参数下的对照测试，不能按比例外推。最终耗时还取决于分镜数、分辨率、步数、参考图和音轨；后续以固定参数实测替换。

共享语音输入节点按需使用 SenseVoice。已有权重可设置 `BBAIGC_SENSEVOICE_MODEL_DIR`；兼容旧变量 `CODEX_SENSEVOICE_MODEL_DIR`，新变量优先。目录应包含 `model.onnx`、`config.yaml`、`tokens.json`、`am.mvn`，模型来源为 [kautism/SenseVoiceSmall-onnx](https://huggingface.co/kautism/SenseVoiceSmall-onnx)。

## 选择作者同款或适配显存的量化版本

先选择：**作者同款**、**适配本机显存**、**高配更高精度**，或同时保留同款与一个适配版本。安装智能体先检查硬件、现有模型和磁盘，再一次性确认尚未指定的方案；不要默认下载全部档位。

作者图片主模型是社区 UC（Uncensored）Q6_K，配套 Qwen 编码器 INT8 与 2.1 VAE。UC 是社区权重变体名称，与本工作台功能无关；原模型和变体各自的许可仍适用。下表显存档位是选择起点，**不是最低显存保证或逐档实测**；分辨率、批量、编码器精度和 CPU/内存卸载都会改变占用，64GB 内存也不能替代足够磁盘。

| UC GGUF 主模型 | 文件大小 | 可优先评估的设备与取舍 |
|---|---:|---|
| `qwen-image-2.1-UC-Q4_0.gguf` | 4.152 GB | 8–12GB 显存先评估；体积最小，量化损失可能更明显，需卸载与小尺寸验证 |
| `qwen-image-2.1-UC-Q4_K_M.gguf` | 4.605 GB | 8–12GB 显存的另一低体积起点；仍需配套编码器与卸载 |
| `qwen-image-2.1-UC-Q5_K_M.gguf` | 5.221 GB | 12–16GB 显存可评估的体积/精度折中 |
| `qwen-image-2.1-UC-Q6_K.gguf` | 5.877 GB | 作者 5060 Ti 16GB 同款；保留已有已验证配置的优先选择 |
| `qwen-image-2.1-UC-Q8_0.gguf` | 7.592 GB | 24GB 及以上可评估；更大不代表固定更快 |
| `qwen-image-2.1-UC-BF16.gguf` | 14.230 GB | 高显存/充足内存设备可评估；近完整精度，磁盘与内存代价最高 |

以上精确文件来自 [KasugaiSakura UC GGUF 仓库](https://huggingface.co/KasugaiSakura/Qwen-Image-2.1-Uncensored-Abenzerps-GGUF/tree/main)，放入 `models/diffusion_models/`，用 GGUF 加载节点选择实际文件名。不要把 MLX 文件用于 ComfyUI 的 PyTorch/GGUF 路线；该仓库的 NVFP4/FP8 等是不同格式，不能仅修改扩展名替代 GGUF。

Qwen 2.1 官方编码器还有 [W4A8 6.312 GB、INT8 9.351 GB、BF16 17.534 GB](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/main/text_encoders)。主模型量化与编码器量化是两个选择；作者用 INT8，低显存可在当前核心确认兼容后评估 W4A8，高配可评估 BF16。统一放入 `models/text_encoders/`，不要误用 PE 专用编码器代替本路线的 `qwen3vl_8b_*`。

| 其他模型的同架构档位 | 大小 | 选择建议 |
|---|---:|---|
| Qwen 2.1 官方基础/ Turbo INT8 ConvRot | 各 7.257 GB | 当前公开 Turbo 默认；配套编码器/VAE 不变 |
| Qwen 2.1 官方基础/ Turbo BF16 | 各 14.230 GB | 高显存设备可评估；不能把基础模型和 Turbo 采样参数混用 |
| Krea 2 Turbo NVFP4 | 7.674 GB | 对应 GPU 与当前 ComfyUI 必须支持该格式；RTX 50 系可作为待验证选择，未逐档实测 |
| Krea 2 Turbo FP8 | 13.142 GB | 本工作台公开默认，较 BF16 节省体积 |
| Krea 2 Turbo BF16 | 26.283 GB | 高配更高精度选择；非低显存默认 |

Krea 的编码器对应 [FP8 5.242 GB、INT8 ConvRot 4.859 GB、BF16 8.876 GB](https://huggingface.co/Comfy-Org/Krea-2/tree/main/text_encoders)，VAE 为同仓库 `qwen_image_vae.safetensors`。默认 FP8 配套组合已写在前面的模型表；更换量化后按真实文件名选择，不能与 Qwen 2.1 的 VAE/编码器混用。

这些变体文件与大小于 2026-10-30 从上游文件元数据核对；不同档位的生成质量、峰值显存和速度仍需安装者在自己的配置下验证。

## H3 视频量化与完整自动分镜下载范围

作者 H3 基础路线采用 `minimax_h3_fl2va_pruned_int8_convrot.safetensors`（20.970 GB），编码器为 H3 专用 NVFP4 AWQ（15.687 GB），配套视频 INT8 VAE 与音频 FP32 VAE。16GB 显存依靠模型卸载/分块工作，文件大小并不表示它们能同时驻留显存。

| H3 FL2VA 主模型档位 | 大小 | 适用选择 |
|---|---:|---|
| `minimax_h3_fl2va_pruned_w6a8.safetensors` | 15.984 GB | 较低体积的候选；先核验当前核心支持，低显存/低内存不保证可运行 |
| `minimax_h3_fl2va_pruned_fp8_scaled.safetensors` | 20.958 GB | 官方较低精度选择，与 INT8 体积接近 |
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 20.970 GB | 作者公开可复现基础同款 |
| `minimax_h3_fl2va_pruned_bf16.safetensors` | 40.226 GB | 高显存和充足内存/磁盘设备可评估 |
| `minimax_h3_fl2va_bf16.safetensors` | 66.280 GB | 完整大体积版本；非工作台默认，不应盲目下载 |

H3 专用编码器另有 INT8 ConvRot 27.141 GB 与 BF16 51.506 GB；高配不一定需要下载最高档。来源均为 [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main)。不要把参考视频 Ref2VA 与本 FL2VA 工作流互换，也不要把图片用 Qwen 编码器当作 H3 编码器。未逐档推理验证，安装者应保持模型架构、VAE 与加速 LoRA 配套。

**`install.py --download-models h3` 只下载视频基础四件套与官方 8 步 LoRA，约 42.03 GB。** 它不包括 Ollama 规划模型、图片生成模型与语音模型。要实现“一句话 → AI 分镜图 → 视频”，还需安装前文 Qwen 图片主模型/编码器/VAE 与 Ollama；作者 UC Q6_K 图片组合另约 **15.903 GB**，官方 Turbo 图片组合另约 **17.283 GB**。只用手动参考图片生成视频时，可以跳过图片生成模型。语音通过 `--with-speech` 额外下载约 0.940 GB，独立 CPU 识别。
