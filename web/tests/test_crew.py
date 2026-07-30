from __future__ import annotations

import pytest
from crewai import Process

from web.crew import (
    LLMConfigurationError,
    WebEditingCrew,
    build_crew_inputs,
    build_groq_llm,
    render_source_bundle,
)
from web.models import LocatorResult, ProposedPatch
from web.settings import Settings


def configured_settings(tmp_path) -> Settings:
    return Settings(
        project_root=tmp_path,
        groq_api_key="test-api-key",
        groq_model="groq/test-model",
    )


def test_build_groq_llm_uses_configured_model(
    tmp_path,
) -> None:
    settings = configured_settings(tmp_path)

    llm = build_groq_llm(settings)

    assert llm.model == "groq/test-model"


def test_build_groq_llm_rejects_placeholder_config(
    tmp_path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        groq_api_key="replace_with_your_key",
        groq_model="groq/replace_with_model_id",
    )

    with pytest.raises(
        LLMConfigurationError,
        match="Groq is not configured",
    ):
        build_groq_llm(settings)


def test_build_groq_llm_requires_groq_prefix(
    tmp_path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        groq_api_key="test-api-key",
        groq_model="llama-test-model",
    )

    with pytest.raises(
        LLMConfigurationError,
        match=r"groq/<model-id>",
    ):
        build_groq_llm(settings)


def test_source_bundle_is_deterministic() -> None:
    sources = {
        "style.css": "body {\n  color: red;\n}\n",
        "index.html": "<h1>Hello</h1>\n",
    }

    bundle = render_source_bundle(sources)

    index_position = bundle.index(
        "===== BEGIN FILE: index.html ====="
    )
    style_position = bundle.index(
        "===== BEGIN FILE: style.css ====="
    )

    assert index_position < style_position
    assert "<h1>Hello</h1>\n" in bundle
    assert "body {\n  color: red;\n}\n" in bundle


def test_source_bundle_preserves_braces() -> None:
    sources = {
        "style.css": (
            ":root {\n"
            "  --accent: #123456;\n"
            "}\n"
        ),
    }

    bundle = render_source_bundle(sources)

    assert ":root {" in bundle
    assert "  --accent: #123456;" in bundle
    assert "}" in bundle


def test_source_bundle_rejects_empty_sources() -> None:
    with pytest.raises(
        ValueError,
        match="at least one source file",
    ):
        render_source_bundle({})


def test_build_crew_inputs() -> None:
    inputs = build_crew_inputs(
        instruction="  Make the button darker.  ",
        sources={
            "style.css": (
                ".cta {\n"
                "  background: green;\n"
                "}\n"
            ),
        },
        session_memory=(
            "SESSION MEMORY\n"
            "Last target: .cta background"
        ),
    )

    assert inputs["instruction"] == "Make the button darker."
    assert "BEGIN FILE: style.css" in inputs["source_bundle"]
    assert ".cta {" in inputs["source_bundle"]
    assert "Last target: .cta background" in inputs[
        "session_memory"
    ]


def test_build_crew_inputs_rejects_blank_instruction() -> None:
    with pytest.raises(
        ValueError,
        match="instruction must not be empty",
    ):
        build_crew_inputs(
            instruction="   ",
            sources={"index.html": "<h1>Hello</h1>"},
            session_memory="No history.",
        )


def test_crew_has_two_agents_and_two_tasks(
    tmp_path,
) -> None:
    definition = WebEditingCrew(
        configured_settings(tmp_path)
    )

    editing_crew = definition.crew()

    assert len(editing_crew.agents) == 2
    assert len(editing_crew.tasks) == 2
    assert editing_crew.process == Process.sequential


def test_agents_share_same_llm(tmp_path) -> None:
    definition = WebEditingCrew(
        configured_settings(tmp_path)
    )

    locator = definition.locator_agent()
    editor = definition.editor_agent()

    assert locator.llm is editor.llm
    assert locator.allow_delegation is False
    assert editor.allow_delegation is False
    assert locator.tools == []
    assert editor.tools == []


def test_locator_task_uses_locator_model(
    tmp_path,
) -> None:
    definition = WebEditingCrew(
        configured_settings(tmp_path)
    )

    locator_task = definition.locator_task()

    assert locator_task.output_pydantic is LocatorResult
    assert locator_task.agent is definition.locator_agent()


def test_editor_task_uses_patch_model_and_locator_context(
    tmp_path,
) -> None:
    definition = WebEditingCrew(
        configured_settings(tmp_path)
    )

    locator_task = definition.locator_task()
    editor_task = definition.editor_task()

    assert editor_task.output_pydantic is ProposedPatch
    assert editor_task.agent is definition.editor_agent()
    assert editor_task.context == [locator_task]
    assert editor_task.context[0] is locator_task


def test_crew_task_order_is_locator_then_editor(
    tmp_path,
) -> None:
    definition = WebEditingCrew(
        configured_settings(tmp_path)
    )

    editing_crew = definition.crew()

    assert editing_crew.tasks[0] is definition.locator_task()
    assert editing_crew.tasks[1] is definition.editor_task()

    assert (
        editing_crew.tasks[1].context[0]
        is editing_crew.tasks[0]
    )


def test_task_templates_contain_runtime_inputs(
    tmp_path,
) -> None:
    definition = WebEditingCrew(
        configured_settings(tmp_path)
    )

    locator_description = (
        definition.locator_task().description
    )
    editor_description = (
        definition.editor_task().description
    )

    for description in (
        locator_description,
        editor_description,
    ):
        assert "{instruction}" in description
        assert "{session_memory}" in description
        assert "{source_bundle}" in description