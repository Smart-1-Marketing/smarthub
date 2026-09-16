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
        dropped by `execute()`, so the read runs with a filter nobody
        applied -- and reports success."""
        for name, tool in ask_smarthub.TOOLS.items():
            try:
                params = set(inspect.signature(tool.fn).parameters)
            except (TypeError, ValueError):       # a lambda adapter
                continue
            with self.subTest(tool=name):
                self.assertEqual(
                    set(tool.arguments) - params, set(),
                    f"{name} declares arguments its function cannot take")
                self.assertEqual(
                    params - set(tool.arguments), set(),
                    f"{name} has parameters the planner can never send")

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


if __name__ == "__main__":
    unittest.main()
