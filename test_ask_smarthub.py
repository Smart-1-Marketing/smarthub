"""Boundaries for the read-only Ask SmartHub planner and executor."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hub import ask_smarthub


class AskPermissionsTests(unittest.TestCase):
    def test_member_cannot_plan_quickbooks(self):
        self.assertNotIn("get_client_quickbooks", ask_smarthub.allowed_tools("member"))
        self.assertIn("get_client_proposals", ask_smarthub.allowed_tools("member"))

    def test_admin_can_read_quickbooks(self):
        self.assertIn("get_client_quickbooks", ask_smarthub.allowed_tools("admin"))

    def test_demo_has_no_tools(self):
        self.assertEqual(ask_smarthub.allowed_tools("demo"), {})

    def test_unknown_and_forbidden_calls_are_removed(self):
        raw = {"calls": [
            {"tool": "delete_client", "arguments": {"client_name": "Acme"}},
            {"tool": "get_client_quickbooks", "arguments": {"client_name": "Acme"}},
            {"tool": "get_client_proposals", "arguments": {
                "client_name": "Acme", "arbitrary_url": "https://evil.invalid"}},
        ]}
        out = ask_smarthub.validate_plan(raw, "member")
        self.assertEqual(out["calls"], [{
            "tool": "get_client_proposals",
            "arguments": {"client_name": "Acme"},
        }])

    def test_plan_is_limited_to_four_reads(self):
        calls = [{"tool": "search_clients", "arguments": {"query": str(i)}}
                 for i in range(8)]
        self.assertEqual(len(ask_smarthub.validate_plan({"calls": calls}, "member")["calls"]), 4)


class AskExecutionTests(unittest.TestCase):
    def test_executor_calls_only_registered_adapter(self):
        fn = Mock(return_value={"count": 1})
        tool = ask_smarthub.Tool("test", ("member",), fn, ("query",))
        with patch.dict(ask_smarthub.TOOLS, {"safe": tool}, clear=True):
            out = ask_smarthub.execute(
                {"calls": [{"tool": "safe", "arguments": {
                    "query": "Acme", "url": "https://evil.invalid"}}]}, "member")
        fn.assert_called_once_with(query="Acme")
        self.assertTrue(out[0]["ok"])

    def test_bad_arguments_return_refusal_not_exception_detail(self):
        def strict(client_name):
            return {"client": client_name}
        tool = ask_smarthub.Tool("test", ("member",), strict, ("client_name",))
        with patch.dict(ask_smarthub.TOOLS, {"safe": tool}, clear=True):
            out = ask_smarthub.execute(
                {"calls": [{"tool": "safe", "arguments": {}}]}, "member")
        self.assertFalse(out[0]["ok"])
        self.assertNotIn("strict", out[0]["error"])


class AskAuditPrivacyTests(unittest.TestCase):
    def test_diagnostics_redact_common_pii_and_credentials(self):
        safe = ask_smarthub._audit_question(
            "Find jane@example.com or 614-555-1234; "
            "SSN 123-45-6789; card 4111 1111 1111 1111; "
            "bearer secret-token; api_key=also-secret"
        )
        self.assertNotIn("jane@example.com", safe)
        self.assertNotIn("614-555-1234", safe)
        self.assertNotIn("123-45-6789", safe)
        self.assertNotIn("4111 1111 1111 1111", safe)
        self.assertNotIn("secret-token", safe)
        self.assertNotIn("also-secret", safe)
        self.assertIn("[redacted email]", safe)
        self.assertIn("[redacted phone]", safe)
        self.assertIn("[redacted SSN]", safe)
        self.assertIn("[redacted payment number]", safe)
        self.assertIn("[redacted credential]", safe)

    def test_diagnostics_keep_the_business_question_useful(self):
        safe = ask_smarthub._audit_question(
            "Show Quality Air Columbus proposals and email jane@example.com"
        )
        self.assertIn("Show Quality Air Columbus proposals", safe)
        self.assertNotIn("jane@example.com", safe)


class AskClientMatchingTests(unittest.TestCase):
    @staticmethod
    def _index(*names):
        entries = {}
        for number, name in enumerate(names):
            key = f"d:client-{number}.example"
            entries[key] = {"key": key, "name": name,
                            "domain": f"client-{number}.example", "names": [name]}
        return {"entries": entries, "by_name": {}, "by_domain": {}}

    def test_unique_initials_expand_abbreviation(self):
        index = self._index("Quality Air Columbus", "Monogram Homes")
        unresolved = {"known": False, "client": "QAC", "candidates": []}
        with patch.object(ask_smarthub.v2_tools, "resolve_identity", return_value=unresolved), \
             patch.object(ask_smarthub.v2_tools.hub_client_key, "alias_index", return_value=index):
            result = ask_smarthub.match_client("QAC")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["client"], "Quality Air Columbus")
        self.assertEqual(result["matched_on"], "abbreviation")

    def test_unique_typo_is_resolved_for_read_only_question(self):
        index = self._index("Monogram Homes", "Quality Air Columbus")
        unresolved = {"known": False, "client": "Monagram Homes", "candidates": []}
        with patch.object(ask_smarthub.v2_tools, "resolve_identity", return_value=unresolved), \
             patch.object(ask_smarthub.v2_tools.hub_client_key, "alias_index", return_value=index):
            result = ask_smarthub.match_client("Monagram Homes")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["client"], "Monogram Homes")

    def test_ambiguous_abbreviation_asks_instead_of_guessing(self):
        index = self._index("National Background Check", "North Building Company")
        unresolved = {"known": False, "client": "NBC", "candidates": []}
        with patch.object(ask_smarthub.v2_tools, "resolve_identity", return_value=unresolved), \
             patch.object(ask_smarthub.v2_tools.hub_client_key, "alias_index", return_value=index):
            result = ask_smarthub.match_client("NBC")
        self.assertEqual(result["status"], "clarify")
        self.assertEqual([row["client"] for row in result["choices"][:2]],
                         ["National Background Check", "North Building Company"])

    def test_clarification_blocks_every_data_read(self):
        plan = {"calls": [{"tool": "get_client_proposals",
                           "arguments": {"client_name": "Acme"}}],
                "direct_answer": ""}
        match = {"status": "clarify", "requested": "Acme", "client": "",
                 "choices": [{"client": "Acme Plumbing", "domain": "acme.test",
                              "score": .9, "matched_on": "fuzzy"}]}
        with patch.object(ask_smarthub, "match_client", return_value=match):
            resolved, _matches, clarification = ask_smarthub.resolve_plan_clients(
                plan, "Show Acme proposals")
        self.assertEqual(resolved["calls"], [])
        self.assertEqual(clarification["choices"][0]["client"], "Acme Plumbing")
        self.assertIn("Use the exact SmartHub client: Acme Plumbing",
                      clarification["choices"][0]["question"])

    def test_probable_match_replaces_only_client_argument(self):
        plan = {"calls": [{"tool": "get_client_ga4_summary", "arguments": {
            "client_name": "QAC", "property_id": "123", "start_date": "30daysAgo"}}]}
        match = {"status": "resolved", "requested": "QAC",
                 "client": "Quality Air Columbus", "confidence": "probable",
                 "matched_on": "abbreviation", "choices": []}
        with patch.object(ask_smarthub, "match_client", return_value=match):
            resolved, matches, clarification = ask_smarthub.resolve_plan_clients(
                plan, "Show QAC analytics")
        self.assertIsNone(clarification)
        self.assertEqual(resolved["calls"][0]["arguments"]["client_name"],
                         "Quality Air Columbus")
        self.assertEqual(resolved["calls"][0]["arguments"]["property_id"], "123")
        self.assertEqual(len(matches), 1)

    def test_client_search_falls_back_to_friendly_matching(self):
        match = {"status": "resolved", "requested": "QAC",
                 "client": "Quality Air Columbus", "confidence": "probable",
                 "matched_on": "abbreviation", "choices": []}
        identity = {"known": True, "client": "Quality Air Columbus",
                    "domain": "qualityaircolumbus.com"}
        with patch.object(ask_smarthub.v2_tools, "search_registry",
                          return_value={"query": "QAC", "count": 0, "clients": []}), \
             patch.object(ask_smarthub, "match_client", return_value=match), \
             patch.object(ask_smarthub.v2_tools, "resolve_identity", return_value=identity):
            result = ask_smarthub.friendly_client_search("QAC")
        self.assertEqual(result["clients"][0]["client"], "Quality Air Columbus")
        self.assertEqual(result["match_status"], "resolved")


class AskClientChoiceUiTests(unittest.TestCase):
    def test_template_renders_clickable_clarification_choices(self):
        template = Path("hub/templates/ask_smarthub.html").read_text()
        self.assertIn("clarification.choices", template)
        self.assertIn("ask(choice.question)", template)
        self.assertIn("ask-choice", template)


if __name__ == "__main__":
    unittest.main()
