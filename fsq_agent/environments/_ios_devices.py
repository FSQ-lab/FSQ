# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.environments.providers._ios import discover_ios_devices
from fsq_agent.models import IOSDeviceDiscoveryResult


class IOSDeviceDiscovery:
    def discover(self, *, timeout_seconds: float = 5.0) -> IOSDeviceDiscoveryResult:
        return discover_ios_devices(timeout_seconds)
