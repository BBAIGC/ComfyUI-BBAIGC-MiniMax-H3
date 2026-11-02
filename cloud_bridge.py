"""H3 adapters using the user's existing encrypted BBAPI profiles."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import nodes


def backend():
    cls = nodes.NODE_CLASS_MAPPINGS.get("BBAPIConnection")
    module = sys.modules.get(getattr(cls, "__module__", ""))
    if module is None or not callable(getattr(module, "make_connection", None)):
        raise RuntimeError("请先安装并启用BBAPI私有节点，再保存本机API配置。")
    return module


def connection(settings):
    return backend().make_connection(settings.get("profile", "bbapi-main"),
                                     settings.get("base_url", ""),
                                     settings.get("timeout", 300))


def validate_cloud(settings, kind="image"):
    current = connection(settings)
    model = str(settings.get("model") or current.default_model or "").strip()
    if not model:
        raise ValueError(f"请在{('文本' if kind == 'text' else '图像')}API设置中选择该密钥分组可用的模型。")
    return current, model


def _response_text(data):
    if data.get("error"):
        raise ValueError(str(data["error"]))
    if data.get("status") not in (None, "completed"):
        raise ValueError("文本API未完整生成结果，请检查输出上限或上游状态。")
    return "".join(part.get("text", "") for item in data.get("output", [])
                   for part in item.get("content", []) if part.get("type") == "output_text")


def _text_payload(raw, protocol):
    """Only a completed response can become a saved storyboard plan."""
    if raw.lstrip().startswith(b"{"):
        data = json.loads(raw)
        if protocol == "responses":
            return _response_text(data)
        choice = data["choices"][0]
        if choice.get("finish_reason") in ("length", "content_filter"):
            raise ValueError("文本API输出未完成，请检查输出上限或内容限制。")
        return choice["message"]["content"]
    completed = None
    for line in raw.decode("utf-8").splitlines():
        if not line.startswith("data:") or line[5:].strip() in ("", "[DONE]"):
            continue
        event = json.loads(line[5:].strip())
        if event.get("type") in ("error", "response.failed", "response.incomplete"):
            raise ValueError(str(event.get("error") or event.get("response", {}).get("error") or "文本API输出未完成。"))
        if event.get("type") == "response.completed":
            completed = event.get("response")
    if not isinstance(completed, dict):
        raise ValueError("文本API流已中断，未收到完成事件；没有保存不完整分镜。")
    return _response_text(completed)


def _response_messages(messages):
    result, instructions = [], []
    for message in messages:
        content = message.get("content", "")
        if message.get("role") in ("system", "developer") and isinstance(content, str):
            instructions.append(content)
            continue
        if isinstance(content, list):
            content = [{"type": "input_text", "text": part.get("text", "")} if part.get("type") == "text" else
                       {"type": "input_image", "image_url": part.get("image_url", {}).get("url", "")}
                       for part in content if part.get("type") in ("text", "image_url")]
        result.append({"role": message.get("role", "user"), "content": content})
    return result, "\n\n".join(instructions)


def chat_json(messages, settings):
    """One bounded POST; authentication and timeout errors never fall back silently."""
    import requests
    module = backend()
    client = sys.modules[module.ImageClient.__module__]
    current, model = validate_cloud(settings, "text")
    protocol = settings.get("protocol", "auto")
    if protocol == "auto":
        protocol = "responses" if re.match(r"^(?:gpt-[56]|.*codex)", model, re.I) else "chat"
    if protocol not in ("responses", "chat"):
        raise ValueError("请选择有效的文本API协议。")
    payload = {"model": model, "messages": messages,
               "temperature": float(settings.get("temperature", .1)),
               "top_p": float(settings.get("top_p", .9)),
               "max_tokens": int(settings.get("num_predict", 10000)),
               "response_format": {"type": "json_object"}, "stream": False}
    if re.match(r"^(?:gpt-5|o[134](?:-|$))", model, re.I):
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        payload.pop("temperature", None)
        payload.pop("top_p", None)
    endpoint = "/v1/chat/completions"
    if protocol == "responses":
        inputs, instructions = _response_messages(messages)
        payload = {"model": model, "input": inputs, "instructions": instructions,
                   "max_output_tokens": int(settings.get("num_predict", 10000)),
                   "store": False, "stream": True, "text": {"format": {"type": "json_object"}}}
        endpoint = "/v1/responses"
    started = time.monotonic()
    try:
        with requests.Session() as session:
            with session.post(current.base_url + endpoint, json=payload,
                              headers={"Authorization": "Bearer " + current.api_key},
                              timeout=(10, current.timeout), stream=True, allow_redirects=False) as response:
                raw = client.bounded_read(response, 4 * 1024 * 1024, started + current.timeout)
                if response.status_code != 200:
                    if response.status_code in (502, 503, 504) and b"<html" in raw[:1024].lower():
                        ray = response.headers.get("cf-ray", "")
                        ray = re.sub(r"[^a-zA-Z0-9-]", "", ray)[:80] if isinstance(ray, str) else ""
                        suffix = f"；网关编号：{ray}" if ray else ""
                        raise RuntimeError(f"文本API网关暂不可用（HTTP {response.status_code}），返回了网页错误而非模型结果；本次未自动重试" + suffix)
                    raise RuntimeError(f"文本API请求失败（HTTP {response.status_code}）：" +
                                       client.safe_message(client._error_payload(raw, "请检查分组、模型与额度。"), current.api_key))
        content = _text_payload(raw, protocol)
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", str(content)).strip()
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("文本API返回的分镜应为JSON对象。")
        return result
    except Exception as error:
        raise RuntimeError(client.safe_message(str(error), current.api_key)) from None


def generate_image(prompt, paths, width, height, settings, progress_callback=None):
    """Use the same edit/generation transport as the standalone BBAPI workbench."""
    from PIL import Image
    import comfy.model_management as mm
    import uuid
    import folder_paths
    module = backend()
    client = sys.modules[module.ImageClient.__module__]
    current, model = validate_cloud(settings)
    references = []
    total = 0
    for path in paths:
        with Image.open(path) as source:
            encoded = client.encode_reference(source)
        total += len(encoded)
        if total > client.MAX_INPUT_BYTES:
            raise ValueError("参考图片合计超过64MB，请缩小图片或减少数量。")
        references.append(encoded)
    client.validate_references(model, references, settings.get("resolution", "1K"))
    prompt = re.sub(r"<image(\d+)>", lambda match: "参考图" + match[1], prompt, flags=re.I)
    if progress_callback:
        progress_callback({"phase": "sampling"})
    result = module.ImageClient(current).generate_one(
        0, model, prompt, settings.get("resolution", "1K"), f"{width}:{height}",
        settings.get("quality", "low"), references, mm.processing_interrupted, None)
    if result.image is None:
        raise RuntimeError(result.error or "图像API未返回图片；本次未自动重提。")
    directory = Path(folder_paths.get_output_directory()) / "H3分镜" / "分镜参考图"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"API分镜_{uuid.uuid4().hex[:16]}.png"
    try:
        result.image.save(output)
    finally:
        result.image.close()
    # Keep a completed paid image on disk, but a cancelled execution must not
    # publish it as this job's successful result or start the next shot.
    mm.throw_exception_if_processing_interrupted()
    if progress_callback:
        progress_callback({"phase": "completed"})
    return str(output.resolve())
