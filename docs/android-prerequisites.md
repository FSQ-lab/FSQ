# Android installation and troubleshooting

FSQ checks ADB, the `appium-connect-agent` Python dependency and managed Appium 3.7.0 + UiAutomator2 8.7.0 runtime (`~/.appium-connect-agent/` plus Node.js, JDK, and Android SDK layout), an existing local ADB server, device connection and authorization, device selection, and the application package. It does not install software, start services, grant permissions, or initialize device automation during diagnosis.

1. Install Android SDK Platform-Tools, Node.js (`^20.19.0 || ^22.12.0 || >=24.0.0`), and a JDK (`JAVA_HOME`), and ensure `adb` and `node` are on `PATH` with `ANDROID_HOME` or `ANDROID_SDK_ROOT` pointing to the SDK root. Initialize the managed Appium runtime once in the FSQ Python environment when needed:

```bash
python -c "from appium_connect_agent.runtime import InstallRequest, Runtime; Runtime().install(InstallRequest())"
```

2. Start the default local ADB server manually in your terminal when needed:

```bash
adb start-server
```

3. Connect your device, unlock it, enable Developer options and USB debugging manually, and accept the computer's authorization prompt. An `unauthorized` device needs authorization; an `offline` device needs its connection inspected. FSQ never revokes authorizations or restarts devices automatically.
4. Select the intended online device in Control Plane. When multiple devices are online, CLI Doctor reports selection required; it does not offer a persisted serial setting. With one online authorized device, CLI Doctor can diagnose that device.
5. Install the intended application manually on that device, or correct the App ID through Workspace configuration. A failed or timed-out package query is not proof of a missing app.
6. Select **Recheck environment**. Goal and Case drafts are preserved. Start checks the selected device and effective application again.

## ADB side effects and endpoint scope

Ordinary ADB commands such as `adb devices` can automatically start an ADB server. FSQ diagnosis instead uses bounded protocol reads against an already-running server, with no auto-start fallback even if the server disappears between requests. The server defaults to `127.0.0.1:5037`. Trusted process-level `ANDROID_ADB_SERVER_HOST` and `ANDROID_ADB_SERVER_PORT` can specify an IPv4 loopback endpoint. Explicitly blank values, IPv6, non-loopback, malformed, or conflicting `ADB_SERVER_SOCKET`/`ADB_SERVER_PORT` overrides require manual repair. IPv6 is unsupported because the selected automation backend uses IPv4 sockets. A non-default endpoint needs matching operator startup instructions, not the default command above.

Ready means the listed prerequisites passed. It does not certify managed Appium process startup, device-side UiAutomator2 session initialization, automation permissions, or a particular Case. Normal execution starts and stops its own managed loopback Appium process and Automation session after preflight. Provider is required for Explore and AI-assisted Strict steps, but not ordinary provider-free Strict Replay.

## Owned resources and terminal cleanup

Readiness does not create an Automation Session or exercise terminal cleanup. Normal execution owns its managed Appium service and Session; a Python integration that constructs a Harness owns that Harness until `close()` succeeds. Closing does not restore application data or device state and is not the recordable `kill_app` capability.

An active owned Session normally closes with SDK `Session.quit()` followed by managed-scope completion. An uncertain or disposing Session requires explicit SDK `Session.dispose(control=...)`. Successful disposal settles its local client and owned managed resources; it does not assert that Appium received DELETE or that the remote Session is closed. Ordinary observations and actions do not dispose, reconnect, or rebuild a non-active Session. Without explicit disposal, SDK quarantine remains in effect.

FSQ retains pending Session/Runtime/scope ownership and the required owner thread when cleanup fails. A later explicit close continues incomplete cleanup without repeating completed stages or business actions. Cleanup failures remain visible alongside the primary failure, including when execution already returned a failed step; cancellation remains the primary cancellation with secondary cleanup diagnostics. Python callers should inspect exception causes and exception-group members rather than treating a final answer, native result, worker-result file, or warning as resource-settlement proof. Independent supervisors must settle only resources whose ownership they actually established, not infer ownership from a port or stale service record.

A Coding Agent runtime instance that encounters Harness or Provider cleanup failure retains the failed resources and rejects subsequent operations before constructing replacements. It has no implicit cleanup retry or acknowledgment API; separately settling a retained Harness does not make that runtime instance reusable.

The terminal-disposal integration requires the SDK public `Session.dispose` contract. The repository dependency remains pinned to its existing immutable revision until a reviewed SDK revision is available; local source-overlay validation does not upgrade that dependency or establish a release candidate. An older SDK without this public API cannot complete uncertain terminal disposal: FSQ reports the failure and retains ownership rather than accessing private leases or silently reporting success. Frozen evaluation installations, historical results, and shared SDK records/locks are not migration inputs and must not be rewritten for this repair.
