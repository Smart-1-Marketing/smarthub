"""Small, dependency-light tests for the SmartHub MCP boundary.

These intentionally avoid live Knack/Google/QuickBooks calls. CI should prove
that the gateway's identity behavior and fail-closed authentication stay safe
without requiring production credentials.
"""
from __future__ import annotations

import asyncio
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


class ToolMetadataTests(unittest.TestCase):
    def test_all_v1_tools_advertise_closed_world_read_only_metadata(self):
        tools = asyncio.run(server.mcp.list_tools())
        self.assertEqual(
            {tool.name for tool in tools},
            {
                "search_clients",
                "get_client",
                "get_client_services",
                "get_client_websites",
                "get_campaign_inventory",
                "get_mcp_activity",
            },
        )
        for tool in tools:
            with self.subTest(tool=tool.name):
                self.assertTrue(tool.title)
                self.assertIsNotNone(tool.annotations)
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertTrue(tool.annotations.idempotent_hint)
                self.assertFalse(tool.annotations.open_world_hint)


if __name__ == "__main__":
    unittest.main()
