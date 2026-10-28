# OceanBase SQL Review Skill

A dependency-free Skill for evidence-based review of OceanBase MySQL-mode SQL.
It separates correctness risks from performance signals, includes a static SQL
audit helper, and guides index proposals using schema, statistics, and `EXPLAIN`
evidence rather than column-name guesses.

## Quick start

```bash
python3 scripts/ob_sql_audit.py examples/risky.sql
python3 -m unittest discover -s tests -v
```

The Skill entrypoint is [`SKILL.md`](SKILL.md). The audit helper is intentionally
static: it never connects to a database or executes the reviewed SQL.
