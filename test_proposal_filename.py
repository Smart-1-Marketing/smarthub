"""Offline tests of the actual named-document response, with storage mocked."""
import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask
import requests

ROOT = Path(__file__).resolve().parent
tree = ast.parse((ROOT / "hub/proposals.py").read_text(encoding="utf-8"))
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in {"_safe_name", "document_response"}]
namespace = {"os": os, "re": __import__("re"), "MAX_BYTES": 1024,
             "ALLOWED": {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / "hub/proposals.py"), "exec"), namespace)


class ProposalFilenameTests(unittest.TestCase):
    def setUp(self):
        self.record = {"id": "random-id", "kind": "pdf", "filename": "Client Growth Plan 2026.pdf",
                       "url": "https://res.cloudinary.com/smart1snap/raw/upload/v1/random-id.pdf"}
        namespace["list_proposals"] = lambda client, backfill=False: [self.record] if client == "Owner & Co" else []
        self.app = Flask(__name__)
        self.app.add_url_rule("/document", view_func=lambda: namespace["document_response"]("Owner & Co", "random-id"))
        self.client = self.app.test_client()
        self.response = MagicMock()
        self.response.status_code = 200
        self.response.iter_content.return_value = [b"%PDF-1.4 test"]
        self.response.__enter__.return_value = self.response
        self.network = patch("requests.get", return_value=self.response)
        self.get = self.network.start()
        self.addCleanup(self.network.stop)

    def assert_named(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"%PDF-1.4 test")
        self.assertEqual(response.headers["Content-Disposition"], 'inline; filename="Client Growth Plan 2026.pdf"')
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        response.close()

    def test_existing_cloud_file_keeps_original_filename(self):
        self.assert_named(self.client.get("/document"))
        self.get.assert_called_once_with(self.record["url"], timeout=30, stream=True, allow_redirects=False)

    def test_local_file_keeps_original_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "random-id.pdf"
            document.write_bytes(b"%PDF-1.4 test")
            self.record["url"] = "/api/client/proposals/file/random-id.pdf"
            namespace["local_file_path"] = lambda name: str(document)
            self.assert_named(self.client.get("/document"))
            self.get.assert_not_called()

    def test_untrusted_file_locations_and_live_links_are_not_fetched(self):
        for url in ["https://example.com/a.pdf", "https://res.cloudinary.com.evil.test/raw/upload/a.pdf",
                    "http://res.cloudinary.com/raw/upload/a.pdf"]:
            self.record["url"] = url
            self.assertEqual(self.client.get("/document").status_code, 404)
        self.record["kind"] = "link"
        self.assertEqual(self.client.get("/document").status_code, 404)
        self.get.assert_not_called()

    def test_oversized_and_unavailable_files(self):
        self.response.iter_content.return_value = [b"x" * 1025]
        self.assertEqual(self.client.get("/document").status_code, 413)
        self.response.status_code = 302
        self.assertEqual(self.client.get("/document").status_code, 502)
        self.get.side_effect = requests.Timeout()
        self.assertEqual(self.client.get("/document").status_code, 502)

    def test_wrong_client_cannot_resolve_the_file(self):
        namespace["list_proposals"] = lambda client, backfill=False: []
        self.assertEqual(self.client.get("/document").status_code, 404)
        self.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
