#!/usr/bin/env python3
"""Dependency-free static review for OceanBase MySQL-mode SQL."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    column: int
    depth: int = 0


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    line: int
    column: int
    message: str
    suggestion: str
    statement: int


_CLAUSE_WORDS = {
    "where",
    "group",
    "having",
    "order",
    "limit",
    "union",
    "except",
    "intersect",
    "window",
    "returning",
}
_SCALAR_FUNCTIONS = {
    "cast",
    "coalesce",
    "date",
    "day",
    "ifnull",
    "lower",
    "month",
    "substr",
    "substring",
    "to_char",
    "trim",
    "unix_timestamp",
    "upper",
    "year",
}
_COMPARISONS = {"=", "<", ">", "<=", ">=", "<>", "!="}
_JOIN_TERMS = {"join"}
_JOIN_BOUNDARIES = _CLAUSE_WORDS | {"join", "on", "using"}


def tokenize(sql: str) -> tuple[Token, ...]:
    """Lex SQL while ignoring comments and preserving source positions."""

    tokens: list[Token] = []
    index = 0
    line = 1
    column = 1
    length = len(sql)

    def advance(text: str) -> None:
        nonlocal line, column
        newlines = text.count("\n")
        if newlines:
            line += newlines
            column = len(text.rsplit("\n", 1)[-1]) + 1
        else:
            column += len(text)

    while index < length:
        character = sql[index]
        if character.isspace():
            start = index
            while index < length and sql[index].isspace():
                index += 1
            advance(sql[start:index])
            continue
        if sql.startswith("--", index) and (
            index + 2 == length or sql[index + 2].isspace()
        ):
            start = index
            end = sql.find("\n", index)
            index = length if end < 0 else end
            advance(sql[start:index])
            continue
        if character == "#":
            start = index
            end = sql.find("\n", index)
            index = length if end < 0 else end
            advance(sql[start:index])
            continue
        if sql.startswith("/*", index):
            start = index
            end = sql.find("*/", index + 2)
            if end < 0:
                raise ValueError(f"unterminated block comment at line {line}, column {column}")
            index = end + 2
            advance(sql[start:index])
            continue
        if character in {"'", '"', "`"}:
            quote = character
            token_line, token_column = line, column
            start = index
            index += 1
            while index < length:
                if sql[index] == "\\" and quote != "`":
                    index += min(2, length - index)
                    continue
                if sql[index] == quote:
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise ValueError(f"unterminated quoted value at line {token_line}, column {token_column}")
            text = sql[start:index]
            advance(text)
            tokens.append(
                Token("string" if quote == "'" else "identifier", text, token_line, token_column)
            )
            continue
        if character.isalpha() or character in {"_", "$"}:
            token_line, token_column = line, column
            start = index
            index += 1
            while index < length and (sql[index].isalnum() or sql[index] in {"_", "$"}):
                index += 1
            text = sql[start:index]
            advance(text)
            tokens.append(Token("word", text, token_line, token_column))
            continue
        if character.isdigit():
            token_line, token_column = line, column
            start = index
            index += 1
            while index < length and (sql[index].isdigit() or sql[index] == "."):
                index += 1
            text = sql[start:index]
            advance(text)
            tokens.append(Token("number", text, token_line, token_column))
            continue
        token_line, token_column = line, column
        index += 1
        advance(character)
        tokens.append(Token("symbol", character, token_line, token_column))

    depth = 0
    positioned: list[Token] = []
    for token in tokens:
        if token.value == ")":
            depth = max(0, depth - 1)
        positioned.append(Token(token.kind, token.value, token.line, token.column, depth))
        if token.value == "(":
            depth += 1
    return tuple(positioned)


def split_statements(tokens: Sequence[Token]) -> tuple[tuple[Token, ...], ...]:
    statements: list[tuple[Token, ...]] = []
    current: list[Token] = []
    for token in tokens:
        if token.value == ";" and token.depth == 0:
            if current:
                statements.append(tuple(current))
                current = []
            continue
        current.append(token)
    if current:
        statements.append(tuple(current))
    return tuple(statements)


def audit_sql(sql: str) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for statement_number, statement in enumerate(split_statements(tokenize(sql)), start=1):
        findings.extend(_audit_statement(statement, statement_number))
    return tuple(sorted(findings, key=lambda item: (item.line, item.column, item.rule)))


def _audit_statement(tokens: tuple[Token, ...], statement_number: int) -> list[Finding]:
    findings: list[Finding] = []
    lowered = [token.value.casefold() for token in tokens]

    def add(
        rule: str,
        severity: str,
        token: Token,
        message: str,
        suggestion: str,
    ) -> None:
        findings.append(
            Finding(
                rule=rule,
                severity=severity,
                line=token.line,
                column=token.column,
                message=message,
                suggestion=suggestion,
                statement=statement_number,
            )
        )

    for index, token in enumerate(tokens):
        word = lowered[index]
        if token.kind == "word" and token.depth == 0 and word in {"update", "delete"}:
            if not any(
                candidate.kind == "word"
                and candidate.depth == 0
                and lowered[pos] == "where"
                for pos, candidate in enumerate(tokens[index + 1 :], start=index + 1)
            ):
                add(
                    "mutation-without-where",
                    "error",
                    token,
                    f"{word.upper()} has no top-level WHERE clause.",
                    "Add a verified predicate or obtain explicit authorization for a full-table mutation.",
                )

        if word == "select":
            level = token.depth
            for candidate, candidate_word in zip(tokens[index + 1 :], lowered[index + 1 :]):
                if candidate.depth == level and candidate_word == "from":
                    break
                if candidate.depth == level and candidate.value == "*":
                    add(
                        "select-star",
                        "warning",
                        candidate,
                        "SELECT * increases transfer, decoding, and covering-index cost.",
                        "List only required columns; keep the projection stable for APIs.",
                    )
                    break

        if word == "like" and index + 1 < len(tokens):
            literal = tokens[index + 1]
            if literal.kind == "string":
                value = literal.value[1:-1]
                if value.startswith(("%", "_")):
                    add(
                        "leading-wildcard-like",
                        "warning",
                        literal,
                        "A leading wildcard generally prevents an ordinary B-tree range seek.",
                        "Use a prefix range, normalized search column, or a verified full-text index.",
                    )

        if word == "not" and index + 2 < len(tokens):
            if lowered[index + 1] == "in" and tokens[index + 2].value == "(":
                next_words = lowered[index + 3 : index + 6]
                if next_words and next_words[0] == "select":
                    add(
                        "not-in-subquery",
                        "warning",
                        token,
                        "NOT IN can produce unexpected empty results when the subquery returns NULL.",
                        "Use NOT EXISTS with an explicit correlation or prove the subquery column is NOT NULL.",
                    )

        if word == "join" and token.depth == 0:
            previous = lowered[index - 1] if index else ""
            previous_previous = lowered[index - 2] if index > 1 else ""
            if previous not in {"cross", "natural"} and "cross" not in {previous, previous_previous}:
                has_predicate = False
                for candidate, candidate_word in zip(tokens[index + 1 :], lowered[index + 1 :]):
                    if candidate.depth == 0 and candidate_word in _JOIN_BOUNDARIES:
                        has_predicate = candidate_word in {"on", "using"}
                        break
                if not has_predicate:
                    add(
                        "join-without-predicate",
                        "warning",
                        token,
                        "JOIN has no visible ON/USING predicate before the next clause.",
                        "Add the intended key relationship or use CROSS JOIN to make a product explicit.",
                    )

        if token.kind == "word" and word in _SCALAR_FUNCTIONS and index + 1 < len(tokens):
            if tokens[index + 1].value == "(":
                close_index = _matching_close(tokens, index + 1)
                if close_index is not None and close_index + 1 < len(tokens):
                    comparator = tokens[close_index + 1]
                    if comparator.value in _COMPARISONS:
                        add(
                            "function-on-predicate",
                            "warning",
                            token,
                            f"{token.value}() wraps a value used in a predicate and may prevent index range use.",
                            "Compare the stored column directly, add a generated column in a tested change, or verify a functional index.",
                        )
        if token.kind == "symbol" and token.value in _COMPARISONS and index > 0:
            right = tokens[index + 1 : index + 3]
            if (
                len(right) == 2
                and right[0].kind == "word"
                and right[0].value.casefold() in _SCALAR_FUNCTIONS
                and right[1].value == "("
            ):
                add(
                    "function-on-predicate",
                    "warning",
                    right[0],
                    f"{right[0].value}() wraps the right-hand predicate value and may prevent index range use.",
                    "Rewrite the predicate so the indexed column is not wrapped by a function, then verify with EXPLAIN.",
                )

        if word == "order" and index + 2 < len(tokens) and lowered[index + 1] == "by":
            if lowered[index + 2] == "rand" and index + 3 < len(tokens) and tokens[index + 3].value == "(":
                add(
                    "order-by-random",
                    "warning",
                    token,
                    "ORDER BY RAND() requires random ordering and may sort/scatter the candidate set.",
                    "Use bounded random-key sampling or a precomputed random column.",
                )

        if word == "limit":
            _audit_limit(tokens, index, add)
        if word == "in" and index + 1 < len(tokens) and tokens[index + 1].value == "(":
            close = _matching_close(tokens, index + 1)
            if close is not None and _list_item_count(tokens[index + 2 : close]) > 100:
                add(
                    "large-in-list",
                    "warning",
                    token,
                    "IN list contains more than 100 top-level values.",
                    "Consider a temporary lookup table or batched query after checking parameter limits and plan behavior.",
                )

    return findings


def _matching_close(tokens: Sequence[Token], open_index: int) -> int | None:
    level = tokens[open_index].depth
    for index in range(open_index + 1, len(tokens)):
        if tokens[index].value == ")" and tokens[index].depth == level:
            return index
    return None


def _list_item_count(tokens: Sequence[Token]) -> int:
    if not tokens:
        return 0
    level = tokens[0].depth
    return 1 + sum(1 for token in tokens if token.depth == level and token.value == ",")


def _audit_limit(tokens: Sequence[Token], index: int, add) -> None:
    if index + 1 >= len(tokens) or tokens[index + 1].kind != "number":
        return
    first = _integer(tokens[index + 1].value)
    offset: int | None = None
    if index + 2 < len(tokens) and tokens[index + 2].value == ",":
        if index + 3 < len(tokens) and tokens[index + 3].kind == "number":
            offset = first
    elif (
        index + 3 < len(tokens)
        and tokens[index + 2].value.casefold() == "offset"
        and tokens[index + 3].kind == "number"
    ):
        offset = _integer(tokens[index + 3].value)
    if offset is not None and offset >= 1000:
        add(
            "large-pagination-offset",
            "warning",
            tokens[index],
            f"Pagination offset {offset} may require scanning and discarding a large prefix.",
            "Prefer keyset pagination using the last stable sort key and add an index matching the sort order.",
        )


def _integer(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _threshold_met(findings: Iterable[Finding], threshold: str) -> bool:
    if threshold == "none":
        return False
    if threshold == "warning":
        return any(finding.severity in {"warning", "error"} for finding in findings)
    return any(finding.severity == "error" for finding in findings)


def _read_input(path: str | None) -> str:
    if path is None or path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _render_text(source: str, findings: Sequence[Finding]) -> str:
    if not findings:
        return "No findings."
    lines = []
    for finding in findings:
        lines.append(
            f"{source}:{finding.line}:{finding.column}: "
            f"{finding.severity} {finding.rule}: {finding.message}"
        )
        lines.append(f"  suggestion: {finding.suggestion}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="SQL file; omit or use '-' for stdin")
    parser.add_argument("--json", action="store_true", help="emit a JSON document")
    parser.add_argument(
        "--fail-on",
        choices=("none", "error", "warning"),
        default="error",
        help="exit 1 when a finding at this severity or above exists",
    )
    args = parser.parse_args(argv)

    source = args.path or "<stdin>"
    try:
        sql = _read_input(args.path)
        findings = audit_sql(sql)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"{source}: {error}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "source": source,
            "findings": [asdict(finding) for finding in findings],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_render_text(source, findings))
    return 1 if _threshold_met(findings, args.fail_on) else 0


if __name__ == "__main__":
    raise SystemExit(main())
