# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import importlib

from fsq_agent import core
from fsq_agent.core import evidence, interfaces, runner


def test_core_root_reexports_canonical_subpackage_objects() -> None:
    assert core.StepRunner is runner.StepRunner
    assert core.StepSequenceRunner is runner.StepSequenceRunner
    assert core.ArtifactStore is evidence.ArtifactStore
    assert core.EvidenceRecorder is evidence.EvidenceRecorder
    assert core.HarnessInterface is interfaces.HarnessInterface
    assert core.DriverFactory is interfaces.DriverFactory
    assert core.HarnessFactory is interfaces.HarnessFactory
    assert isinstance(core.CapabilityRegistry(), interfaces.CapabilityRegistryInterface)
    assert isinstance(core.RuntimeSecretStore.empty(), interfaces.RuntimeSecretResolver)


def test_harness_protocol_compatibility_modules_alias_canonical_modules() -> None:
    pairs = [
        ("fsq_agent.core.harness._interface", "fsq_agent.core.interfaces._harness"),
        ("fsq_agent.core.harness._android_driver", "fsq_agent.core.interfaces._android_driver"),
        ("fsq_agent.core.harness._web_driver", "fsq_agent.core.interfaces._web_driver"),
        ("fsq_agent.core.harness._windows_driver", "fsq_agent.core.interfaces._windows_driver"),
        ("fsq_agent.core.harness._macos_driver", "fsq_agent.core.interfaces._macos_driver"),
    ]

    for legacy_name, canonical_name in pairs:
        assert importlib.import_module(legacy_name) is importlib.import_module(canonical_name)


def test_concrete_driver_and_harness_compatibility_modules_alias_canonical_modules() -> None:
    pairs = [
        ("fsq_agent.core.harness._appium_connect_android_driver", "fsq_agent.drivers.android._appium_connect"),
        ("fsq_agent.core.harness._playwright_driver", "fsq_agent.drivers.web._playwright"),
        ("fsq_agent.core.harness._pywinauto_driver", "fsq_agent.drivers.windows._pywinauto"),
        ("fsq_agent.core.harness._appium_mac2_driver", "fsq_agent.drivers.macos._appium_mac2"),
        ("fsq_agent.core.harness._android", "fsq_agent.harnesses._android"),
        ("fsq_agent.core.harness._web", "fsq_agent.harnesses._web"),
        ("fsq_agent.core.harness._windows", "fsq_agent.harnesses._windows"),
        ("fsq_agent.core.harness._macos", "fsq_agent.harnesses._macos"),
    ]

    for legacy_name, canonical_name in pairs:
        assert importlib.import_module(legacy_name) is importlib.import_module(canonical_name)


def test_case_dsl_and_runtime_compatibility_objects_are_canonical() -> None:
    legacy_fsq = importlib.import_module("fsq_agent.fsq")
    canonical_fsq = importlib.import_module("fsq_agent.case_dsl")
    assert legacy_fsq.FsqCaseLoader is canonical_fsq.FsqCaseLoader
    assert legacy_fsq.FsqExecutableStepAdapter is canonical_fsq.FsqExecutableStepAdapter
    assert importlib.import_module("fsq_agent.fsq._loader") is importlib.import_module("fsq_agent.case_dsl._loader")
    assert importlib.import_module("fsq_agent.fsq._step_adapter") is importlib.import_module("fsq_agent.case_dsl._step_adapter")

    core = importlib.import_module("fsq_agent.core")
    environments = importlib.import_module("fsq_agent.environments")
    assert core.PlatformRuntimeService is environments.PlatformRuntimeService
    assert importlib.import_module("fsq_agent.core._platform_runtime") is importlib.import_module("fsq_agent.environments._service")
