# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import pytest

from fsq_agent.drivers.macos._native_fallback import _read_ax_identity_and_value


@pytest.mark.parametrize(
    ("role", "subrole", "reads_value"),
    [("AXTextField", "AXSecureTextField", False), ("AXUnknown", "", False), ("AXTextField", None, False), ("AXTextField", "", True)],
)
def test_native_ax_value_read_requires_known_safe_type(role: str, subrole: str | None, reads_value: bool) -> None:
    calls: list[str] = []
    values = {"AXRole": role, "AXSubrole": subrole, "AXValue": "editable value"}

    def read(attribute: str) -> str | None:
        calls.append(attribute)
        return values[attribute]

    assert _read_ax_identity_and_value(read) == (role, subrole, "editable value" if reads_value else None)
    assert calls == ["AXRole", "AXSubrole", *(["AXValue"] if reads_value else [])]
