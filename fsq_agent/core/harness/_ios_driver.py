# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import sys

from fsq_agent.core.interfaces import _ios_driver as _canonical

sys.modules[__name__] = _canonical
