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


class AskHelpTests(unittest.TestCase):
    """"How do I…" is answered from hub/help.py, which said so all along."""

    def test_help_tool_is_available_to_every_staff_role(self):
        self.assertIn("search_help", ask_smarthub.allowed_tools("member"))
        self.assertIn("search_help", ask_smarthub.allowed_tools("admin"))
        self.assertEqual(ask_smarthub.allowed_tools("demo"), {})

    def test_a_process_question_finds_its_screen_and_where_to_start(self):
        found = ask_smarthub.help_answers("how do I raise a web ticket")
        self.assertTrue(found["count"])
        top = found["topics"][0]
        self.assertEqual(top["title"], "Raise a web ticket")
        self.assertEqual(top["open"]["href"], "/tools/tickets/")
        self.assertIn("eight", top["explains"])

    def test_a_question_with_no_searchable_word_answers_nothing(self):
        """Better than an unrelated screen: the caller falls back to the
        honest summary of what it can read."""
        self.assertEqual(ask_smarthub.help_answers("what do you do")["count"], 0)
        self.assertEqual(ask_smarthub.help_answers("")["count"], 0)

    def test_two_letter_hub_words_survive_the_stopword_strip(self):
        self.assertEqual(ask_smarthub._help_terms("where do I ask for ad copy?"),
                         "ask ad copy")

    def test_next_steps_take_only_relative_links_from_help_reads(self):
        results = [
            {"tool": "search_help", "ok": True, "result": {"topics": [
                {"open": {"label": "Open Web Tickets", "href": "/tools/tickets/"}},
                {"open": {"label": "Elsewhere", "href": "https://evil.invalid"}},
                {"open": {"label": "Again", "href": "/tools/tickets/"}},
                {"open": None},
            ]}},
            {"tool": "get_client_proposals", "ok": True,
             "result": {"topics": [{"open": {"label": "No", "href": "/nope"}}]}},
            {"tool": "search_help", "ok": False, "error": "unavailable"},
        ]
        self.assertEqual(ask_smarthub.next_steps(results),
                         [{"label": "Open Web Tickets", "href": "/tools/tickets/"}])

    def test_capability_summary_is_the_allowlist_not_a_sentence(self):
        member = ask_smarthub.capability_summary("member")
        admin = ask_smarthub.capability_summary("admin")
        self.assertIn(ask_smarthub.TOOLS["get_client_proposals"].description, member)
        self.assertNotIn("QuickBooks balance", member)
        self.assertIn(ask_smarthub.TOOLS["get_client_quickbooks"].description, admin)
        self.assertIn("I only read", member)
        self.assertEqual(ask_smarthub.capability_summary("demo"),
                         "This account cannot read anything through Ask SmartHub.")

    def test_a_question_no_tool_could_answer_reaches_the_written_help(self):
        empty_plan = {"calls": [], "direct_answer": "Sure."}
        with patch.object(ask_smarthub, "plan", return_value=empty_plan), \
             patch.object(ask_smarthub, "answer", return_value="Here is how."), \
             patch.object(ask_smarthub.audit, "log"):
            out = ask_smarthub.ask("how do I raise a web ticket",
                                   role="member", actor="tester@example.test")
        self.assertEqual([row["tool"] for row in out["sources"]], ["search_help"])
        self.assertEqual(out["next_steps"][0]["href"], "/tools/tickets/")
        self.assertTrue(out["read_only"])

    def test_nothing_read_and_no_help_still_says_what_it_can_read(self):
        empty_plan = {"calls": [], "direct_answer": ""}
        with patch.object(ask_smarthub, "plan", return_value=empty_plan), \
             patch.object(ask_smarthub.audit, "log"):
            out = ask_smarthub.ask("what do you do", role="member",
                                   actor="tester@example.test")
        self.assertEqual(out["sources"], [])
        self.assertEqual(out["next_steps"], [])
        self.assertIn("What I can read:", out["answer"])


class AskGapReportTests(unittest.TestCase):
    """The questions it could read nothing for are already in the log."""

    ROWS = [
        {"time": "2026-09-16T10:00:00+00:00", "question": "Show me last month invoices",
         "source_count": 0, "actor": "rep@example.test"},
        {"time": "2026-09-15T09:00:00+00:00", "question": "show me last month INVOICES",
         "source_count": 0, "actor": "other@example.test"},
        {"time": "2026-09-14T09:00:00+00:00", "question": "Which GA4 properties for Acme",
         "source_count": 2, "tools": ["get_client_ga4_properties"]},
        {"time": "2026-09-13T09:00:00+00:00", "question": "proposals for acme",
         "source_count": 0, "match_status": "clarification"},
        {"time": "2026-09-12T09:00:00+00:00", "question": "Who owns this client",
         "source_count": 0, "actor": "rep@example.test"},
        {"time": "2026-09-11T09:00:00+00:00", "question": "   ", "source_count": 0},
    ]

    def test_only_the_unanswered_are_reported_and_alike_ones_are_one_row(self):
        from hub import qa, audit
        with patch.object(audit, "read", return_value=self.ROWS):
            out = qa.ask_gaps()
        self.assertTrue(out["measured"])
        questions = [row[0] for row in out["rows"]]
        self.assertEqual(questions, ["Show me last month invoices",
                                     "Who owns this client"])
        self.assertEqual(out["rows"][0][1], 2)                  # asked twice
        self.assertEqual(out["rows"][0][2], "2026-09-16 10:00")  # newest of the two
        self.assertEqual(out["rows"][0][3], "rep@example.test")

    def test_an_unreadable_log_is_unmeasured_rather_than_empty(self):
        from hub import qa, audit
        with patch.object(audit, "read", side_effect=RuntimeError("no table")):
            out = qa.ask_gaps()
        self.assertFalse(out["measured"])
        self.assertIn("could not be read", out["note"])

    def test_the_report_is_on_the_qa_index(self):
        from hub import qa
        self.assertEqual(qa.REPORTS["ask-gaps"]["fn"], qa.ask_gaps)
        self.assertTrue(qa.REPORTS["ask-gaps"]["group"])


class AskClientChoiceUiTests(unittest.TestCase):
    def test_template_renders_clickable_clarification_choices(self):
        template = Path("hub/templates/ask_smarthub.html").read_text()
        self.assertIn("clarification.choices", template)
        self.assertIn("ask(choice.question)", template)
        self.assertIn("ask-choice", template)

    def test_template_renders_where_to_start_links(self):
        """The answer is escaped text, so a path inside it is not clickable.

        These are, and the page refuses anything that is not a Hub path --
        the same rule the server applies, on the side that would render it.
        """
        template = Path("hub/templates/ask_smarthub.html").read_text()
        self.assertIn("d.next_steps", template)
        self.assertIn("Where to start", template)
        self.assertIn("step.href.charAt(0)!=='/'", template)


if __name__ == "__main__":
    unittest.main()
