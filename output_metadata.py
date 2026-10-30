"""Execution-local ComfyUI metadata for newly generated H3 assets."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4


_METADATA = ContextVar("h3_output_metadata", default=None)


def _disabled():
    try:
        from comfy.cli_args import args
        return bool(getattr(args, "disable_metadata", False))
    except ImportError:
        return False


def _json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _prompt_snapshot(prompt):
    # ComfyUI mutates its execution prompt with per-node cache receipts. Those
    # may contain NaN for IS_CHANGED nodes and are not graph input parameters.
    if isinstance(prompt, dict):
        prompt = {
            identity: {key: value for key, value in node.items() if key != "is_changed"}
            if isinstance(node, dict) else node
            for identity, node in prompt.items()
        }
    return _json_copy(prompt)


@contextmanager
def execution_metadata(prompt=None, extra_pnginfo=None):
    """Scope a JSON snapshot; omitted values inherit an outer execution.

    Internal shot helpers omit hidden inputs, so they preserve their parent
    context. No metadata is reconstructed from saved workflow files or assets.
    """
    metadata = None
    if not _disabled():
        metadata = _json_copy(_METADATA.get()) or {}
        if isinstance(extra_pnginfo, dict):
            for key, value in extra_pnginfo.items():
                if not isinstance(key, str):
                    continue
                try:
                    metadata[key] = _json_copy(value)
                except (TypeError, ValueError, OverflowError):
                    metadata.pop(key, None)
        if prompt is not None:
            try:
                metadata["prompt"] = _prompt_snapshot(prompt)
            except (TypeError, ValueError, OverflowError):
                metadata.pop("prompt", None)
        metadata = metadata or None
    token = _METADATA.set(metadata)
    try:
        yield
    finally:
        _METADATA.reset(token)


def current_metadata():
    """Return a detached snapshot, or None when absent/disabled."""
    if _disabled():
        return None
    return _json_copy(_METADATA.get())


def save_png(image, destination):
    """Save pixels unchanged, adding standard PNG text metadata if present."""
    metadata = current_metadata()
    if metadata:
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        for key, value in metadata.items():
            info.add_text(key, json.dumps(value, ensure_ascii=False, allow_nan=False))
        image.save(destination, format="PNG", pnginfo=info)
    else:
        image.save(destination, format="PNG")
    return str(destination)


def _graph_workflow(workflow):
    if not isinstance(workflow, dict):
        return False
    nodes, links = workflow.get("nodes"), workflow.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        return False
    ids = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("type"), str) or not node["type"]:
            return False
        identity = node.get("id")
        if isinstance(identity, bool) or not isinstance(identity, (int, str)) or identity in ids:
            return False
        ids.add(identity)
    return all(isinstance(link, (list, dict)) for link in links)


def _same_workflow(destination, workflow):
    try:
        return json.loads(destination.read_text(encoding="utf-8")) == workflow
    except (OSError, ValueError):
        return False


def save_workflow_snapshot(directory, identity):
    """Atomically publish an importable graph without replacing other payloads."""
    metadata = current_metadata()
    workflow = metadata.get("workflow") if metadata else None
    if not _graph_workflow(workflow):
        return None

    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(identity or "run")).strip("_")[:96] or "run"
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / ("工作流_" + safe + ".json")
    if _same_workflow(destination, workflow):
        return str(destination)
    payload = json.dumps(workflow, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=root, prefix=".h3-workflow-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        while True:
            try:
                # A hard-link publishes the complete file and cannot overwrite
                # an existing name, including when another execution races us.
                os.link(temporary, destination)
                return str(destination)
            except FileExistsError:
                if _same_workflow(destination, workflow):
                    return str(destination)
                destination = root / ("工作流_" + safe + "_" + uuid4().hex[:12] + ".json")
            except OSError:
                if os.name != "nt":
                    raise
                # Windows rename is atomic and refuses an existing destination;
                # this also supports filesystems that cannot create hard links.
                try:
                    os.rename(temporary, destination)
                    temporary = None
                    return str(destination)
                except FileExistsError:
                    if _same_workflow(destination, workflow):
                        return str(destination)
                    destination = root / ("工作流_" + safe + "_" + uuid4().hex[:12] + ".json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
