# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING

from fsq_agent.ai_services import build_ai_assertion_evaluator
from fsq_agent.config import validate_provider_settings
from fsq_agent.models import ConfigurationError

if TYPE_CHECKING:
    from fsq_agent.config import Settings
    from fsq_agent.core.interfaces import AIAssertionEvaluatorProtocol


def prepare_strict_visual_evaluator(settings: Settings, *, requires_ai_assertion: bool) -> AIAssertionEvaluatorProtocol | None:
    """Prepare only for authored visual assertions; Provider readiness is not frame authorization."""
    evaluator = None
    if requires_ai_assertion:
        try:
            validate_provider_settings(settings)
            evaluator = build_ai_assertion_evaluator(settings)
        except ConfigurationError:
            # Strict replay records the assertion as unverified instead of rejecting startup.
            pass
    target = getattr(settings.harness, settings.harness.platform)
    target.visual_assertion_policy = target.visual_assertion_policy.model_copy(update={"provider_available": evaluator is not None})
    return evaluator
