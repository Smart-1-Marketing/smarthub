"""Offline tests for MCP V2 identity and connector read boundaries."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from mcp_gateway import v2_tools


class CanonicalIdentityTests(unittest.TestCase):
    def test_public_identity_keeps_match_evidence(self):
        raw = {
            "known": True,
            "client": "Riverside HVAC",
            "key": "d:riverside-hvac.com",
            "domain": "riverside-hvac.com",
            "matched_on": "domain",
            "confidence": "exact",
            "candidates": [],
            "why": "Exact domain match.",
            "input": {"name": "ignored internal field"},
        }
        out = v2_tools.public_identity(raw)
        self.assertEqual(out["client_key"], "d:riverside-hvac.com")
        self.assertEqual(out["matched_on"], "domain")
        self.assertEqual(out["confidence"], "exact")
        self.assertNotIn("input", out)

    def test_ambiguous_match_is_not_promoted_to_known(self):
        with patch.object(v2_tools.hub_client_key, "resolve", return_value={
            "known": False,
            "client": "Acme",
            "key": "n:acme",
            "domain": "",
            "matched_on": "",
            "confidence": "unmatched",
            "candidates": ["Acme Plumbing", "Acme Roofing"],
            "why": "2 clients could be Acme.",
        }):
            out = v2_tools.resolve_identity("Acme")
        self.assertFalse(out["known"])
        self.assertEqual(out["candidates"], ["Acme Plumbing", "Acme Roofing"])
        self.assertEqual(out["confidence"], "unmatched")


class QuickBooksBoundaryTests(unittest.TestCase):
    def test_status_drops_token_and_realm_material(self):
        health = {
            "connected": True,
            "ok": True,
            "environment": "production",
            "persistent_storage": True,
            "disk_mounted": True,
            "backed_up": True,
            "refresh_age_days": 2,
            "refresh_days_left": 98,
            "problems": [],
            "token_path": "/var/data/quickbooks_tokens.json",
            "token_file_exists": True,
            "realm_id": True,
            "access_token": "must-not-leak",
            "refresh_token": "must-not-leak",
        }
        with patch.object(v2_tools.quickbooks, "health", return_value=health), \
             patch.object(v2_tools.quickbooks, "configured", return_value=True), \
             patch.object(v2_tools.quickbooks, "connected", return_value=True):
            out = v2_tools.quickbooks_status()
        text = repr(out).lower()
        self.assertTrue(out["connected"])
        self.assertNotIn("token_path", out)
        self.assertNotIn("realm_id", out)
        self.assertNotIn("access_token", text)
        self.assertNotIn("refresh_token", text)
        self.assertNotIn("must-not-leak", text)

    def test_unavailable_quickbooks_is_explicit(self):
        identity = {
            "known": True,
            "client": "Quality Air Columbus",
            "client_key": "d:qualityaircolumbus.com",
            "domain": "qualityaircolumbus.com",
            "matched_on": "domain",
            "confidence": "exact",
            "candidates": [],
            "why": "Exact domain match.",
        }
        with patch.object(v2_tools, "resolve_identity", return_value=identity), \
             patch.object(v2_tools, "quickbooks_status", return_value={
                 "available": True, "configured": True, "connected": False,
             }), \
             patch.object(v2_tools, "_audit"):
            out = v2_tools.client_quickbooks("Quality Air Columbus")
        self.assertTrue(out["found"])
        self.assertFalse(out["available"])
        self.assertEqual(out["customers"], [])

    def test_client_lookup_returns_only_sanitized_invoice_fields(self):
        identity = {
            "known": True,
            "client": "Quality Air Columbus",
            "client_key": "d:qualityaircolumbus.com",
            "domain": "qualityaircolumbus.com",
            "matched_on": "domain",
            "confidence": "exact",
            "candidates": [],
            "why": "Exact domain match.",
        }
        lookup = {"customers": [{
            "id": "123", "name": "Quality Air Columbus", "balance": 250.0,
            "customer_since": "2024-01-01", "customer_since_label": "Jan 2024",
            "customer_years": 2.7, "link": "https://app.qbo.intuit.com/customer/123",
            "secret": "do-not-return",
            "invoices": [{
                "id": "999", "doc_number": "INV-9", "date": "2026-09-01",
                "due_date": "2026-09-30", "total": 500, "balance": 250,
                "status": "Open", "link": "https://app.qbo.intuit.com/invoice/999",
                "raw": "do-not-return",
            }],
        }]}
        with patch.object(v2_tools, "resolve_identity", return_value=identity), \
             patch.object(v2_tools, "quickbooks_status", return_value={
                 "available": True, "configured": True, "connected": True,
             }), \
             patch.object(v2_tools.quickbooks, "lookup", return_value=lookup), \
             patch.object(v2_tools, "_audit"):
            out = v2_tools.client_quickbooks("Quality Air Columbus")
        customer = out["customers"][0]
        invoice = customer["invoices"][0]
        self.assertNotIn("secret", customer)
        self.assertNotIn("id", invoice)
        self.assertNotIn("raw", invoice)
        self.assertEqual(invoice["doc_number"], "INV-9")


class OperationalReadTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "known": True, "client": "Quality Air Columbus",
            "client_key": "d:qualityaircolumbus.com",
            "domain": "qualityaircolumbus.com", "matched_on": "domain",
            "confidence": "exact", "candidates": [], "why": "Exact match.",
        }

    def test_ga4_property_list_drops_google_login(self):
        indexed = {"built_at": "2026-09-12T12:00:00+00:00", "stale": False,
                   "never_built": False, "ga4": [{
                       "resource_id": "123456", "name": "Quality Air",
                       "account_name": "Smart 1", "google_login": "secret@example.com",
                       "match": "name", "match_detail": "Exact name",
                       "open_url": "https://analytics.google.com/analytics/web/#/p123456",
                   }]}
        with patch.object(v2_tools, "resolve_identity", return_value=self.identity), \
             patch("hub.google_index.for_client", return_value=indexed), \
             patch.object(v2_tools, "_audit"):
            out = v2_tools.client_ga4_properties("Quality Air Columbus")
        self.assertTrue(out["available"])
        self.assertEqual(out["properties"][0]["property_id"], "123456")
        self.assertNotIn("google_login", repr(out))
        self.assertNotIn("secret@example.com", repr(out))

    def test_ga4_summary_refuses_unmapped_property(self):
        indexed = {"built_at": "2026-09-12T12:00:00+00:00", "stale": False,
                   "never_built": False, "ga4": [{
                       "resource_id": "123456", "name": "Quality Air",
                       "google_login": "analytics@example.com",
                   }]}
        with patch.object(v2_tools, "resolve_identity", return_value=self.identity), \
             patch("hub.google_index.for_client", return_value=indexed), \
             patch.object(v2_tools, "_audit"):
            out = v2_tools.client_ga4_summary(
                "Quality Air Columbus", property_id="999999")
        self.assertFalse(out["available"])
        self.assertEqual(out["reason"], "property_not_mapped")

    def test_proposals_read_uses_canonical_name(self):
        result = {"count": 1, "saved": [{"quote_number": "Q-10001"}],
                  "uploaded": [], "note": ""}
        with patch.object(v2_tools, "resolve_identity", return_value=self.identity), \
             patch("hub.proposals.proposals_for", return_value=result) as read, \
             patch.object(v2_tools, "_audit"):
            out = v2_tools.client_proposals("Quality Air")
        read.assert_called_once_with("Quality Air Columbus")
        self.assertEqual(out["count"], 1)

    def test_io_read_drops_internal_delivery_identifiers(self):
        result = {"measured": True, "error": "", "rows": [{
            "order": "IO-88", "client": "Quality Air Columbus",
            "monthly": 9500, "campaign_total": 114000,
            "suite_opportunity_id": "must-not-return",
            "suite_contact_id": "must-not-return",
            "suite": {"delivered": True, "ever_delivered": True},
            "lines": [{"product": "Paid Search", "budget": 4000,
                       "internal_note": "must-not-return"}],
        }]}
        with patch.object(v2_tools, "resolve_identity", return_value=self.identity), \
             patch("hub.io_records.listing", return_value=result), \
             patch.object(v2_tools, "_audit"):
            out = v2_tools.client_insertion_orders("Quality Air")
        text = repr(out)
        self.assertEqual(out["orders"][0]["order"], "IO-88")
        self.assertNotIn("must-not-return", text)
        self.assertNotIn("suite_opportunity_id", text)


if __name__ == "__main__":
    unittest.main()
