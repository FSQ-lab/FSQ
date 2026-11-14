# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import importlib
import sys

_canonical = importlib.import_module("fsq_agent.drivers.android._appium_connect")
sys.modules[__name__] = _canonical
