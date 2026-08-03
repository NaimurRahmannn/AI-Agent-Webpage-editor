"""Deterministic HTML and CSS syntax validation.

Uses html5lib for HTML parsing and tinycss2 for CSS parsing. Validates the
complete resulting source code before backup or atomic write operations.
"""

from __future__ import annotations

from typing import Any, Literal

import html5lib
import tinycss2
import tinycss2.ast

from web.models import StrictModel
from web.settings import Settings


class SyntaxValidationError(ValueError):
    """Base error for syntax validation failures."""


class HtmlSyntaxValidationError(SyntaxValidationError):
    """Raised when HTML parsing detects malformed syntax."""


class CssSyntaxValidationError(SyntaxValidationError):
    """Raised when CSS parsing detects malformed syntax."""


class SyntaxValidatorConfigurationError(SyntaxValidationError):
    """Raised when syntax validation configuration is invalid."""


class SyntaxIssue(StrictModel):
    """Structured syntax error detail with line and column position."""

    line: int | None = None
    column: int | None = None
    code: str
    message: str


class SyntaxValidationResult(StrictModel):
    """Overall syntax validation result for one file."""

    file: str
    language: Literal["html", "css"]
    valid: bool
    issues: tuple[SyntaxIssue, ...] = ()


CSS_DECLARATION_BLOCK_AT_RULES = {
    "color-profile",
    "counter-style",
    "font-face",
    "font-palette-values",
    "page",
    "property",
    "viewport",
}


def _validate_html_syntax(
    filename: str,
    content: str,
) -> SyntaxValidationResult:
    """Validate complete HTML document syntax using html5lib."""

    parser = html5lib.HTMLParser(strict=False)

    try:
        parser.parse(content)
    except Exception as exc:
        issue = SyntaxIssue(
            line=1,
            column=1,
            code="html-parse-exception",
            message=f"HTML parsing exception: {exc}",
        )
        return SyntaxValidationResult(
            file=filename,
            language="html",
            valid=False,
            issues=(issue,),
        )

    issues: list[SyntaxIssue] = []

    for error in parser.errors:
        pos, code, datadict = error if len(error) == 3 else (None, "html-parse-error", {})
        line = pos[0] if pos and isinstance(pos, tuple) and len(pos) >= 1 else None
        col = pos[1] if pos and isinstance(pos, tuple) and len(pos) >= 2 else None

        msg_parts = [code.replace("-", " ")]
        if datadict and isinstance(datadict, dict):
            details = ", ".join(f"{k}={v}" for k, v in datadict.items())
            if details:
                msg_parts.append(f"({details})")

        msg = " ".join(msg_parts)

        issues.append(
            SyntaxIssue(
                line=line,
                column=col,
                code=str(code),
                message=msg,
            )
        )

    issues_tuple = tuple(issues[:5])

    return SyntaxValidationResult(
        file=filename,
        language="html",
        valid=len(issues) == 0,
        issues=issues_tuple,
    )


def _append_css_parse_error(
    issues: list[SyntaxIssue],
    node: Any,
    default_code: str,
    default_message: str,
) -> None:
    issues.append(
        SyntaxIssue(
            line=getattr(node, "source_line", None),
            column=getattr(node, "source_column", None),
            code=getattr(node, "error_code", default_code),
            message=getattr(node, "message", default_message),
        )
    )


def _inspect_css_component_values(
    nodes: list[Any] | tuple[Any, ...],
    issues: list[SyntaxIssue],
) -> None:
    """Inspect declaration values and nested component blocks for ParseError nodes."""

    for node in nodes:
        if isinstance(node, tinycss2.ast.ParseError):
            _append_css_parse_error(
                issues,
                node,
                "css-component-error",
                "Malformed CSS component value",
            )
            continue

        nested_nodes = getattr(node, "content", None) or getattr(node, "arguments", None)
        if nested_nodes:
            _inspect_css_component_values(nested_nodes, issues)


def _inspect_css_declarations(
    nodes: list[Any],
    issues: list[SyntaxIssue],
    default_line: int | None = None,
    default_column: int | None = None,
) -> None:
    """Inspect a CSS declaration list for parser errors."""

    decls = tinycss2.parse_declaration_list(
        nodes,
        skip_comments=True,
        skip_whitespace=True,
    )

    for decl in decls:
        if isinstance(decl, tinycss2.ast.ParseError):
            issues.append(
                SyntaxIssue(
                    line=getattr(decl, "source_line", default_line),
                    column=getattr(decl, "source_column", default_column),
                    code=getattr(decl, "error_code", "css-declaration-error"),
                    message=getattr(decl, "message", "Malformed CSS declaration"),
                )
            )
            continue

        value = getattr(decl, "value", None)
        if value:
            _inspect_css_component_values(value, issues)


def _inspect_css_rules(rules: list[Any], issues: list[SyntaxIssue]) -> None:
    """Recursively inspect CSS AST rules for ParseError objects and malformed declarations."""

    for rule in rules:
        if isinstance(rule, tinycss2.ast.ParseError):
            _append_css_parse_error(
                issues,
                rule,
                "css-parse-error",
                "Malformed CSS rule or block",
            )
            continue

        if isinstance(rule, tinycss2.ast.QualifiedRule):
            _inspect_css_declarations(
                rule.content,
                issues,
                default_line=getattr(rule, "source_line", None),
                default_column=getattr(rule, "source_column", None),
            )

        elif isinstance(rule, tinycss2.ast.AtRule) and rule.content:
            at_keyword = rule.lower_at_keyword

            if at_keyword in CSS_DECLARATION_BLOCK_AT_RULES:
                _inspect_css_declarations(
                    rule.content,
                    issues,
                    default_line=getattr(rule, "source_line", None),
                    default_column=getattr(rule, "source_column", None),
                )
            else:
                nested_rules = tinycss2.parse_rule_list(
                    rule.content,
                    skip_comments=True,
                    skip_whitespace=True,
                )
                _inspect_css_rules(nested_rules, issues)


def _scan_css_structure(content: str) -> list[SyntaxIssue]:
    """Find unmatched CSS delimiters while ignoring strings, comments, and escapes."""

    issues: list[SyntaxIssue] = []
    stack: list[tuple[str, int, int]] = []
    opening = {"{": "}", "(": ")", "[": "]"}
    closing = {"}": "{", ")": "(", "]": "["}
    unclosed_codes = {
        "{": "css-unclosed-block",
        "(": "css-unclosed-parenthesis",
        "[": "css-unclosed-bracket",
    }
    unclosed_messages = {
        "{": "Unclosed CSS block",
        "(": "Unclosed CSS parenthesis",
        "[": "Unclosed CSS bracket",
    }

    index = 0
    line = 1
    column = 1
    comment_start: tuple[int, int] | None = None
    string_quote: str | None = None
    string_start: tuple[int, int] | None = None

    def advance(char: str) -> None:
        nonlocal line, column
        if char == "\n":
            line += 1
            column = 1
        else:
            column += 1

    while index < len(content):
        char = content[index]
        next_char = content[index + 1] if index + 1 < len(content) else ""

        if comment_start:
            if char == "*" and next_char == "/":
                advance(char)
                advance(next_char)
                index += 2
                comment_start = None
                continue

            advance(char)
            index += 1
            continue

        if string_quote:
            if char == "\\" and next_char:
                advance(char)
                advance(next_char)
                index += 2
                continue

            if char == string_quote:
                string_quote = None
                string_start = None

            advance(char)
            index += 1
            continue

        if char == "/" and next_char == "*":
            comment_start = (line, column)
            advance(char)
            advance(next_char)
            index += 2
            continue

        if char in {'"', "'"}:
            string_quote = char
            string_start = (line, column)
            advance(char)
            index += 1
            continue

        if char == "\\" and next_char:
            advance(char)
            advance(next_char)
            index += 2
            continue

        if char in opening:
            stack.append((char, line, column))
        elif char in closing:
            expected_opener = closing[char]
            if stack and stack[-1][0] == expected_opener:
                stack.pop()
            else:
                issues.append(
                    SyntaxIssue(
                        line=line,
                        column=column,
                        code="css-unmatched-delimiter",
                        message="Unmatched CSS closing delimiter",
                    )
                )

        advance(char)
        index += 1

    if comment_start:
        issues.append(
            SyntaxIssue(
                line=comment_start[0],
                column=comment_start[1],
                code="css-unclosed-comment",
                message="Unclosed CSS comment",
            )
        )

    if string_quote and string_start:
        issues.append(
            SyntaxIssue(
                line=string_start[0],
                column=string_start[1],
                code="css-unclosed-string",
                message="Unclosed CSS string",
            )
        )

    for opener, opener_line, opener_column in reversed(stack):
        issues.append(
            SyntaxIssue(
                line=opener_line,
                column=opener_column,
                code=unclosed_codes[opener],
                message=unclosed_messages[opener],
            )
        )

    return issues


def _validate_css_syntax(
    filename: str,
    content: str,
) -> SyntaxValidationResult:
    """Validate complete CSS stylesheet syntax using tinycss2."""

    issues = _scan_css_structure(content)

    try:
        rules = tinycss2.parse_stylesheet(
            content,
            skip_comments=True,
            skip_whitespace=True,
        )
    except Exception as exc:
        issue = SyntaxIssue(
            line=1,
            column=1,
            code="css-parse-exception",
            message=f"CSS parsing exception: {exc}",
        )
        return SyntaxValidationResult(
            file=filename,
            language="css",
            valid=False,
            issues=(issue,),
        )

    _inspect_css_rules(rules, issues)

    issues_tuple = tuple(issues[:5])

    return SyntaxValidationResult(
        file=filename,
        language="css",
        valid=len(issues) == 0,
        issues=issues_tuple,
    )


def validate_source_syntax(
    *,
    filename: str,
    content: str,
    settings: Settings,
) -> SyntaxValidationResult:
    """
    Validate the complete HTML or CSS source syntax.

    Returns a SyntaxValidationResult.
    """

    filename_lower = filename.lower()
    if filename_lower.endswith(".html"):
        lang: Literal["html", "css"] = "html"
    elif filename_lower.endswith(".css"):
        lang = "css"
    else:
        raise SyntaxValidationError(f"unsupported source extension for validation: {filename}")

    if not settings.syntax_validation_enabled:
        return SyntaxValidationResult(file=filename, language=lang, valid=True, issues=())

    if lang == "html":
        if not settings.html_validation_enabled:
            return SyntaxValidationResult(file=filename, language=lang, valid=True, issues=())
        return _validate_html_syntax(filename, content)
    else:
        if not settings.css_validation_enabled:
            return SyntaxValidationResult(file=filename, language=lang, valid=True, issues=())
        return _validate_css_syntax(filename, content)
