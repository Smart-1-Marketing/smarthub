"""Boundaries for the read-only Ask SmartHub planner and executor."""
import inspect
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hub import ask_smarthub
from mcp_gateway import v2_tools


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

    def test_a_typo_is_offered_rather_than_assumed(self):
        """A one-letter typo asks now, and it is one tap to answer.

        This used to resolve itself, on a second scorer whose 0.90 was not
        the 0.90 company_identity.py means by it. On the Hub's one scale
        "Monagram Homes" scores 0.74 against "Monogram Homes" -- the tokens
        differ, and that scorer is deliberately hard on a changed token
        because the module it was written for *writes* aliases. So the
        candidate comes back as a choice instead of an assumption, which is
        the house rule ("never guess ownership from a partial name") applied
        to the assistant. What it costs is one tap; what it buys is that no
        answer is ever quietly about a different client.
        """
        index = self._index("Monogram Homes", "Quality Air Columbus")
        unresolved = {"known": False, "client": "Monagram Homes", "candidates": []}
        with patch.object(ask_smarthub.v2_tools, "resolve_identity", return_value=unresolved), \
             patch.object(ask_smarthub.v2_tools.hub_client_key, "alias_index", return_value=index):
            result = ask_smarthub.match_client("Monagram Homes")
        self.assertEqual(result["status"], "clarify")
        self.assertEqual(result["choices"][0]["client"], "Monogram Homes")

    def test_the_scale_and_the_rule_are_the_hubs_own(self):
        """No second opinion about whether two names are one company."""
        from hub import company_identity
        score, reason, evidence = ask_smarthub._similarity(
            "Monagram Homes", "Monogram Homes")
        self.assertEqual(score, company_identity.name_score(
            "Monagram Homes", "Monogram Homes"))
        self.assertEqual(reason, "fuzzy")
        self.assertEqual(evidence, [])
        self.assertFalse(hasattr(ask_smarthub, "_AUTO_MATCH_SCORE"),
                         "a second set of thresholds has grown back")
        # An exactly normalised name is evidence company_identity weighs;
        # an abbreviation is this file's own reading and says so.
        self.assertEqual(ask_smarthub._similarity("Acme Ltd", "Acme"),
                         (1.0, "exact", ["normalized-name"]))
        self.assertEqual(ask_smarthub._similarity("QAC", "Quality Air Columbus"),
                         (1.0, "abbreviation", ["abbreviation"]))

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


class AskOwnWorkTests(unittest.TestCase):
    """"My clients" is whoever signed in, and no question can change that."""

    def test_no_tool_lets_the_plan_name_whose_work_to_read(self):
        for name, tool in ask_smarthub.TOOLS.items():
            for forbidden in ("actor", "email", "owner", "user"):
                self.assertNotIn(forbidden, tool.arguments,
                                 f"{name} would let a question choose whose work is read")

    def test_the_actor_comes_from_the_session_not_the_question(self):
        seen = {}
        tool = ask_smarthub.Tool("test", ("member",),
                                 lambda **kw: seen.update(kw) or {"ok": True},
                                 (), needs_actor=True)
        with patch.dict(ask_smarthub.TOOLS, {"mine": tool}, clear=True):
            out = ask_smarthub.execute(
                {"calls": [{"tool": "mine", "arguments": {
                    "actor": "someone.else@example.test",
                    "owner": "someone.else@example.test"}}]},
                "member", actor="me@example.test")
        self.assertEqual(seen, {"actor": "me@example.test"})
        self.assertTrue(out[0]["ok"])

    def test_a_session_with_no_account_is_told_so_rather_than_shown_everybody(self):
        out = ask_smarthub.my_clients("Shared login")
        self.assertFalse(out["measured"])
        self.assertIn("no account behind it", out["message"])
        self.assertEqual(out["opens"]["href"], "/my-clients")

    def test_my_clients_reads_the_same_run_the_page_draws(self):
        board = {"measured": True, "clients": 12, "with_issues": 3, "issues": 7,
                 "billing_monthly": 41000.0,
                 "top": [{"client": "Quality Air Columbus", "issues": 4,
                          "url": "/my-clients?client=Quality+Air+Columbus"}]}
        with patch.dict("sys.modules"), \
             patch("hub.client_health.scoreboard", return_value=board) as board_fn, \
             patch("hub.client_owner.clients_for", return_value=["Quality Air Columbus"]):
            out = ask_smarthub.my_clients("rep@example.test")
        board_fn.assert_called_once_with(owner="rep@example.test")
        self.assertTrue(out["measured"])
        self.assertEqual(out["outstanding_items"], 7)
        self.assertEqual(out["most_outstanding_first"][0]["client"],
                         "Quality Air Columbus")
        self.assertNotIn("message", out)

    def test_nobody_assigned_reads_differently_from_nothing_outstanding(self):
        board = {"measured": True, "clients": 0, "with_issues": 0, "issues": 0,
                 "top": []}
        with patch("hub.client_health.scoreboard", return_value=board), \
             patch("hub.client_owner.clients_for", return_value=[]):
            out = ask_smarthub.my_clients("rep@example.test")
        self.assertIn("No client is assigned to you", out["message"])
        self.assertEqual(out["opens"]["href"], "/qa/client-owners")

    def test_an_unreadable_book_is_not_a_quiet_zero(self):
        with patch("hub.client_health.scoreboard",
                   side_effect=RuntimeError("no table")):
            out = ask_smarthub.my_clients("rep@example.test")
        self.assertFalse(out["measured"])
        self.assertIn("RuntimeError", out["message"])

    def test_qa_tasks_are_summarized_without_internal_ids(self):
        data = {"measured": True, "error": "", "line": "Two waiting on you.",
                "counts": {"to_do": 2, "overdue": 1},
                "to_do": [{"id": 41, "target_label": "Client 360",
                           "instructions": "Check the spend card",
                           "status_label": "Open", "due_on_pretty": "Sep 18",
                           "overdue": True, "created_by_name": "Todd",
                           "assigned_to_email": "rep@example.test"}],
                "waiting_on_you": []}
        with patch("hub.qa_tasks.for_person", return_value=data):
            out = ask_smarthub.my_qa_tasks("rep@example.test")
        row = out["waiting_on_you_to_do"][0]
        self.assertEqual(row["task"], "Client 360")
        self.assertTrue(row["overdue"])
        self.assertNotIn("id", row)
        self.assertEqual(out["opens"]["href"], "/qa-tasks")

    def test_an_unmeasured_next_action_says_so_rather_than_all_clear(self):
        with patch("hub.next_action.for_client",
                   return_value={"measured": False, "text": "", "state": "ok",
                                 "go": "", "href": ""}):
            out = ask_smarthub.client_next_action("Acme")
        self.assertFalse(out["measured"])
        self.assertIn("not the same as nothing being outstanding", out["message"])
        self.assertNotIn("opens", out)

    def test_a_next_action_offers_the_record_it_points_at(self):
        with patch("hub.next_action.for_client",
                   return_value={"measured": True, "text": "Quote expires Friday.",
                                 "state": "bad", "go": "Open the proposal",
                                 "href": "/client360?q=Acme"}):
            out = ask_smarthub.client_next_action("Acme")
        self.assertEqual(out["opens"], {"label": "Open the proposal",
                                        "href": "/client360?q=Acme"})
        self.assertEqual(ask_smarthub.next_steps(
            [{"tool": "get_client_next_action", "ok": True, "result": out}]),
            [{"label": "Open the proposal", "href": "/client360?q=Acme"}])

    def test_work_keeps_the_group_member_its_work_was_done_for(self):
        log = {"count": 2, "last_activity": "2026-09-16T10:00:00+00:00",
               "by_source": {"Creative Studio": 2},
               "items": [{"when": "2026-09-16T10:00:00+00:00", "kind": "Banner set",
                          "source": "Creative Studio", "module": "creative_studio",
                          "member": "Fast Fingerprints"}],
               "note": "Assembled from the activity log."}
        with patch("hub.client_groups.member_names", return_value=["Fast Fingerprints"]), \
             patch("hub.client_brand.work_log", return_value=log) as work:
            out = ask_smarthub.client_work("National Background Check", limit=5)
        work.assert_called_once_with("National Background Check", 5,
                                     also=["Fast Fingerprints"])
        self.assertEqual(out["items"][0]["for_group_member"], "Fast Fingerprints")
        self.assertNotIn("module", out["items"][0])

    def test_no_launch_blockers_says_which_kind_of_empty_it_is(self):
        with patch("hub.client_brief.build", return_value={}), \
             patch("hub.launch_blockers.find", return_value=[]):
            out = ask_smarthub.client_launch_blockers("Acme")
        self.assertEqual(out["count"], 0)
        self.assertIn("nobody has scanned also reports nothing", out["note"])


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
        self.assertIn("ask-choice", template)
        # Picking a client re-asks THAT choice's question, and carries the
        # recipe with it: the person answered "which client", not "never
        # mind the table I asked for".
        self.assertIn("ask(choice.question,recipeKey)", template)


class CatalogAgreementTests(unittest.TestCase):
    """The two catalogs a V2 read tool has to appear in, kept in step.

    A tool lives in `mcp_gateway/v2_tools.py` and is declared twice: in
    `register()` for the MCP server, and in `ask_smarthub.TOOLS` for the
    planner. Adding it to one and not the other passes every test that only
    looks at one of them -- which is exactly how this branch shipped two
    tools that `mcp_gateway/test_v2.py`'s closed set then failed on.
    """

    def _registered(self):
        class FakeMCP:
            def __init__(self):
                self.tools = {}

            def tool(self, **kw):
                def wrap(fn):
                    self.tools[fn.__name__] = fn
                    return fn
                return wrap

        fake = FakeMCP()
        v2_tools.register(fake)
        return fake.tools

    def test_every_v2_backed_planner_tool_is_registered_with_mcp(self):
        registered = self._registered()
        for name, tool in ask_smarthub.TOOLS.items():
            if "v2_tools" not in str(getattr(tool.fn, "__module__", "")):
                continue
            with self.subTest(tool=name):
                self.assertIn(
                    name, registered,
                    f"{name} is in the planner's catalog but not in "
                    f"v2_tools.register(); an MCP client cannot reach it.")

    def test_every_declared_argument_is_one_the_function_accepts(self):
        """A planner argument the function has no parameter for is silently
        dropped by `execute()`, so the read runs with a filter nobody applied
        -- and reports success.

        `actor` is the deliberate exception on a `needs_actor` tool: it is
        injected from the session AFTER the argument filter, so the planner
        cannot reach it. That is the point of those tools, not a gap.
        """
        for name, tool in ask_smarthub.TOOLS.items():
            try:
                params = set(inspect.signature(tool.fn).parameters)
            except (TypeError, ValueError):       # a lambda adapter
                continue
            supplied = {"actor"} if getattr(tool, "needs_actor", False) else set()
            with self.subTest(tool=name):
                self.assertEqual(
                    set(tool.arguments) - params, set(),
                    f"{name} declares arguments its function cannot take")
                self.assertEqual(
                    params - set(tool.arguments) - supplied, set(),
                    f"{name} has parameters the planner can never send")

    def test_the_planner_can_never_supply_whose_desk_is_read(self):
        """`actor` decides whose work a "my clients" read returns, and it is
        settled at sign-in. A tool that let the planner pass it would let a
        question -- or a sentence inside a client's own record -- ask on
        somebody else's behalf."""
        for name, tool in ask_smarthub.TOOLS.items():
            with self.subTest(tool=name):
                self.assertNotIn(
                    "actor", tool.arguments,
                    f"{name} lets the planner supply the actor")
        source = Path("hub/ask_smarthub.py").read_text()
        # Injected after the filter that keeps only declared arguments.
        self.assertIn('arguments["actor"] = actor', source)
        self.assertLess(
            source.index("arguments = {key: incoming[key]"),
            source.index('arguments["actor"] = actor'),
            "actor is injected before the argument filter, so the planner "
            "could overwrite it")

    def test_no_tool_is_reachable_by_a_role_outside_the_catalog(self):
        for role in ("client", "", "anonymous", "viewer"):
            with self.subTest(role=role):
                self.assertEqual(ask_smarthub.allowed_tools(role), {})


class AskRecipeUiTests(unittest.TestCase):
    def test_chips_come_from_the_recipe_library_not_the_template(self):
        template = Path("hub/templates/ask_smarthub.html").read_text()
        self.assertIn("recipe_groups", template)
        self.assertIn("data-recipe=", template)
        # The chip asks its own filled-in question rather than its label.
        self.assertIn("data-question", template)

    def test_the_recipe_key_is_sent_with_the_question(self):
        template = Path("hub/templates/ask_smarthub.html").read_text()
        self.assertIn("recipe:recipeKey", template)

    def test_a_recipe_never_widens_the_tool_catalog(self):
        from hub import ask_recipes
        for recipe in ask_recipes.RECIPES:
            for role in recipe.roles:
                reachable = ask_smarthub.allowed_tools(role)
                for tool in recipe.tools:
                    self.assertIn(tool, reachable, f"{recipe.key} / {role}")

    def test_a_role_outside_a_recipe_gets_neither_hint_nor_guidance(self):
        from hub import ask_recipes
        self.assertEqual(ask_recipes.tool_hint("performance_summary", "client"), ())
        self.assertEqual(ask_recipes.render_for("performance_summary", "client"), "")

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
