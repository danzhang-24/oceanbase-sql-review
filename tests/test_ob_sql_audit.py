from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "ob_sql_audit.py"
SPEC = importlib.util.spec_from_file_location("ob_sql_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def rules(sql: str) -> set[str]:
    return {finding.rule for finding in AUDIT.audit_sql(sql)}


class SQLAuditTests(unittest.TestCase):
    def test_safe_query_has_no_findings(self) -> None:
        self.assertEqual(
            rules(
                "SELECT id, total FROM orders "
                "WHERE tenant_id = 7 AND created_at >= '2026-01-01' "
                "ORDER BY created_at DESC LIMIT 50"
            ),
            set(),
        )

    def test_full_table_mutation_is_an_error(self) -> None:
        findings = AUDIT.audit_sql("UPDATE orders SET state = 'x'")
        self.assertEqual(
            [(item.rule, item.severity) for item in findings],
            [("mutation-without-where", "error")],
        )

    def test_comments_and_strings_do_not_create_false_matches(self) -> None:
        sql = """
        -- UPDATE orders SET state = 'bad'
        SELECT note
        FROM orders
        WHERE note = 'DELETE FROM orders'
        """
        self.assertEqual(rules(sql), set())

    def test_scoped_update_and_delete_are_not_reported(self) -> None:
        self.assertEqual(
            rules(
                "UPDATE orders SET state = 'x' WHERE id = 9;"
                "DELETE FROM orders WHERE tenant_id = 2 AND expires_at < NOW()"
            ),
            set(),
        )

    def test_static_review_signals_are_reported(self) -> None:
        sql = """
        SELECT *
        FROM orders o
        JOIN customers c
        WHERE LOWER(o.email) = 'a@example.com'
          AND o.note LIKE '%urgent'
          AND o.customer_id NOT IN (SELECT id FROM blocked)
        ORDER BY RAND()
        LIMIT 1000, 20;
        """
        self.assertLessEqual(
            {
                "select-star",
                "join-without-predicate",
                "function-on-predicate",
                "leading-wildcard-like",
                "not-in-subquery",
                "order-by-random",
                "large-pagination-offset",
            },
            rules(sql),
        )

    def test_right_hand_function_wrapper_is_reported(self) -> None:
        self.assertIn(
            "function-on-predicate",
            rules("SELECT id FROM orders WHERE id = CAST('42' AS UNSIGNED)"),
        )

    def test_large_in_list_is_reported(self) -> None:
        values = ",".join(str(index) for index in range(101))
        self.assertIn(
            "large-in-list",
            rules(f"SELECT id FROM orders WHERE id IN ({values})"),
        )

    def test_statement_numbers_survive_splitting(self) -> None:
        findings = AUDIT.audit_sql(
            "SELECT id FROM orders WHERE id = 1;\n"
            "DELETE FROM orders;"
        )
        self.assertEqual(
            [(finding.rule, finding.statement) for finding in findings],
            [("mutation-without-where", 2)],
        )

    def test_cross_join_is_not_reported_as_missing_join_predicate(self) -> None:
        self.assertNotIn(
            "join-without-predicate",
            rules("SELECT a.id, b.id FROM a CROSS JOIN b"),
        )

    def test_json_cli_and_exit_code(self) -> None:
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "--json", "--fail-on", "warning"],
            input="SELECT * FROM orders",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 1)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["source"], "<stdin>")
        self.assertEqual(payload["findings"][0]["rule"], "select-star")

    def test_usage_error_has_distinct_exit_code(self) -> None:
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "/path/that/does/not/exist.sql"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("No such file", process.stderr)


if __name__ == "__main__":
    unittest.main()
