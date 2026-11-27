# Platform Prerequisites

`pip install fsq-agent` installs supported Python Driver/Runtime packages. FSQ does not install, start, or modify browsers, applications, devices, ADB, Appium servers, or other host services.

## Web

Provide an installed Chromium-family browser. Supported channels are `chromium`, Chrome stable/beta/dev/canary, and Microsoft Edge stable/beta/dev/canary. `fsq init` discovers an exact channel match when there is one; pass `--browser-executable-path` when discovery is ambiguous or the installation uses a non-standard location. Firefox and WebKit are outside the current Web target contract. FSQ does not install or start the browser during readiness checks.

## Android

ADB and Node.js (`^20.19.0 || ^22.12.0 || >=24.0.0`) must be on `PATH`, `JAVA_HOME` and `ANDROID_HOME`/`ANDROID_SDK_ROOT` must point to valid JDK and Android SDK installations, the managed Appium 3.7.0 + UiAutomator2 8.7.0 runtime root (`~/.appium-connect-agent/`) must be initialized via `Runtime(LocalSettings()).install(InstallRequest())`, an online authorized device must be visible to an existing local ADB server, and the target application must already be installed. Device connection, managed runtime preparation, and authorization remain operator responsibilities; FSQ does not connect, authorize, or install devices, runtimes, or applications during readiness checks.

## Windows

Run on Windows with an existing application path and an interactive desktop session that can expose the target UI. The package includes pywinauto; application installation, startup prerequisites, permissions, and desktop-session availability remain operator responsibilities.

Windows Quick Test includes application presets for Edge, Chrome, Edge Beta, Edge Canary, Notepad, and Calculator. Applications must already be installed and accessible in the interactive desktop session.

Optional `--sandbox wsb` on `fsq quick-test run`, `fsq case create`, and `fsq case test` (`--platform windows` only) requires Windows 11 with the Windows Sandbox optional feature (`wsb.exe`) enabled, an unlocked interactive host desktop session, no other running Windows Sandbox instance, and host Visual C++/MFC runtime DLLs (`mfc140u.dll`, `msvcp140.dll`, `vcruntime140.dll`, `vcruntime140_1.dll`). Run `fsq quick-test context --platform windows --sandbox wsb` or `fsq doctor --sandbox wsb` to check Windows Sandbox host readiness without starting a Sandbox VM. Target applications must exist inside the fresh Sandbox image (for example Microsoft Edge) unless already accessible within the mounted Workspace.

## macOS

Run on macOS with full Xcode, Node.js (`^20.19.0 || ^22.12.0 || >=24.0.0`) on `PATH`, the managed Appium 3.7.0 + Mac2 driver runtime root (`~/.appium-connect-agent/`), and an existing application identified by bundle id, application path, or both. Command Line Tools alone are insufficient.

1. Install full Xcode from the Mac App Store, launch it once, and complete Apple's requested setup.
2. Select it as the active developer directory and accept its license when required. If Xcode is installed elsewhere, use that installation's `Contents/Developer` path:

   ```bash
   sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
   sudo xcodebuild -license accept
   ```

3. Ensure Node.js (`^20.19.0 || ^22.12.0 || >=24.0.0`) is on `PATH`, then initialize the managed Appium 3.7.0 runtime and official Mac2 driver once in the FSQ Python environment when needed:

   ```bash
   python -c "from appium_connect_agent.contracts import LocalSettings; from appium_connect_agent.runtime import InstallRequest, Runtime; Runtime(LocalSettings()).install(InstallRequest())"
   python -c "from appium_connect_agent.contracts import LocalSettings; from appium_connect_agent.runtime import Runtime; Runtime(LocalSettings()).ensure_managed_driver('mac2')"
   ```

4. Configure an existing application bundle or executable path, a bundle identifier, or both, then run `fsq doctor` from the registered Workspace root. For Microsoft Edge Stable, the usual values are `/Applications/Microsoft Edge.app` and `com.microsoft.edgemac`. When both are configured, the identifier must match the specified application's `Info.plist`; an executable inside a bundle uses the enclosing bundle's identity.

Doctor still lists independent host checks if the configured application has been moved or uninstalled. Restore it or update its configured path. A damaged `Info.plist` affects the bundle identifier check without discarding the other results. An invalid Workspace document or mismatched Workspace identity must be repaired before target diagnosis.

If macOS prompts for Accessibility or Automation access during an actual test, grant only the permissions appropriate for the operator's environment. Doctor does not trigger those prompts or claim that permission state is ready. It reports full Xcode, the active developer directory, the managed Appium + Mac2 runtime, application path, and bundle identifier separately. Normal execution starts and stops its own managed loopback Appium process and Mac2 session on demand; FSQ does not install these dependencies, accept licenses, change system settings, grant permissions, start Appium, or launch the target during diagnosis.

Run `fsq doctor` from the exact registered Workspace root. Doctor does not authenticate interactively, invoke a model, launch a target, create an external session, or repair the host.

## iOS

Use a macOS host with full Xcode selected, Node.js on `PATH`, and the managed Appium runtime with the XCUITest driver installed. Run `fsq quick-test context --platform ios` to view Simulator and physical-device choices. Run `fsq quick-test run --platform ios --goal '<goal>'` to auto-select the sole booted Simulator or connected physical device, or pass `--target-id '<UDID>'` explicitly (required when multiple devices are available, and never silently replaced when unavailable). Context does not boot a Simulator, install an app, or start Appium or WDA.

For a Simulator, name an installed app, provide its exact bundle ID, or put its absolute local `.app` path in the goal. A physical device requires your local WDA signing configuration: `target.xcode_org_id` and `target.xcode_signing_id` in `~/.fsq/quick-test/.fsq/config/config.ios.yaml`, with `target.updated_wda_bundle_id` if your setup requires it. If the file is absent, use the existing `fsq init --platform ios` Workspace setup from the exact dedicated `quick-test` root with the `quick-test` name and a valid iOS application target. Keep signing values out of the goal. If this host cannot enumerate apps on the physical device, provide the exact installed app bundle ID; preflight cannot verify installation in this mode.
