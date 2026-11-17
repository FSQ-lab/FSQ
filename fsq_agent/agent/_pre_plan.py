# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fsq_agent.models import GoalPrePlan, KnowledgeBundle, SkillBundle

PRE_PLAN_AGENT_INSTRUCTIONS = """
You are fsq-agent's goal pre-planner.
Convert one planning reference into an ordered list of key actions and one final verification goal using the loaded planning context.

The input contains reference_type and reference_text. For reference_type="goal", treat reference_text as the
natural-language goal. For reference_type="raw_case", treat the complete raw case text as advisory source material,
not as parsed executable input. Raw YAML steps may help infer an execution flow, but they may be stale, incomplete,
or inaccurate. Prefer case-level intent signals such as name, metadata, tags, properties, and human-authored goal or
description text when summarizing verification_goal. Use step content only as supporting context when case-level intent
is incomplete or ambiguous. If steps conflict with case-level intent, case-level intent wins and the conflict must be
recorded in warnings. Lifecycle commands such as launchApp and killApp are setup or teardown intent unless they are
semantically central to the case.

This is planning only. Do not execute UI actions, do not call UI automation tools, and do not claim runtime verification.
The input contains available_platform_tools from the active CommonTool/PlatformTool capability registry. Use this as
the current executable action surface when choosing key actions and final verification wording. Tool names are SDK
function names; aliases are authored FSQ action names when available.
Your initial context contains configured skills that loaded successfully, project.md when present, and index.md when
present. Use read_knowledge_index to reload the available project and page-index entries if needed. Use
read_knowledge_page only to load concrete page nodes needed to continue the goal action chain. When a loaded page
operation points to another page_id, read that page if it is needed to continue the action chain. Use project guidance,
page identifiers, elements, reference locators, and element operation results to infer a concise action path.
Treat reference locators as helpful hints, not authoritative truth.

Return only the structured GoalPrePlan output. Key actions should be actionable, ordered, and page-aware.
verification_goal must be exactly one concise string describing the final user-visible outcome that evidence must prove.
Do not turn intermediate operations into final verification requirements. Do not add unrelated product, account,
network, performance, visual-regression, or accessibility checks unless explicitly requested by the input.
Preserve an unsaved, unsubmitted, or unsent terminal state only when the goal explicitly requests that restriction or
a draft state, for example "do not save", "without submitting", "leave as draft", or "unsent". Such restrictions win.
Otherwise, when creating an entry, applying settings, or answering an interactive form/challenge requires a clear
Submit, Save, Done, or OK control to make the requested result effective, include that task-required completion action
and verify the resulting state. The goal need not literally say "click Submit". Never invent "without submitting"
or an unsaved-draft goal merely because those words are absent. Do not authorize unrelated or destructive confirmations.
For click-to-reveal sequences, count requested clicks separately from observed values: the initial visible item can be
part of the sequence, and the final click can open the answer form rather than reveal another item. Use the complete
actually observed valid sequence, compute the requested answer, and complete any required authorized submission.
Do not invent an extra item or abandon submission solely because the UI transitioned to the form. Truly missing or
contradictory observations remain unverified; planning cannot claim observations or success before execution.
Use result.to_page_id values from page element operations when describing navigation between pages.
If optional project or page knowledge is incomplete or absent, still return the best concise contiguous plan and add
warnings only when missing knowledge materially affects planning confidence. You may skip at most one consecutive
missing key action when page or element knowledge is unavailable for goal references. For raw_case references, use raw
steps as a reference path, not brittle truth. If you cannot form a useful action chain or cannot summarize a reliable
verification goal, return empty key_actions or an empty verification_goal and explain why in warnings.
""".strip()


class ReadKnowledgeIndexArgs(BaseModel):
    reason: str | None = Field(default=None, description="Short reason for rereading available project and page-index knowledge.")


class ReadKnowledgePageArgs(BaseModel):
    page_id: str | None = Field(default=None, description="Page id to load, such as edge_android_new_tab_page.")
    file: str | None = Field(default=None, description="Optional relative page file path from the page index or pre-plan knowledge directory.")
    reason: str | None = Field(default=None, description="Short reason this page is needed for planning.")


def build_pre_plan_input(
    reference_text: str,
    knowledge: KnowledgeBundle,
    skills: list[SkillBundle],
    reference_type: str = "goal",
    available_platform_tools: list[dict[str, Any]] | None = None,
    runtime_secret_names: list[str] | None = None,
    runtime_secret_warnings: list[str] | None = None,
) -> str:
    payload = {
        "reference_type": reference_type,
        "reference_text": reference_text,
        "available_platform_tools": list(available_platform_tools or []),
        "runtime_secret_names": list(runtime_secret_names or []),
        "runtime_secret_warnings": list(runtime_secret_warnings or []),
        "knowledge_items": knowledge.items,
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "instructions": skill.instructions,
            }
            for skill in skills
        ],
        "output_schema": GoalPrePlan.model_json_schema(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def extract_json_payload(text: str) -> Any | None:
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def page_file_from_index(index_text: str, page_id: str) -> str | None:
    payload = extract_json_payload(index_text)
    if not isinstance(payload, dict):
        return None
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return None
    for page in pages:
        if not isinstance(page, dict):
            continue
        if page.get("page_id") == page_id and page.get("file"):
            return str(page["file"])
    return None


def safe_page_relative_path(value: str) -> Path | None:
    normalized = value.replace("\\", "/").strip()
    if not normalized:
        return None
    path = Path(normalized)
    if path.is_absolute() or ".." in path.parts:
        return None
    if path.parts and path.parts[0] == "pages":
        return path
    return Path("pages") / path
