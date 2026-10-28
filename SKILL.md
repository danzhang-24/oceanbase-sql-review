---
name: oceanbase-sql-review
description: Review OceanBase MySQL-mode SQL for risky data changes, inefficient access patterns, and justified index changes; use when auditing SQL, tuning a query, or reviewing schema/index proposals.
---

# OceanBase SQL Review

Review the SQL and its execution evidence before recommending an index or
rewrite. Preserve correctness first, then reduce scan/sort work, then consider
index maintenance cost.

## Workflow

1. Record the OceanBase version, MySQL mode, relevant `SHOW CREATE TABLE`
   output, approximate row counts, and the original SQL. Do not guess column
   types, cardinality, or collation.
2. Run `scripts/ob_sql_audit.py` on the SQL. Treat its findings as hypotheses,
   not proof of a performance problem.
3. For tuning work, inspect `EXPLAIN` output. Use a runtime plan tool only when
   the deployment supports it and the statement is read-only. Read
   [references/explain-and-indexing.md](references/explain-and-indexing.md) for
   plan signals and index design rules.
4. Report correctness risks separately from performance opportunities. For
   every index proposal, name the predicates/order it serves, estimate
   selectivity, state the leftmost-prefix behavior, and mention write/storage
   cost.
5. Provide a verification plan using representative parameters and a
   read-only `EXPLAIN` before any production change.

## Safety and evidence rules

- Never execute DDL, DML, or a proposed index change merely to inspect it.
- Do not infer an index is useful from column names alone. Use real predicates,
  join keys, ordering, and measured or estimated cardinality.
- Do not recommend adding every filtered column to an index. Prefer the
  smallest index that serves the dominant equality/range/order pattern.
- Treat `SELECT *`, large offsets, leading-wildcard `LIKE`, and functions over
  indexed columns as review signals. They become findings only when the query
  context and plan support that conclusion.
- Do not rewrite a query in a way that changes NULL semantics, duplicate-row
  behavior, locking, isolation, or collation.
- If schema or plan evidence is unavailable, label the answer as static review
  and list the exact evidence needed for a final recommendation.

## Audit helper

From this Skill directory:

```bash
python3 scripts/ob_sql_audit.py path/to/query.sql
cat query.sql | python3 scripts/ob_sql_audit.py
python3 scripts/ob_sql_audit.py --json --fail-on error query.sql
```

The helper is dependency-free and performs only static analysis. Exit code `0`
means no finding reached the selected threshold, `1` means the threshold was
reached, and `2` means input/usage failed.

## Response format

Use these sections when the request is a review:

1. **Verdict** — correctness risk, performance risk, and confidence.
2. **Correctness findings** — data-change scope and semantic hazards first.
3. **Plan evidence** — observed scans, estimate errors, exchanges, sorts, and
   the dominant cost.
4. **Recommended changes** — query rewrites and index candidates in priority
   order, with tradeoffs.
5. **Verification** — `EXPLAIN` comparison, representative parameters, and
   rollback/monitoring notes for index changes.

Keep speculative advice explicitly labeled. Do not claim an improvement that
was not observed or clearly derived from the supplied plan/statistics.
