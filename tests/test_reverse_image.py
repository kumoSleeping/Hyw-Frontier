"""Offline regression coverage; never sends a live TinEye query."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from threading import Event, Barrier
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from jsonschema import Draft202012Validator

from hyw_frontier.image_crop import UserImageCrops
from hyw_frontier.jina import JinaClient, JinaError
from hyw_frontier.media import ImagePipeline, MAX_EDGE, MAX_JPEG_BYTES
from hyw_frontier.reverse_image import ReverseImageSearch, tineye_matches, visual_matches
from hyw_frontier.source_titles import source_titles
from hyw_frontier.tools import ToolRuntime, tool_definitions
from hyw_frontier.image_bridge import load_bridge, save_bridge, bridge_status
from tempfile import TemporaryDirectory

BRIDGE_URL = "https://bridge.example.org"
BRIDGE_API_KEY = "test-only-upload-key"
BRIDGE = {"url": BRIDGE_URL, "api_key": BRIDGE_API_KEY}


# The real successful response has no status field; preserve that regression.
CONTENT = json.dumps({"num_matches": 1, "matches": [{"domain": "example.org", "score": 33,
    "backlinks": [{"url": "https://example.org/match.png", "backlink": "https://example.org/page"}]}]})


class Uploads:
    def __init__(self, raw):
        self.calls = []
        digest = hashlib.sha256(raw).hexdigest()
        self.result = {"url": BRIDGE_URL + "/images/" + digest, "sha256": digest,
                       "expiresAt": datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat()}

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return BytesIO(json.dumps(self.result).encode())

    def close(self):
        pass


class ReverseImageTests(unittest.TestCase):
    def setUp(self):
        with Image.new("RGB", (1600, 800), "orange") as image, BytesIO() as output:
            image.save(output, "PNG")
            self.raw = output.getvalue()
        self.messages = [{"role": "user", "content": [{"type": "image", "mimeType": "image/png",
                          "data": base64.b64encode(self.raw).decode()}]}]
        self.crops = UserImageCrops(self.messages)
        self.reads = []

        def transport(endpoint, body, headers):
            self.reads.append((endpoint, body, headers))
            return {"data": {"content": CONTENT if "tineye.com/" in body["url"] else "No results found", "url": body["url"]}}

        self.jina = JinaClient(Path("/unused"), transport=transport)
        self.uploads = Uploads(self.raw)
        self.search = ReverseImageSearch(self.jina, self.crops.originals, opener=self.uploads, bridge=BRIDGE,
                                         image_lookup=self.crops.resolve_search_image)
        self.args = {"source_id": self.crops.originals[0]["_user_image"]["source_id"]}
        self.addCleanup(self.crops.close)
        self.addCleanup(self.jina.close)
        self.addCleanup(self.search.close)

    def test_upload_only_on_call_single_flight_and_history_reuse(self):
        self.assertEqual(self.uploads.calls, [])
        self.assertEqual(self.reads, [])
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(self.search.search, [self.args] * 3))
        self.assertEqual(len(self.uploads.calls), 1)
        self.assertEqual(len(self.reads), 2)
        request, timeout = self.uploads.calls[0]
        self.assertEqual(request.data, self.raw)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + BRIDGE_API_KEY)
        self.assertEqual(request.get_header("Content-type"), "image/png")
        self.assertEqual(timeout, 30)
        endpoint, body, headers = next(r for r in self.reads if "tineye.com/" in r[1]["url"])
        self.assertEqual(endpoint, "https://r.jina.ai/")
        self.assertFalse(any(k.lower() == "authorization" for k in headers))
        self.assertEqual(parse_qs(urlsplit(body["url"]).query),
                         {"page": ["1"], "url": [self.uploads.result["url"]]})
        self.assertEqual(results[0]["sources"][1]["content"], CONTENT)
        self.assertEqual(results[0]["matches"][0]["url"], "https://example.org/page")
        history = UserImageCrops(deepcopy(self.messages))
        self.addCleanup(history.close)
        next_search = ReverseImageSearch(self.jina, history.originals, opener=self.uploads, bridge=BRIDGE)
        self.addCleanup(next_search.close)
        self.assertTrue(next_search.search(self.args)["upload_reused"])
        self.assertEqual(len(self.uploads.calls), 1)

    def test_expired_or_mismatched_metadata_reuploads(self):
        for metadata in ({**self.uploads.result, "expiresAt": "2000-01-01T00:00:00Z"},
                         {**self.uploads.result, "sha256": "a" * 64, "url": BRIDGE_URL + "/images/" + "a" * 64}):
            self.search._uploads.clear()
            self.crops.originals[0]["_reverse_image_upload"] = metadata
            self.search.search(self.args)
        self.assertEqual(len(self.uploads.calls), 2)

    def test_url_import_and_pagination(self):
        self.search.search({"url": "https://example.org/input.png?a=1&b=2", "page": 2})
        request, _ = self.uploads.calls[0]
        self.assertEqual(json.loads(request.data), {"url": "https://example.org/input.png?a=1&b=2"})
        self.assertEqual(parse_qs(urlsplit(next(r for r in self.reads if "tineye.com/" in r[1]["url"])[1]["url"]).query)["page"], ["2"])

    def test_failed_upload_stops_before_reader_without_retry(self):
        with patch.object(self.uploads, "open", side_effect=HTTPError(BRIDGE_URL, 401, "", {}, None)) as upload:
            with self.assertRaises(JinaError) as caught:
                self.search.search(self.args)
            self.assertEqual(caught.exception.code, "image_upload_http_401")
            upload.assert_called_once()
        self.assertEqual(self.reads, [])

    def test_invalid_sources_and_schema(self):
        for args in ({"source_id": "missing"}, {"url": "https://127.0.0.1/a"}, {"url": "http://example.org/a"}):
            with self.assertRaises(JinaError):
                self.search.search(args)
        self.assertEqual(self.uploads.calls, [])
        for provider in ("jina", "parallel", "ddgs"):
            schema = next(t["parameters"] for t in tool_definitions(provider) if t["name"] == "reverse_image_search")
            validator = Draft202012Validator(schema)
            self.assertTrue(validator.is_valid(self.args))
            self.assertTrue(validator.is_valid({"url": "https://example.org/a"}))
            self.assertFalse(validator.is_valid({}))
            self.assertFalse(validator.is_valid({**self.args, "url": "https://example.org/a"}))

    def test_invalid_tineye_is_not_evidence(self):
        for content in ("challenge page", '{"status":"fail","num_matches":0,"matches":[]}',
                        '{"status":"ok"}', '{"num_matches":true,"matches":[]}',
                        '{"num_matches":0,"matches":[],"error":"blocked"}'):
            with self.assertRaises(JinaError):
                tineye_matches(content)
        self.assertEqual(tineye_matches('{"num_matches":0,"matches":[]}'), [])
        self.assertEqual(tineye_matches('{"num_matches":1,"matches":[{"backlinks":null}]}'), [])

    def test_all_domain_backlinks_are_retained_without_duplicates(self):
        payload = json.loads(CONTENT)
        row = payload["matches"][0]
        extra = {"backlink": "https://another.example.org/post", "url": "https://another.example.org/image.jpg"}
        row["domains"] = [{"backlinks": row["backlinks"] + [extra]}]
        matches = tineye_matches(json.dumps(payload))
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[1]["url"], extra["backlink"])

    def test_query_events_have_identity_and_actual_match_count(self):
        runtime = ToolRuntime(self.jina, search_provider="jina")
        runtime.reverse_image_search = self.search.search
        events = []
        result = runtime.execute({"id": "reverse", "name": "reverse_image_search", "arguments": self.args}, on_query=events.append)
        self.assertFalse(result["isError"])
        self.assertEqual([e["type"] for e in events], ["query_start", "query_end"])
        self.assertTrue(all(e["id"] == "reverse" and e["name"] == "reverse_image_search" for e in events))
        self.assertEqual(events[-1]["result_count"], 1)
        self.assertEqual(events[0]["provider"], "yandex+tineye")

    def test_tool_image_pipeline_compression_binding_and_double_timeout(self):
        runtime = ToolRuntime(self.jina, search_provider="jina")
        runtime.reverse_image_search = self.search.search
        result = runtime.execute({"id": "reverse", "name": "reverse_image_search", "arguments": self.args})
        ordinary = {"role": "toolResult", "toolName": "search_images", "content": [{"type": "text", "text": json.dumps({
            "results": [{"ok": True, "results": [{"url": "https://example.org/other", "image_url": "https://example.org/other.png"}]}]})}]}
        timeouts = {}

        def download(candidate, cancel, timeout):
            timeouts[candidate.url] = timeout
            return candidate, self.raw, "downloaded", 1

        pipeline = ImagePipeline()
        self.addCleanup(pipeline.close)
        with patch("hyw_frontier.media.download", side_effect=download):
            pipeline.prepare([ordinary, result], Event(), lambda event: None)
        self.assertEqual(timeouts, {"https://example.org/match.png": 5.0, "https://example.org/other.png": 2.5})
        self.assertEqual(result["content"][2]["type"], "image")
        self.assertEqual(json.loads(result["content"][1]["text"])["url"], "https://example.org/match.png")
        raw = base64.b64decode(result["content"][2]["data"])
        self.assertLessEqual(len(raw), MAX_JPEG_BYTES)
        with Image.open(BytesIO(raw)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(max(image.size), MAX_EDGE)
        self.assertEqual(source_titles([result])["https://example.org/match.png"], "example.org")
        self.assertEqual(json.loads(result["content"][0]["text"])["results"][0]["sources"][1]["content"], CONTENT)

    def test_download_timeout_keeps_reader_text(self):
        runtime = ToolRuntime(self.jina, search_provider="jina")
        runtime.reverse_image_search = self.search.search
        result = runtime.execute({"id": "reverse", "name": "reverse_image_search", "arguments": self.args})
        pipeline = ImagePipeline()
        self.addCleanup(pipeline.close)
        with patch("hyw_frontier.media.download", side_effect=lambda c, cancel, timeout: (c, None, "download_timeout", 5000)):
            pipeline.prepare([result], Event(), lambda event: None)
        data = json.loads(result["content"][0]["text"])
        self.assertEqual(data["results"][0]["sources"][1]["content"], CONTENT)
        self.assertEqual(data["media_images"][0]["status"], "download_timeout")
        self.assertEqual(len(result["content"]), 1)

    def test_two_readers_parallel_and_provenance(self):
        barrier = Barrier(2)
        yandex = '* [![Image 8: Author](https://avatars.mds.yandex.net/thumb)200×143](https://example.org/match.png) [Author post](https://example.org/post) https://example.org/match.png'
        seen = []

        def transport(endpoint, body, headers):
            seen.append(body["url"])
            barrier.wait(timeout=3)  # Serial execution cannot pass this test.
            content = CONTENT if "tineye.com" in body["url"] else yandex
            return {"data": {"content": content}}

        self.jina.transport = transport
        runtime = ToolRuntime(self.jina)
        runtime.reverse_image_search = self.search.search
        result = runtime.execute({"id": "hybrid", "name": "reverse_image_search", "arguments": self.args})
        data = json.loads(result["content"][0]["text"])
        self.assertTrue(data["ok"])
        self.assertFalse(data["partial"])
        self.assertEqual(len(seen), 2)
        self.assertFalse(any("google" in url for url in seen))
        hybrid = data["results"][0]
        self.assertEqual([s["status"] for s in hybrid["sources"]], ["matched", "matched"])
        self.assertEqual(len(hybrid["matches"]), 1)
        match = hybrid["matches"][0]
        self.assertEqual(match["engines"], ["yandex", "tineye"])
        self.assertEqual({s["url"] for s in match["sources"]}, {"https://example.org/post", "https://example.org/page"})
        self.assertEqual(hybrid["deduplication"]["duplicates_merged"], 1)
        self.assertNotIn(BRIDGE_API_KEY, result["content"][0]["text"])
        pipeline = ImagePipeline()
        self.addCleanup(pipeline.close)
        with patch("hyw_frontier.media.download", side_effect=lambda c, cancel, timeout: (c, self.raw, "downloaded", 1)) as download:
            pipeline.prepare([result], Event(), lambda _: None)
        download.assert_called_once()
        binding = json.loads(result["content"][1]["text"])
        self.assertEqual(binding["engines"], ["yandex", "tineye"])
        self.assertEqual(len(binding["source_urls"]), 2)

    def test_lens_blob_retains_page_and_filters_favicons(self):
        content = '\n'.join([
            '[![Image 13](blob:http://localhost/abc) Manga title](https://lens.google.com/goto?url=one)',
            '[![Image 14](https://encrypted-tbn0.gstatic.com/images?q=two) Related panel](https://lens.google.com/goto?url=two)',
            '[![Image 15](https://encrypted-tbn0.gstatic.com/favicon-tbn?q=three) Page title](https://lens.google.com/goto?url=three)',
            '[![logo](https://google.com/logo.png) Sign in](https://accounts.google.com/login)',
        ])
        rows = visual_matches(content, "google_lens", "https://lens.google.com/upload")
        self.assertEqual(len(rows), 3)
        self.assertNotIn("image_url", rows[0])
        self.assertEqual(rows[0]["title"], "Manga title")
        self.assertEqual(rows[0]["source_url_status"], "redirect_link")
        self.assertIn("gstatic.com/images", rows[1]["image_url"])
        self.assertNotIn("image_url", rows[2])

    def test_yandex_prefers_observed_original_over_thumbnail(self):
        content = '* [![Image 8: Title](https://avatars.mds.yandex.net/thumb)200×143](https://images.example.org/mx_original.jpg) [Source title](https://example.org/post) [example.org](https://example.org/post)https://images.example.org/original.jpg'
        rows = visual_matches(content, "yandex", "https://yandex.com/images/search")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["image_url"], "https://images.example.org/original.jpg")
        self.assertEqual(rows[0]["url"], "https://example.org/post")

    def test_engine_network_error_does_not_drop_other_results(self):
        def read(args):
            if "yandex.com" in args["url"]:
                raise JinaError("network_error", "timeout")
            return {"content": CONTENT if "tineye.com" in args["url"] else "No results found"}
        with patch.object(self.jina, "read_url", side_effect=read):
            result = self.search.search(self.args)
        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["sources"][0]["code"], "network_error")
        self.assertEqual(len(result["matches"]), 1)
        with patch.object(self.jina, "read_url", side_effect=JinaError("network_error", "timeout")):
            result = self.search.search(self.args)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["sources"]), 2)

    def test_private_bridge_settings_do_not_echo_key(self):
        with TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual(bridge_status(home), {"configured": False})
            result = save_bridge(home, BRIDGE)
            self.assertNotIn("api_key", result)
            self.assertNotIn("url", bridge_status(home))
            self.assertEqual(load_bridge(home), BRIDGE)
            self.assertEqual((home / "image-bridge.json").stat().st_mode & 0o777, 0o600)
            save_bridge(home, {"url": BRIDGE_URL, "api_key": ""})
            self.assertEqual(load_bridge(home), BRIDGE)
            with self.assertRaises(JinaError):
                save_bridge(home, {"url": "https://different.example.org", "api_key": ""})
            for invalid in ({"url": None}, {"url": BRIDGE_URL, "api_key": []},
                            {"url": "http://example.org", "api_key": "test"}):
                with self.assertRaises(JinaError):
                    save_bridge(home, invalid)

    def test_crop_then_reverse_uploads_exact_crop_and_reuses_in_history(self):
        runtime = ToolRuntime(self.jina)
        runtime.crop_user_image = self.crops.crop
        runtime.reverse_image_search = self.search.search
        cropped = runtime.execute({"id": "crop", "name": "crop_user_image", "arguments": {
            **self.args, "bbox": [50, 100, 450, 300]}})
        report = json.loads(cropped["content"][0]["text"])
        self.assertTrue(report["ok"])
        crop_id = report["crop_id"]
        marker = json.loads(cropped["content"][1]["text"].removeprefix("user_image_crop "))
        self.assertEqual(marker["crop_id"], crop_id)
        raw = base64.b64decode(cropped["content"][2]["data"])
        self.assertNotEqual(raw, self.raw)
        with Image.open(BytesIO(raw)) as image:
            self.assertEqual(image.size, (400, 200))
        self.assertEqual(self.uploads.calls, [])  # Crop alone never uploads.
        self.uploads.result = Uploads(raw).result
        result = runtime.execute({"id": "search-crop", "name": "reverse_image_search", "arguments": {"source_id": crop_id}})
        self.assertFalse(result["isError"])
        self.assertEqual(self.uploads.calls[0][0].data, raw)
        self.assertEqual(len(self.reads), 2)
        self.assertTrue(all(parse_qs(urlsplit(r[1]["url"]).query)["url"] == [self.uploads.result["url"]] for r in self.reads))
        self.messages.append(cropped)
        self.messages.append({"role": "user", "content": [{"type": "text", "text": "继续核对"}]})
        history = UserImageCrops(deepcopy(self.messages))
        self.addCleanup(history.close)
        followup = ReverseImageSearch(self.jina, history.originals, opener=self.uploads, bridge=BRIDGE,
                                     image_lookup=history.resolve_search_image)
        self.addCleanup(followup.close)
        self.assertTrue(followup.search({"source_id": crop_id})["upload_reused"])
        self.assertEqual(len(self.uploads.calls), 1)
        self.assertEqual(self.crops.originals[0]["data"], base64.b64encode(self.raw).decode())

    def test_crop_ids_are_stable_scoped_and_invalid_ids_do_not_upload(self):
        first, blocks = self.crops.crop(self.args["source_id"], [0, 0, 400, 200])
        again, _ = self.crops.crop(self.args["source_id"], [0, 0, 400, 200])
        other, _ = self.crops.crop(self.args["source_id"], [100, 0, 500, 200])
        self.assertEqual(first["crop_id"], again["crop_id"])
        self.assertNotEqual(first["crop_id"], other["crop_id"])
        with self.assertRaises(JinaError):
            self.search.search({"source_id": "crop_not_returned"})
        self.assertEqual(self.uploads.calls, [])
        self.messages.extend([
            {"role": "toolResult", "toolName": "crop_user_image", "content": blocks},
            {"role": "user", "content": [{"type": "image", "mimeType": "image/png", "data": base64.b64encode(self.raw).decode()}]},
        ])
        latest = UserImageCrops(self.messages)
        self.addCleanup(latest.close)
        self.assertIsNone(latest.resolve_search_image(first["crop_id"]))
        self.crops.close()
        self.assertIsNone(self.crops.resolve_search_image(first["crop_id"]))


if __name__ == "__main__":
    unittest.main()
