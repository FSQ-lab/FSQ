# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fsq_agent.models import GoalPrePlan, KnowledgeBundle, SkillBundle, Task

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
Authorized browsing, viewing, inspection, and exploration are legitimate executable requests even without a rigid QA
assertion. For these requests, verification_goal describes completing the requested authorized flow and truthfully
reporting evidence-grounded observations without unauthorized effects. Do not invent a known answer, expected state,
exhaustive exploration requirement, or state change. Preserve explicit scope, exact text/answers, requested output
format/units/rounding, duration bounds, no-extra-item requirements, and no-save/no-send or other terminal constraints.
Do not invent observed facts or substitute for a missing named target. Plan authorized delegated choices on matched
targets: choose the nearest observed available option meeting every explicit constraint, or compose current-run facts
with neutral truthful wording at the semantically matching destination. Do not skip because a default, wording, or
cell is unspecified. Cross-check related sources before declaring ambiguity; continue on an evidenced tie only when
authorized, and report it. Plan independent deliverables despite a local blocker, but retain necessary prerequisites.
For open relative time windows, check device_local_time and matching records first. Use the latest populated business
period or activity anchor only when evidence shows a historical dataset; report that basis. Never replace explicit
dates or genuinely current requirements.
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
Preserve an explicitly requested complete filename exactly, including an explicit absence of extension. A default .md
suffix is not equivalent. Plan complete saved-name/destination verification in the actual file-list/file-management view,
not an editor title that may hide extensions; never add a save/rename that violates a draft or no-save restriction.
Put execution-critical qualifications in the same key action's notes; preserve observed facts rather than inventing them.
Only a valid tool-root write_budget receipt activates max/remaining throttling and goal-required completion reserves.
Absent, malformed, or throwing optional budget hooks impose no restrictions, synthetic counters, or budget-based stop.
For cross-screen collection, retain identities, relevant fields, unresolved facts, and coverage with multiplicity but no
overlap double count. Prune only when a goal-defined predicate is disproved; unequal durations can still contribute to
sum/subset tasks. Reanchor after paging/deletion and continue feasible requested coverage while adequate budget remains.
For location search, use the core name with country/region constraints and expand the current general radius until a
candidate, observed terminal/unchanged/blocked range, or supplied limit before narrow category tabs. No unchanged cycles
or hidden coordinates. Verified input is not rewritten merely because its display clips; distinguish uncertain acceptance.
For other named lookups prefer exposed Search unless the target is already visible or the browsing path is explicit.
On the first empty or mismatched scoped result verify the parent scope and correct a wrong scope before refining. Use at
most two concise root queries per unchanged scope, then switch if authorized, never away from an explicitly fixed scope.
Reuse unambiguous locators for deterministic same-view actions; refresh ui_snapshot at navigation, modal/sheet changes,
dynamic results, or locator ambiguity/failure. Reserve assertions for goal-required checkpoints and final verification.
Use result.to_page_id values from page element operations when describing navigation between pages.
If optional project or page knowledge is incomplete or absent, still return the best concise contiguous plan and add
warnings only when missing knowledge materially affects planning confidence. You may skip at most one consecutive
missing key action when page or element knowledge is unavailable for goal references. For raw_case references, use raw
steps as a reference path, not brittle truth. When an authorized request has no reliable concrete action chain, leave
key_actions empty rather than inventing actions; execution can derive its path from fresh observations within the request.
Still supply its evidence-grounded flow-delivery verification_goal. A bare app/topic without an authorized scope is not
permission to invent a task. Technical output failures are not equivalent to a legitimate open-ended request.
""".strip()


def verification_goal_for_task(task: Task) -> str:
    if task.verification_goal and task.verification_goal.strip():
        return task.verification_goal
    scope = {
        "name": task.name,
        "description": task.description,
        "reference_type": task.planning_reference_kind,
        "reference_text": task.planning_reference_text,
        "explicit_constraints": task.acceptance_criteria,
    }
    return (
        "Complete only the flow authorized by the original request and truthfully deliver evidence-grounded observations "
        "or the supported answer. Preserve every explicit outcome, exact value/text, scope, output format, unit/rounding, "
        "duration interval, collection/no-extra-item, and terminal-state constraint; do not authorize additional effects. "
        "For open inspection/exploration, evidence of the requested flow and truthful observations is sufficient, without "
        "an invented QA assertion, exhaustive exploration, or state change. Explicit objective requirements remain strict. "
        "For authorized delegated choices on the matched target, verify the observed compliant selection or context-grounded write, not an invented exact parameter. "
        "Verify any historical anchor used for an open relative window, and distinguish independent completed deliverables from blocked prerequisites. "
        "Raw-case steps are advisory context, not additional final requirements. "
        "Use actual execution evidence, not the main agent's claim alone.\nOriginal request context:\n" + json.dumps(scope, ensure_ascii=False, indent=2)
    )


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
