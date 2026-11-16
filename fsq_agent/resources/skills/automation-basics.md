# Automation Basics

- Use the platform tools exposed by the active harness for automation interactions. Do not invent actions outside the provided platform tools.
- Prefer locators from current platform tool responses, observations, and action evidence. These have higher priority than knowledge because knowledge can be stale; use knowledge only as flow reference and candidate target guidance.
- Use platform tool assertion or verification capabilities for verify, assert, confirm, check, ensure, and validate requirements. Do not decide pass or fail only from agent narrative, screenshots, stale artifacts, or ad-hoc observation.
- Use deterministic assertions for exact accessible text, numeric values, checked/selected states, and other exposed semantic facts. Complex or ambiguous locators require renewed observation and refinement, not visual approximation. Missing or clipped evidence is not proof that no accessibility node exists.
- Reserve `assert_with_ai` for explicitly visual assertions of image content, colors/appearance, or custom-drawn content without accessible semantic state. Do not use it to locate targets, ground coordinates, drive the UI, replace an available deterministic check, or override deterministic counterevidence. The presence of unrelated accessible nodes does not invalidate a genuinely visual assertion.
