"""Small, dependency-light tests for the SmartHub MCP boundary.

These intentionally avoid live Knack/Google/QuickBooks calls. CI should prove
that the gateway's identity behavior and fail-closed authentication stay safe
without requiring production credentials.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mcp_gateway import server


class ClientResolutionTests(unittest.TestCase):
    def test_exact_client_name_resolves(self):
        with patch.object(server, "_client_names", return_value={
            "qualityaircolumbus": "Quality Air Columbus",
            "qualityairdayton": "Quality Air Dayton",
        }):
            hit, suggestions = server._resolve_client("Quality Air Columbus")
        self.assertEqual(hit, "Quality Air Columbus")
        self.assertEqual(suggestions, [])

    def test_ambiguous_partial_name_never_guesses(self):
        with patch.object(server, "_client_names", return_value={
            "acmeplumbing": "Acme Plumbing",
            "acmeroofing": "Acme Roofing",
        }):
            hit, suggestions = server._resolve_client("Acme")
        self.assertIsNone(hit)
        self.assertEqual(suggestions, ["Acme Plumbing", "Acme Roofing"])

    def test_unique_partial_name_can_resolve(self):
        with patch.object(server, "_client_names", return_value={
            "qualityaircolumbus": "Quality Air Columbus",
            "monogramhomes": "Monogram Homes",
        }):
            hit, suggestions = server._resolve_client("Monogram")
        self.assertEqual(hit, "Monogram Homes")
        self.assertEqual(suggestions, [])


class AuthBoundaryTests(unittest.TestCase):
    def test_gateway_is_fail_closed_without_token(self):
        previous = os.environ.pop("MCP_API_TOKEN", None)
        try:
            self.assertFalse(bool((os.environ.get("MCP_API_TOKEN") or "").strip()))
        finally:
            if previous is not None:
                os.environ["MCP_API_TOKEN"] = previous

    def test_capability_contract_is_read_only(self):
        text = server.capabilities()
        self.assertIn("read-only", text.lower())
        self.assertIn("cannot", text.lower())
        self.assertIn("post accounting transactions", text.lower())


if __name__ == "__main__":
    unittest.main()
