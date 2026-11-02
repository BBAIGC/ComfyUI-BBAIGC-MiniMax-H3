"""Provider routing, private-profile transport and optional storyboard contracts."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class ModelServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        package = types.ModuleType("model_service_fixture")
        package.__path__ = [str(ROOT)]
        folders = types.ModuleType("folder_paths")
        folders.get_output_directory = folders.get_input_directory = lambda: self.directory.name
        folders.get_filename_list = Mock(return_value=[])
        nodes = types.ModuleType("nodes")
        self.backend = types.ModuleType("service_fake_backend")
        connection_class = type("BBAPIConnection", (), {"__module__": self.backend.__name__})
        nodes.NODE_CLASS_MAPPINGS = {"BBAPIConnection": connection_class}
        self.connection = types.SimpleNamespace(base_url="https://example.test", api_key="secret-value",
                                                default_model="image-model", timeout=30)
        self.backend.make_connection = Mock(return_value=self.connection)
        self.client = types.ModuleType("service_fake_client")
        self.client.bounded_read = lambda response, limit, deadline: response.body
        self.client._error_payload = lambda raw, default: json.loads(raw).get("error", default)
        self.client.safe_message = lambda message, secret="": str(message).replace(secret, "[已隐藏]") if secret else str(message)
        self.backend.ImageClient = type("ImageClient", (), {"__module__": self.client.__name__})
        resources = types.ModuleType("model_service_fixture.resource_control")
        resources.release_resources = Mock()
        self.patch = patch.dict(sys.modules, {package.__name__: package, "folder_paths": folders,
            "nodes": nodes, self.backend.__name__: self.backend, self.client.__name__: self.client,
            resources.__name__: resources})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.folders = folders

    def load(self, name):
        fullname = "model_service_fixture." + name
        spec = importlib.util.spec_from_file_location(fullname, ROOT / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        self.addCleanup(sys.modules.pop, fullname, None)
        spec.loader.exec_module(module)
        return module

    def session(self, response):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        session.post.return_value = response
        return session

    def test_chat_preserves_multimodal_messages_and_returns_json(self):
        module = self.load("cloud_bridge")
        response = Mock(status_code=200, body=json.dumps({"choices": [{"message": {
            "content": '```json\n{"scenes":[{"dialogue":"下午好，请喝水。"}]}\n```'}}]}).encode())
        session = self.session(response)
        messages = [{"role": "user", "content": [{"type": "text", "text": "规划镜头"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}]}]
        with patch("requests.Session", return_value=session):
            result = module.chat_json(messages, {"profile": "text-profile", "model": "gpt-test"})
        self.assertEqual(result["scenes"][0]["dialogue"], "下午好，请喝水。")
        self.assertEqual(session.post.call_count, 1)
        request = session.post.call_args.kwargs
        self.assertEqual(request["json"]["messages"], messages)
        self.assertNotIn("api_key", request["json"])
        self.assertFalse(request["allow_redirects"])

    def test_http_error_is_redacted_without_a_second_paid_request(self):
        module = self.load("cloud_bridge")
        session = self.session(Mock(status_code=401, body=b'{"error":"secret-value expired"}'))
        with patch("requests.Session", return_value=session), self.assertRaises(RuntimeError) as failure:
            module.chat_json([], {"model": "gpt-test"})
        self.assertNotIn("secret-value", str(failure.exception))
        self.assertEqual(session.post.call_count, 1)

    def test_responses_stream_requires_completion_and_preserves_visual_input(self):
        module = self.load("cloud_bridge")
        event = {"type": "response.completed", "response": {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"scenes":[1,2]}'}]}]}}
        raw = ('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode()
        session = self.session(Mock(status_code=200, body=raw))
        messages = [{"role": "system", "content": "只返回JSON。"}, {"role": "user", "content": [
            {"type": "text", "text": "两个镜头"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}]}]
        with patch("requests.Session", return_value=session):
            result = module.chat_json(messages, {"model": "gpt-5.4", "protocol": "responses"})
        self.assertEqual(result, {"scenes": [1, 2]})
        payload = session.post.call_args.kwargs["json"]
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
        with self.assertRaisesRegex(ValueError, "完成事件"):
            module._text_payload(b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n', "responses")

    def test_html_gateway_error_is_identified_without_blame_or_retry(self):
        module = self.load("cloud_bridge")
        session = self.session(Mock(status_code=502, body=b'<!DOCTYPE html><html><title>502: Bad gateway</title></html>',
                                    headers={"cf-ray": "a5430f3b0f49b8b6-SJC"}))
        with patch("requests.Session", return_value=session), self.assertRaises(RuntimeError) as failure:
            module.chat_json([], {"model": "gpt-6-luna", "protocol": "responses"})
        self.assertIn("网关暂不可用", str(failure.exception))
        self.assertIn("a5430f3b0f49b8b6-SJC", str(failure.exception))
        self.assertEqual(session.post.call_count, 1)

    def test_cloud_images_reject_local_lora_parameters(self):
        bridge = self.load("image_bridge")
        with self.assertRaisesRegex(ValueError, "本地采样或LoRA"):
            bridge.normalize_settings({"provider": "bbapi", "loras": []})
        result = bridge.validate_image_backend({"provider": "bbapi", "model": "image-model"})
        self.assertEqual(result["encoder"], "API")

    def test_image_adapter_uses_the_edit_transport_and_saves_the_real_result(self):
        from PIL import Image
        module = self.load("cloud_bridge")
        path = Path(self.directory.name) / "reference.png"
        Image.new("RGB", (640, 480), "steelblue").save(path)
        self.client.encode_reference = lambda image: b"original-reference"
        self.client.MAX_INPUT_BYTES = 64 * 1024 * 1024
        self.client.validate_references = Mock()
        generate = Mock(return_value=types.SimpleNamespace(image=Image.new("RGB", (1024, 768), "tan")))
        self.backend.ImageClient = type("ImageClient", (), {"__module__": self.client.__name__,
            "__init__": lambda self, connection: None, "generate_one": staticmethod(generate)})
        comfy = types.ModuleType("comfy")
        comfy.__path__ = []
        management = types.ModuleType("comfy.model_management")
        management.processing_interrupted = lambda: False
        management.throw_exception_if_processing_interrupted = Mock()
        with patch.dict(sys.modules, {"comfy": comfy, "comfy.model_management": management}):
            output = module.generate_image("<image1>中的茶杯放在桌上。", [path], 1024, 768,
                {"model": "image-model", "resolution": "1K", "quality": "low"})
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(generate.call_args.args[2], "参考图1中的茶杯放在桌上。")
        self.assertEqual(generate.call_args.args[6], [b"original-reference"])
        with Image.open(output) as result:
            self.assertEqual(result.size, (1024, 768))

    def test_cancelled_cloud_result_is_saved_but_never_returned_as_success(self):
        from PIL import Image
        module = self.load("cloud_bridge")
        self.client.MAX_INPUT_BYTES = 64 * 1024 * 1024
        self.client.validate_references = Mock()
        generate = Mock(return_value=types.SimpleNamespace(image=Image.new("RGB", (1024, 768), "tan")))
        self.backend.ImageClient = type("ImageClient", (), {"__module__": self.client.__name__,
            "__init__": lambda self, connection: None, "generate_one": staticmethod(generate)})
        comfy = types.ModuleType("comfy")
        comfy.__path__ = []
        management = types.ModuleType("comfy.model_management")
        management.processing_interrupted = lambda: True
        management.throw_exception_if_processing_interrupted = Mock(side_effect=InterruptedError("cancelled"))
        with patch.dict(sys.modules, {"comfy": comfy, "comfy.model_management": management}), self.assertRaises(InterruptedError):
            module.generate_image("女孩在窗边端茶。", [], 1024, 768, {"model": "image-model"})
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(len(list(Path(self.directory.name).rglob("API分镜_*.png"))), 1)

    def test_disabled_image_lora_does_not_inspect_a_missing_file(self):
        bridge = self.load("image_bridge")
        self.assertEqual(bridge.image_loras([{"enabled": False, "name": "missing.safetensors"}]), [])
        self.folders.get_filename_list.assert_not_called()

    def test_text_storyboard_can_generate_without_external_references(self):
        self.load("image_bridge")
        studio = self.load("manual_image_nodes")
        data = studio._request({"task_id": "storyboard-test", "source_mode": "文生视频",
            "width": 1024, "height": 768, "shots": [{"id": "分镜1", "prompt": "女孩在窗边放下茶杯。", "references": []}],
            "image_settings": {"provider": "bbapi", "model": "image-model"}})
        self.assertEqual(data["shots"][0]["references"], [])
        self.assertEqual(data["image_settings"]["provider"], "bbapi")


if __name__ == "__main__":
    unittest.main()
