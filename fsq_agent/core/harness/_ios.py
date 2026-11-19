# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import importlib
import sys

_canonical = importlib.import_module("fsq_agent.harnesses._ios")
sys.modules[__name__] = _canonical
