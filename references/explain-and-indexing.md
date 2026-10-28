# EXPLAIN And Index Review

Read this reference when the task includes a plan, schema, or index proposal.

## Evidence to request

- OceanBase version and MySQL-mode `sql_mode`.
- `SHOW CREATE TABLE` for every joined or filtered table.
- Approximate table size, distinct counts for leading candidate columns, and
  representative parameter values.
- The exact SQL, not a paraphrased version.
- `EXPLAIN` for the original query and each proposed rewrite. If a runtime-plan
  facility is available in the deployment, use it only for a read-only query.

## Plan signals

Prioritize the operator that explains measured latency or estimated work:

| Signal | Investigate |
| --- | --- |
| Full table scan with a selective predicate | Missing/unusable index, implicit conversion, function wrapping, poor leading column |
| Range scan producing many rows | Predicate selectivity, stale statistics, or a range that is broader than expected |
| Huge estimate/actual mismatch | Stale or insufficient statistics; do not tune around a bad estimate first |
| Sort or temporary materialization | Ordering/index mismatch, large `IN`, aggregation, or unstable pagination |
| Repeated scans of one side | Join order, join predicate, or missing join-key index |
| Exchange/network operator dominating | Bottleneck is likely row movement or partition/plan shape, not a simple local index |

Do not call a scan "bad" solely because its label contains `TABLE`. A large scan
may be optimal for a large fraction of the table.

## Index candidate rules

1. Start from stable, selective predicates. Put equality columns before a range
   column for a single composite candidate.
2. Consider the final `ORDER BY` only when it preserves the query's required
   ordering and does not conflict with more selective access.
3. Include columns needed for covering only when they remove meaningful row
   lookups and do not make the index excessively wide.
4. Check leftmost-prefix consequences. An index on `(tenant_id, created_at)` does
   not independently accelerate a query filtered only by `created_at`.
5. Respect collation, type, and implicit-conversion rules. A function or type
   mismatch can make an otherwise correct index unusable.
6. Evaluate write amplification, storage, cache pressure, and overlap with
   existing indexes. Removing or merging a redundant tested index may be more
   valuable than adding another one.
7. Use a prefix index or functional/generated-column strategy only after
   confirming support in the deployed version and proving it with `EXPLAIN`.

## Safe verification

- Compare plans with the same SQL mode, schema, statistics, and parameters.
- Check estimates and the chosen access method before checking wall-clock time.
- Run read-only benchmarks on representative data; avoid production mutation.
- Stage index changes with a rollback plan and monitor write latency, lock waits,
  storage growth, and plan regressions.
- Re-run statistics collection when the plan depends on stale distributions.

## Review output template

```text
Verdict:
  Correctness risk: ...
  Performance risk: ...
  Confidence: ...

Plan evidence:
  - Current access: ...
  - Dominant operator: ...
  - Estimate issue: ...

Recommended changes:
  1. [P0] ...
     Evidence: ...
     Tradeoff: ...
  2. [P1] ...

Verification:
  - EXPLAIN comparison: ...
  - Representative parameters: ...
  - Rollback/monitoring: ...
```
