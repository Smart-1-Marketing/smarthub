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

    def test_sales_view_can_read_quickbooks_without_widening_other_roles(self):
        self.assertIn("get_client_quickbooks",
                      ask_smarthub.allowed_tools("member", ["sales"]))
        self.assertNotIn("get_client_quickbooks",
                         ask_smarthub.allowed_tools("member", ["client-success"]))

    def test_every_staff_account_can_use_navigation_assistant(self):
        self.assertIn("find_in_smarthub", ask_smarthub.allowed_tools("member"))

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

    def test_navigation_returns_only_verified_local_links(self):
        results = {"results": [
            {"kind": "tool", "title": "Proposal Builder", "subtitle": "Start here",
             "url": "/sales/builder/"},
            {"kind": "tool", "title": "Bad", "url": "https://evil.invalid"},
            {"kind": "tool", "title": "Also bad", "url": "//evil.invalid"},
        ], "note": ""}
        with patch("hub.search_index.search", return_value=results):
            out = ask_smarthub.find_in_smarthub("proposal")
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["results"][0]["url"], "/sales/builder/")

    def test_navigation_retries_meaningful_terms_from_a_natural_question(self):
        empty = {"results": [], "note": "Nothing", "errors": []}
        proposal = {"results": [{"kind": "tool", "title": "Proposal Builder",
                                  "subtitle": "", "url": "/sales/builder/"}],
                    "note": "", "errors": []}
        def search(query, limit=8):
            return proposal if query == "proposal" else empty
        with patch("hub.search_index.search", side_effect=search):
            out = ask_smarthub.find_in_smarthub("Where do I start a proposal?")
        self.assertEqual(out["results"][0]["title"], "Proposal Builder")

    def test_navigation_suggests_a_close_tool_name_without_inventing_a_link(self):
        empty = {"results": [], "note": "Nothing", "errors": []}
        pages = [{"kind": "tool", "title": "Proposal Builder", "subtitle": "",
                  "url": "/sales/builder/", "_title": "proposal builder"}]
        with patch("hub.search_index.search", return_value=empty), \
             patch("hub.search_index.pages", return_value=pages):
            out = ask_smarthub.find_in_smarthub("proposel")
        self.assertEqual(out["results"][0]["url"], "/sales/builder/")

    def test_response_links_deduplicate_and_recheck_urls(self):
        rows = [{"tool": "find_in_smarthub", "ok": True, "result": {"results": [
            {"title": "Reports", "url": "/qa"},
            {"title": "Reports again", "url": "/qa"},
            {"title": "Unsafe", "url": "javascript:alert(1)"},
        ]}}]
        self.assertEqual(ask_smarthub.response_links(rows), [
            {"title": "Reports", "subtitle": "", "kind": "", "url": "/qa"}])


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

    def test_assistant_ui_is_navigation_first_and_uses_sidebar_icon(self):
        template = Path("hub/templates/ask_smarthub.html").read_text()
        widget = Path("hub/static/ask-smarthub-widget.js").read_text()
        self.assertNotIn("Read-only V1", template)
        self.assertNotIn("Check QuickBooks connection status", template)
        self.assertIn("Where do I start a proposal?", template)
        self.assertIn("d.links", template)
        self.assertIn("✨", template)
        self.assertIn("✨ Ask SmartHub", widget)

    def test_diagnostics_surfaces_logged_questions(self):
        template = Path("hub/templates/diagnostics.html").read_text()
        self.assertIn("Ask SmartHub &mdash; what people need", template)
        self.assertIn("module=ask_smarthub", template)
        self.assertIn("e.question", template)


class AskDiagnosticsTests(unittest.TestCase):
    def test_question_log_keeps_evaluation_fields(self):
        with patch.object(ask_smarthub.audit, "log") as log:
            ask_smarthub._log_question(
                actor="sales@example.com", role="member",
                question="Where do I start a proposal?", context={"path": "/", "client": ""},
                outcome="answered", tools=["find_in_smarthub"], link_count=1,
                views=["sales"])
        fields = log.call_args.kwargs
        self.assertEqual(fields["question"], "Where do I start a proposal?")
        self.assertEqual(fields["outcome"], "answered")
        self.assertEqual(fields["access_views"], ["sales"])


if __name__ == "__main__":
    unittest.main()
