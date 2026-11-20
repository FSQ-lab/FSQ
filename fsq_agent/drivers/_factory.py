# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import cast

from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver
from fsq_agent.drivers.ios._appium_connect import AppiumConnectIOSDriver
from fsq_agent.drivers.macos._appium_connect import AppiumConnectMacOSDriver
from fsq_agent.drivers.web._playwright import PlaywrightWebDriver
from fsq_agent.drivers.windows._pywinauto import PywinautoWindowsDriver
from fsq_agent.models import ConfigurationError, HarnessPlatform

_BACKENDS = {
    "android": "appium_connect_agent",
    "web": "playwright",
    "windows": "pywinauto",
    "macos": "appium_connect_agent",
    "ios": "appium_connect_agent",
}
_DRIVER_CLASSES: dict[HarnessPlatform, type[object]] = {
    "android": cast("type[object]", AppiumConnectAndroidDriver),
    "web": cast("type[object]", PlaywrightWebDriver),
    "windows": cast("type[object]", PywinautoWindowsDriver),
    "macos": cast("type[object]", AppiumConnectMacOSDriver),
    "ios": cast("type[object]", AppiumConnectIOSDriver),
}


def _driver_class_for_backend(platform: HarnessPlatform, backend: str | None = None) -> type[object]:
    selected = backend or _BACKENDS[platform]
    _ensure_backend(platform, selected)
    return _DRIVER_CLASSES[platform]


def _ensure_backend(platform: HarnessPlatform, backend: str) -> None:
    if backend != _BACKENDS[platform]:
        raise ConfigurationError(f"Unsupported {platform} harness backend.", context={"platform": platform, "backend": backend, "supported": [_BACKENDS[platform]]})


class _DriverFactoryImplementation:
    def create_android_driver(self, settings, *, app_id=None, serial=None):
        _ensure_backend("android", settings.backend)
        return AppiumConnectAndroidDriver(
            app_id=app_id if app_id is not None else settings.app_id or "",
            serial=serial if serial is not None else settings.serial,
            uiautomator2_wait_for_idle_timeout_ms=settings.uiautomator2_wait_for_idle_timeout_ms,
            new_command_timeout_seconds=settings.new_command_timeout_seconds,
        )

    def create_web_driver(self, settings):
        _ensure_backend("web", settings.backend)
        viewport = None if settings.viewport_width is None or settings.viewport_height is None else (settings.viewport_width, settings.viewport_height)
        return PlaywrightWebDriver(
            channel=settings.channel,
            executable_path=settings.browser_executable_path,
            headless=settings.headless,
            base_url=settings.base_url,
            viewport=viewport,
            attach=settings.attach,
            attach_endpoint=settings.attach_endpoint,
        )

    def create_windows_driver(self, settings):
        _ensure_backend("windows", settings.backend)
        return PywinautoWindowsDriver(app_path=settings.app_path, backend_kind=settings.backend_kind, window_title_re=settings.window_title_re, launch_args=settings.launch_args)

    def create_macos_driver(self, settings, *, bundle_id=None):
        _ensure_backend("macos", settings.backend)
        return AppiumConnectMacOSDriver(
            bundle_id=bundle_id if bundle_id is not None else settings.bundle_id,
            app_path=settings.app_path,
            page_source_max_depth=settings.page_source_max_depth,
            action_timeout_seconds=settings.action_timeout_seconds,
            new_command_timeout_seconds=settings.new_command_timeout_seconds,
        )

    def create_ios_driver(self, settings, *, bundle_id=None, udid=None):
        _ensure_backend("ios", settings.backend)
        return AppiumConnectIOSDriver(
            udid=udid if udid is not None else settings.udid,
            bundle_id=bundle_id if bundle_id is not None else settings.bundle_id,
            app_path=settings.app_path,
            page_source_max_depth=settings.page_source_max_depth,
            action_timeout_seconds=settings.action_timeout_seconds,
            new_command_timeout_seconds=settings.new_command_timeout_seconds,
            wda_launch_timeout_ms=settings.wda_launch_timeout_ms,
            xcuitest_wait_for_idle_timeout_seconds=settings.xcuitest_wait_for_idle_timeout_seconds,
            xcuitest_snapshot_max_depth=settings.xcuitest_snapshot_max_depth,
            xcuitest_accessibility_deadline_seconds=settings.xcuitest_accessibility_deadline_seconds,
            xcuitest_animation_cool_off_timeout_seconds=settings.xcuitest_animation_cool_off_timeout_seconds,
            xcuitest_page_source_excluded_attributes=settings.xcuitest_page_source_excluded_attributes,
            xcode_org_id=settings.xcode_org_id,
            xcode_signing_id=settings.xcode_signing_id,
            updated_wda_bundle_id=settings.updated_wda_bundle_id,
        )
