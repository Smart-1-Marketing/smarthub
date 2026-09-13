"""Boundaries for the read-only Ask SmartHub planner and executor."""
import unittest
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


if __name__ == "__main__":
    unittest.main()
