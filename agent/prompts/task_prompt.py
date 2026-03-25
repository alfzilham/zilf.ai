"""
Task Prompt Templates â€” structures the first user message for common task types.

Principles (from Prompt Engineering.md):
  - One task per prompt
  - Provide acceptance criteria, not implementation instructions
  - Include relevant file paths explicitly
  - Keep templates terse and self-contained

Available templates:
  BugFixTemplate          â€” reproduce â†’ locate â†’ fix â†’ test
  FeatureTemplate         â€” spec â†’ interface â†’ criteria
  RefactorTemplate        â€” scope â†’ constraints â†’ criteria
  CodeReviewTemplate      â€” review type â†’ focus areas
  build_task_prompt()     â€” auto-selects template based on task keywords
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Bug fix template
# ---------------------------------------------------------------------------


BUG_FIX_TEMPLATE = """\
## Task: Bug Fix

**Report**
{bug_description}

**Reproduction**
```
{reproduction_steps}
```

**Expected behaviour**
{expected_behaviour}

**Actual behaviour**
{actual_behaviour}

**Relevant files** (may be incomplete â€” search if needed)
{relevant_files}

**Acceptance criteria**
- [ ] The reproduction steps no longer trigger the bug
- [ ] All existing tests pass
- [ ] No unrelated files are modified
{additional_criteria}

**Constraints**
{constraints}
"""


# ---------------------------------------------------------------------------
# Feature implementation template
# ---------------------------------------------------------------------------


FEATURE_TEMPLATE = """\
## Task: Feature Implementation

**Feature name**
{feature_name}

**Description**
{feature_description}

**Interface specification**
```python
{interface_spec}
```

**Behaviour requirements**
{behaviour_requirements}

**Edge cases to handle**
{edge_cases}

**Out of scope**
{out_of_scope}

**Relevant existing code**
{relevant_files}

**Acceptance criteria**
- [ ] All specified interface methods are implemented
- [ ] Unit tests cover the happy path and all listed edge cases
- [ ] Docstrings follow the existing module style
- [ ] No new dependencies introduced without prior approval
{additional_criteria}
"""


# ---------------------------------------------------------------------------
# Refactor template
# ---------------------------------------------------------------------------


REFACTOR_TEMPLATE = """\
## Task: Refactor

**Target**
{target_description}

**Motivation**
{motivation}

**Scope** (touch ONLY these files/modules)
{scope}

**Out of scope**
{out_of_scope}

**Acceptance criteria**
- [ ] All existing tests pass unchanged
- [ ] Public API is preserved (no breaking changes)
- [ ] Code complexity is reduced or equivalent
{additional_criteria}

**Constraints**
{constraints}
"""


# ---------------------------------------------------------------------------
# Code review template
# ---------------------------------------------------------------------------


CODE_REVIEW_TEMPLATE = """\
## Task: Code Review

**Files to review**
{files_to_review}

**Review type**
{review_type}

**Focus areas**
{focus_areas}

**Context**
{context}

Provide a structured review with:
1. Summary of what the code does
2. Issues found (critical / major / minor)
3. Specific line-level suggestions
4. Overall assessment
"""


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------


@dataclass
class TaskPromptConfig:
    """Flexible config for any task type."""

    task_type: str = "general"
    description: str = ""
    relevant_files: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    constraints: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def build_task_prompt(config: TaskPromptConfig) -> str:
    """
    Auto-select and populate the right template based on task_type.

    task_type options: "bug_fix", "feature", "refactor", "review", "general"
    """
    files_str = "\n".join(config.relevant_files) if config.relevant_files else "(search the workspace)"
    criteria_str = "\n".join(f"- [ ] {c}" for c in config.acceptance_criteria)

    if config.task_type == "bug_fix":
        return BUG_FIX_TEMPLATE.format(
            bug_description=config.description,
            reproduction_steps=config.extra.get("reproduction_steps", "(see description)"),
            expected_behaviour=config.extra.get("expected_behaviour", "(see description)"),
            actual_behaviour=config.extra.get("actual_behaviour", "(see description)"),
            relevant_files=files_str,
            additional_criteria=criteria_str,
            constraints=config.constraints or "None",
        )

    if config.task_type == "feature":
        return FEATURE_TEMPLATE.format(
            feature_name=config.extra.get("feature_name", config.description[:60]),
            feature_description=config.description,
            interface_spec=config.extra.get("interface_spec", "# Define the interface"),
            behaviour_requirements=config.extra.get("behaviour_requirements", "(see description)"),
            edge_cases=config.extra.get("edge_cases", "(identify from requirements)"),
            out_of_scope=config.extra.get("out_of_scope", "None specified"),
            relevant_files=files_str,
            additional_criteria=criteria_str,
        )

    if config.task_type == "refactor":
        return REFACTOR_TEMPLATE.format(
            target_description=config.description,
            motivation=config.extra.get("motivation", "(see description)"),
            scope=files_str,
            out_of_scope=config.extra.get("out_of_scope", "All other files"),
            additional_criteria=criteria_str,
            constraints=config.constraints or "None",
        )

    if config.task_type == "review":
        return CODE_REVIEW_TEMPLATE.format(
            files_to_review=files_str,
            review_type=config.extra.get("review_type", "General code quality"),
            focus_areas=config.extra.get("focus_areas", "Correctness, readability, performance"),
            context=config.description,
        )

    # General / free-form
    lines = [config.description]
    if config.relevant_files:
        lines.append(f"\n**Relevant files**\n{files_str}")
    if config.acceptance_criteria:
        lines.append(f"\n**Acceptance criteria**\n{criteria_str}")
    if config.constraints:
        lines.append(f"\n**Constraints**\n{config.constraints}")
    return "\n".join(lines)


def auto_detect_task_type(task: str) -> str:
    """
    Heuristic: guess the task type from keywords in the task description.
    Returns one of: bug_fix, feature, refactor, review, general.
    """
    task_lower = task.lower()
    if any(w in task_lower for w in ("fix", "bug", "error", "crash", "fail", "broken")):
        return "bug_fix"
    if any(w in task_lower for w in ("implement", "add", "create", "build", "write")):
        return "feature"
    if any(w in task_lower for w in ("refactor", "clean", "reorganize", "simplify", "improve")):
        return "refactor"
    if any(w in task_lower for w in ("review", "audit", "check", "analyse", "analyze")):
        return "review"
    return "general"
