"""Deterministic HTML and CSS syntax validation for Phase 10.

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


def _inspect_css_rules(rules: list[Any], issues: list[SyntaxIssue]) -> None:
    """Recursively inspect CSS AST rules for ParseError objects and malformed declarations."""

    for rule in rules:
        if isinstance(rule, tinycss2.ast.ParseError):
            issues.append(
                SyntaxIssue(
                    line=getattr(rule, "source_line", None),
                    column=getattr(rule, "source_column", None),
                    code=getattr(rule, "error_code", "css-parse-error"),
                    message=getattr(rule, "message", "Malformed CSS rule or block"),
                )
            )
            continue

        if isinstance(rule, tinycss2.ast.QualifiedRule):
            decls = tinycss2.parse_declaration_list(
                rule.content,
                skip_comments=True,
                skip_whitespace=True,
            )
            for decl in decls:
                if isinstance(decl, tinycss2.ast.ParseError):
                    issues.append(
                        SyntaxIssue(
                            line=getattr(decl, "source_line", getattr(rule, "source_line", None)),
                            column=getattr(decl, "source_column", getattr(rule, "source_column", None)),
                            code=getattr(decl, "error_code", "css-declaration-error"),
                            message=getattr(decl, "message", "Malformed CSS declaration"),
                        )
                    )

        elif isinstance(rule, tinycss2.ast.AtRule) and rule.content:
            nested_rules = tinycss2.parse_rule_list(
                rule.content,
                skip_comments=True,
                skip_whitespace=True,
            )
            _inspect_css_rules(nested_rules, issues)


def _validate_css_syntax(
    filename: str,
    content: str,
) -> SyntaxValidationResult:
    """Validate complete CSS stylesheet syntax using tinycss2."""

    issues: list[SyntaxIssue] = []

    if "/*" in content and "*/" not in content[content.rfind("/*"):]:
        issues.append(
            SyntaxIssue(
                line=1,
                column=1,
                code="css-unclosed-comment",
                message="Unclosed CSS comment",
            )
        )

    if content.count("{") != content.count("}"):
        issues.append(
            SyntaxIssue(
                line=1,
                column=1,
                code="css-unclosed-block",
                message="Unclosed CSS block (mismatched '{' and '}')",
            )
        )

    if content.count("(") != content.count(")"):
        issues.append(
            SyntaxIssue(
                line=1,
                column=1,
                code="css-unclosed-parenthesis",
                message="Unclosed CSS parenthesis (mismatched '(' and ')')",
            )
        )

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
