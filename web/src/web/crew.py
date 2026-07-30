from __future__ import annotations

from collections.abc import Mapping

from crewai import Agent, Crew, LLM, Process, Task
from crewai.project import CrewBase, agent, crew, task

from web.models import LocatorResult, ProposedPatch
from web.settings import Settings


class LLMConfigurationError(ValueError):
    """Raised when the Groq-backed CrewAI LLM is not configured safely."""


def build_groq_llm(settings: Settings) -> LLM:
    """
    Create the shared Groq-backed CrewAI LLM.

    Constructing this object does not make an API request. A network call
    occurs only when the crew is kicked off.
    """

    if not settings.llm_is_configured:
        raise LLMConfigurationError(
            "Groq is not configured. Set GROQ_API_KEY and GROQ_MODEL "
            "to non-placeholder values."
        )

    if settings.groq_api_key is None:
        raise LLMConfigurationError("GROQ_API_KEY is missing")

    if settings.groq_model is None:
        raise LLMConfigurationError("GROQ_MODEL is missing")

    model = settings.groq_model.strip()

    if not model.startswith("groq/"):
        raise LLMConfigurationError(
            "GROQ_MODEL must use CrewAI's 'groq/<model-id>' format"
        )

    api_key = settings.groq_api_key.get_secret_value().strip()

    return LLM(
        model=model,
        api_key=api_key,
        temperature=0.0,
        max_completion_tokens=4096,
        timeout=60,
    )


def render_source_bundle(
    sources: Mapping[str, str],
) -> str:
    """
    Render current source files as clearly separated prompt data.

    File names are sorted to make prompts and tests deterministic.
    """

    if not sources:
        raise ValueError("at least one source file is required")

    blocks: list[str] = []

    for relative_name in sorted(sources):
        content = sources[relative_name]

        if not relative_name.strip():
            raise ValueError("source filenames must not be empty")

        if not isinstance(content, str):
            raise TypeError(
                f"source content must be text: {relative_name}"
            )

        block = (
            f"===== BEGIN FILE: {relative_name} =====\n"
            f"{content}"
        )

        if not content.endswith(("\n", "\r")):
            block += "\n"

        block += f"===== END FILE: {relative_name} ====="

        blocks.append(block)

    return "\n\n".join(blocks)


def build_crew_inputs(
    instruction: str,
    sources: Mapping[str, str],
    session_memory: str,
) -> dict[str, str]:
    """
    Build the runtime inputs that Phase 5 will pass to crew.kickoff().

    This function performs no LLM call and no filesystem write.
    """

    normalized_instruction = instruction.strip()

    if not normalized_instruction:
        raise ValueError("instruction must not be empty")

    normalized_memory = session_memory.strip()

    if not normalized_memory:
        normalized_memory = (
            "SESSION MEMORY\n"
            "Current source files are the source of truth.\n"
            "No successful previous edits are available."
        )

    return {
        "instruction": normalized_instruction,
        "source_bundle": render_source_bundle(sources),
        "session_memory": normalized_memory,
    }


@CrewBase
class WebEditingCrew:
    """Sequential locator/editor crew for one HTML or CSS edit."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(
        self,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self._shared_llm = build_groq_llm(self.settings)

    @agent
    def locator_agent(self) -> Agent:
        """Create the source-locator agent."""

        return Agent(
            config=self.agents_config["locator_agent"],
            llm=self._shared_llm,
            allow_delegation=False,
            verbose=False,
            max_iter=3,
        )

    @agent
    def editor_agent(self) -> Agent:
        """Create the minimal-patch editor agent."""

        return Agent(
            config=self.agents_config["editor_agent"],
            llm=self._shared_llm,
            allow_delegation=False,
            verbose=False,
            max_iter=3,
        )

    @task
    def locator_task(self) -> Task:
        """Locate one exact source target or reject the request."""

        return Task(
            config=self.tasks_config["locator_task"],
            agent=self.locator_agent(),
            output_pydantic=LocatorResult,
        )

    @task
    def editor_task(self) -> Task:
        """
        Produce one structured minimal patch.

        The explicit context link is a non-negotiable architecture
        requirement. It supplies the locator result to the editor.
        """

        return Task(
            config=self.tasks_config["editor_task"],
            agent=self.editor_agent(),
            context=[self.locator_task()],
            output_pydantic=ProposedPatch,
        )

    @crew
    def crew(self) -> Crew:
        """Assemble the two-agent sequential crew."""

        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
            memory=False,
        )