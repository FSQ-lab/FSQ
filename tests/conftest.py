# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def android_sdk():
    # The pinned SDK imports POSIX coordination with its public Runtime API.
    # This prerequisite is for SDK-dependent proofs, not for pure FSQ tests.
    if sys.platform == "win32":
        pytest.skip("Pinned appium-connect-agent public Runtime/Automation APIs require POSIX fcntl and pwd; FSQ's other backends remain portable.")
    from appium_connect_agent import automation, runtime

    return SimpleNamespace(automation=automation, runtime=runtime)


@pytest.fixture
def macos_android_sdk():
    # Runtime.connect is explicitly macOS-only in the pinned SDK contract.
    # Reject the fixture before importing the SDK or starting a wire peer.
    if sys.platform != "darwin":
        pytest.skip("Pinned appium-connect-agent Runtime connections require macOS; real SDK integration is exercised by the macOS CI job.")
    from appium_connect_agent import automation, runtime

    return SimpleNamespace(automation=automation, runtime=runtime)
