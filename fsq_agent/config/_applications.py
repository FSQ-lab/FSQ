# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from pathlib import Path

import yaml
from pydantic import TypeAdapter

from fsq_agent.config._workspace import _UniqueKeyLoader
from fsq_agent.models import ConfigurationError, WindowsApplicationConfig


def load_windows_application_catalogue() -> tuple[WindowsApplicationConfig, ...]:
    """Load bundled policy without probing installation or accepting overrides."""
    try:
        source = Path(__file__).with_name("applications.windows.yaml").read_text(encoding="utf-8")
        data = yaml.load(source, Loader=_UniqueKeyLoader)  # noqa: S506 -- existing SafeLoader subclass rejects duplicate keys.
        entries = TypeAdapter(tuple[WindowsApplicationConfig, ...]).validate_python(data)
        if not entries or len({entry.id for entry in entries}) != len(entries):
            raise ValueError("Empty catalogue or duplicate application IDs.")  # noqa: TRY301 -- translate all resource/schema failures at this boundary.
    except (OSError, UnicodeError, yaml.YAMLError, ValueError, TypeError, ConfigurationError):
        raise ConfigurationError("The bundled Windows application catalogue is missing or invalid.") from None
    return entries
