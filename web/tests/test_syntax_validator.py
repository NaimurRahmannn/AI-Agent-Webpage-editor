"""Unit tests for Part 1 deterministic HTML/CSS syntax validation.

All tests are deterministic and execute in memory using html5lib and tinycss2.
"""

from __future__ import annotations

from pathlib import Path
import pytest
from pydantic import ValidationError

from web.settings import Settings
from web.tools.syntax_validator import (
    SyntaxIssue,
    SyntaxValidationError,
    SyntaxValidationResult,
    validate_source_syntax,
)


@pytest.fixture()
def val_settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        syntax_validation_enabled=True,
        html_validation_enabled=True,
        css_validation_enabled=True,
    )


SAMPLE_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Test Page</title>
</head>
<body>
  <h1>Hello World</h1>
</body>
</html>
"""

SAMPLE_CSS = """\
:root {
  --main-color: #2d6a4f;
}

body {
  margin: 0;
  color: var(--main-color);
}

@media (min-width: 768px) {
  .hero {
    display: flex;
  }
}
"""


def test_valid_html(val_settings: Settings) -> None:
    res = validate_source_syntax(filename="index.html", content=SAMPLE_HTML, settings=val_settings)
    assert res.valid is True
    assert res.language == "html"
    assert len(res.issues) == 0


def test_valid_css(val_settings: Settings) -> None:
    res = validate_source_syntax(filename="style.css", content=SAMPLE_CSS, settings=val_settings)
    assert res.valid is True
    assert res.language == "css"
    assert len(res.issues) == 0


def test_html_malformed_closing_structure(val_settings: Settings) -> None:
    bad_html = "<div><p>Unclosed div and p</span></div>"
    res = validate_source_syntax(filename="index.html", content=bad_html, settings=val_settings)
    assert res.valid is False
    assert len(res.issues) > 0


def test_html_malformed_attributes(val_settings: Settings) -> None:
    bad_html = "<div class=>Test</div>"
    res = validate_source_syntax(filename="index.html", content=bad_html, settings=val_settings)
    assert res.valid is False
    assert len(res.issues) > 0


def test_html_duplicate_attributes(val_settings: Settings) -> None:
    bad_html = '<a href="foo" href="bar">Link</a>'
    res = validate_source_syntax(filename="index.html", content=bad_html, settings=val_settings)
    assert res.valid is False
    assert any("duplicate" in issue.code.lower() or "duplicate" in issue.message.lower() for issue in res.issues)


def test_css_missing_colon(val_settings: Settings) -> None:
    bad_css = "body { color red; }"
    res = validate_source_syntax(filename="style.css", content=bad_css, settings=val_settings)
    assert res.valid is False
    assert len(res.issues) > 0


def test_css_unclosed_block(val_settings: Settings) -> None:
    bad_css = "body { color: red;"
    res = validate_source_syntax(filename="style.css", content=bad_css, settings=val_settings)
    assert res.valid is False
    assert len(res.issues) > 0


def test_css_valid_media_query(val_settings: Settings) -> None:
    css = "@media (max-width: 600px) { .btn { font-size: 12px; } }"
    res = validate_source_syntax(filename="style.css", content=css, settings=val_settings)
    assert res.valid is True


def test_css_invalid_inside_media_query(val_settings: Settings) -> None:
    bad_css = "@media (max-width: 600px) { .btn { font-size 12px; } }"
    res = validate_source_syntax(filename="style.css", content=bad_css, settings=val_settings)
    assert res.valid is False
    assert len(res.issues) > 0


def test_css_custom_property(val_settings: Settings) -> None:
    css = ":root { --accent-color: #ff0000; } p { color: var(--accent-color); }"
    res = validate_source_syntax(filename="style.css", content=css, settings=val_settings)
    assert res.valid is True


def test_unicode_html_and_css(val_settings: Settings) -> None:
    html = "<!doctype html><html><body><h1>Bonjour 🌍</h1></body></html>"
    css = "body::after { content: '✨'; }"
    assert validate_source_syntax(filename="index.html", content=html, settings=val_settings).valid is True
    assert validate_source_syntax(filename="style.css", content=css, settings=val_settings).valid is True


def test_unsupported_extension(val_settings: Settings) -> None:
    with pytest.raises(SyntaxValidationError, match="unsupported source extension"):
        validate_source_syntax(filename="script.js", content="console.log('hello');", settings=val_settings)


def test_global_validation_disabled(tmp_path: Path) -> None:
    s = Settings(project_root=tmp_path, syntax_validation_enabled=False)
    bad_css = "body { color red; }"
    res = validate_source_syntax(filename="style.css", content=bad_css, settings=s)
    assert res.valid is True
    assert len(res.issues) == 0


def test_html_validation_disabled(tmp_path: Path) -> None:
    s = Settings(project_root=tmp_path, syntax_validation_enabled=True, html_validation_enabled=False)
    bad_html = '<a href="foo" href="bar">Link</a>'
    res = validate_source_syntax(filename="index.html", content=bad_html, settings=s)
    assert res.valid is True


def test_css_validation_disabled(tmp_path: Path) -> None:
    s = Settings(project_root=tmp_path, syntax_validation_enabled=True, css_validation_enabled=False)
    bad_css = "body { color red; }"
    res = validate_source_syntax(filename="style.css", content=bad_css, settings=s)
    assert res.valid is True


def test_parser_error_output_bounded(val_settings: Settings) -> None:
    bad_html = "".join(f'<a href="{i}" href="{i}">link</a>\n' for i in range(20))
    res = validate_source_syntax(filename="index.html", content=bad_html, settings=val_settings)
    assert res.valid is False
    assert len(res.issues) <= 5  # bounded to first 5 issues


def test_complete_source_not_exposed_in_errors(val_settings: Settings) -> None:
    secret_text = "SECRET_SOURCE_TEXT_12345"
    bad_css = f"/* {secret_text} */ body {{ color red; }}"
    res = validate_source_syntax(filename="style.css", content=bad_css, settings=val_settings)
    for issue in res.issues:
        assert secret_text not in issue.message


def test_unexpected_result_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        SyntaxValidationResult.model_validate(
            {"file": "index.html", "language": "html", "valid": True, "extra_field": "bad"}
        )
