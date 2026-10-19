import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { controlPlaneClient } from '../../api/controlPlaneClient';
import type { BootstrapResponse, DiagnosticDetail, QuickTestContext, RunSnapshot } from '../../api/types';
import { quickDiagnostics } from '../../test/quickTestFixtures';
import { QuickTestPage } from './QuickTestPage';
import { useQuickTest } from './useQuickTest';

const stream = vi.hoisted(() => ({ snapshot: null as RunSnapshot | null }));
vi.mock('../devices/hooks/useRunStream', () => ({
  useRunStream: () => ({ snapshot: stream.snapshot, connection: 'idle', error: null }),
}));

type Choice = QuickTestContext['choices'][number];
const phone = (id: string, selectable = true, status: Choice['status'] = 'ready'): Choice => ({ id, label: id, selectable, status });
const bootstrap: BootstrapResponse = {
  apiVersion: '1', platforms: [{ id: 'android', label: 'Android' }, { id: 'web', label: 'Web' }, { id: 'macos', label: 'macOS' }],
  busy: false, activeTask: null,
};
function context(choices: Choice[], targetId?: string, platform: QuickTestContext['platform'] = 'android'): QuickTestContext {
  const diagnostics = quickDiagnostics(platform, targetId ?? null);
  const selected = choices.some(choice => choice.id === targetId && choice.selectable && choice.status === 'ready');
  diagnostics.checks.configuration = { status: 'ready', code: null, message: 'Configuration loaded', action: '' };
  diagnostics.commands.prepare = selected
    ? { status: 'ready', code: null, message: 'Ready to identify application', action: '' }
    : { status: 'unavailable', code: 'device.selection_required', message: 'Select an available device', action: 'Select a device.' };
  return { platform, targetId: targetId ?? null, choices, checks: [], diagnostics, canStart: selected };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
let inventory: Choice[];
beforeEach(() => {
  stream.snapshot = null;
  inventory = [phone('one')];
  vi.spyOn(controlPlaneClient, 'bootstrap').mockResolvedValue(bootstrap);
  vi.spyOn(controlPlaneClient, 'quickTestContext').mockImplementation(async (platform, target) => context(inventory, target, platform));
  vi.spyOn(controlPlaneClient, 'startQuickTest').mockResolvedValue({ requestId: 'q1' });
});
afterEach(() => vi.restoreAllMocks());

function setup() {
  const hook = renderHook(({ active }) => useQuickTest(active), { initialProps: { active: true } });
  act(() => { hook.result.current.setPlatform('android'); hook.result.current.setGoal('Verify the welcome page'); });
  return hook;
}
async function checked(hook: ReturnType<typeof setup>) {
  await waitFor(() => expect(hook.result.current.context).not.toBeNull());
}

it.each([
  { name: 'zero', choices: [], expected: '' },
  { name: 'one online', choices: [phone('one')], expected: 'one' },
  { name: 'multiple online', choices: [phone('one'), phone('two')], expected: '' },
  { name: 'one online plus offline and unauthorized', choices: [phone('one'), phone('offline', false, 'unavailable'), phone('unauthorized', false, 'unavailable')], expected: 'one' },
  { name: 'only offline and unauthorized', choices: [phone('offline', false, 'unavailable'), phone('unauthorized', false, 'unavailable')], expected: '' },
  { name: 'selectable but unavailable', choices: [phone('one', true, 'unavailable')], expected: '' },
  { name: 'ready but not selectable', choices: [phone('one', false)], expected: '' },
])('selects only the sole eligible Android device: $name', async ({ choices, expected }) => {
  inventory = choices;
  const hook = setup();
  await checked(hook);
  expect(hook.result.current.targetId).toBe(expected);
  expect(hook.result.current.canStart).toBe(Boolean(expected));
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it('waits for new selected-device facts rather than using the empty-target context', async () => {
  const selected = deferred<QuickTestContext>();
  vi.mocked(controlPlaneClient.quickTestContext).mockImplementation((_platform, target) => target ? selected.promise : Promise.resolve(context(inventory)));
  const hook = setup();
  await waitFor(() => expect(hook.result.current.targetId).toBe('one'));
  expect(hook.result.current.context).toBeNull();
  expect(hook.result.current.canStart).toBe(false);
  expect(controlPlaneClient.quickTestContext).toHaveBeenLastCalledWith('android', 'one', expect.any(AbortSignal));
  await act(async () => selected.resolve(context(inventory, 'one')));
  await waitFor(() => expect(hook.result.current.canStart).toBe(true));
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
  await act(async () => hook.result.current.start());
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledExactlyOnceWith({ platform: 'android', targetId: 'one', goal: 'Verify the welcome page' });
});

it.each(['provider', 'mismatch', 'request error'] as const)('never bypasses selected-context %s failure', async failure => {
  vi.mocked(controlPlaneClient.quickTestContext).mockImplementation(async (_platform, target) => {
    if (!target) return context(inventory);
    if (failure === 'request error') throw new Error('Discovery failed');
    const data = context(inventory, failure === 'mismatch' ? 'other' : target);
    if (failure === 'provider') {
      const error: DiagnosticDetail = { status: 'error', code: 'provider.unavailable', message: 'Provider unavailable', action: 'Check Settings.' };
      data.diagnostics!.checks.provider = error;
      data.diagnostics!.commands.prepare = error;
      data.canStart = false;
    }
    return data;
  });
  const hook = setup();
  await waitFor(() => expect(hook.result.current.targetId).toBe('one'));
  await waitFor(() => expect(failure === 'provider' ? hook.result.current.context : hook.result.current.error).not.toBeNull());
  expect(hook.result.current.canStart).toBe(false);
  await act(async () => hook.result.current.start());
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it.each([0, 2])('auto-selects on refresh from %i eligible devices to one without prior intent', async count => {
  inventory = Array.from({ length: count }, (_, index) => phone(String(index)));
  const hook = setup();
  await checked(hook);
  expect(hook.result.current.targetId).toBe('');
  inventory = [phone('new')];
  act(() => hook.result.current.refresh());
  await waitFor(() => expect(hook.result.current.targetId).toBe('new'));
  await waitFor(() => expect(hook.result.current.canStart).toBe(true));
});

it.each(['missing', 'offline', 'configuration error', 'request error'] as const)('retains an existing serial through %s instead of falling back', async failure => {
  inventory = [phone('one'), phone('two')];
  const hook = setup();
  await checked(hook);
  act(() => hook.result.current.setTargetId('two'));
  await waitFor(() => expect(hook.result.current.canStart).toBe(true));
  inventory = failure === 'offline' ? [phone('one'), phone('two', false, 'unavailable')] : [phone('one')];
  if (failure === 'configuration error') {
    const data = context([], 'two');
    const error: DiagnosticDetail = { status: 'error', code: 'doctor.configuration_invalid', message: 'Configuration could not be loaded', action: 'Check Settings.' };
    data.diagnostics!.checks.configuration = error;
    data.diagnostics!.commands.prepare = error;
    vi.mocked(controlPlaneClient.quickTestContext).mockResolvedValue(data);
  } else if (failure === 'request error') {
    vi.mocked(controlPlaneClient.quickTestContext).mockRejectedValue(new Error('Discovery failed'));
  }
  act(() => hook.result.current.refresh());
  await waitFor(() => expect(failure === 'request error' ? hook.result.current.error : hook.result.current.context).not.toBeNull());
  expect(hook.result.current.targetId).toBe('two');
  expect(hook.result.current.canStart).toBe(false);
  expect(hook.result.current.goal).toBe('Verify the welcome page');
  expect(controlPlaneClient.quickTestContext).toHaveBeenLastCalledWith('android', 'two', expect.any(AbortSignal));
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it('preserves explicit clearing through refresh, mounted inactivity, New test and same-platform selection', async () => {
  const hook = setup();
  await waitFor(() => expect(hook.result.current.targetId).toBe('one'));
  act(() => hook.result.current.setTargetId(''));
  await checked(hook);
  for (const change of [
    () => hook.result.current.refresh(),
    () => hook.result.current.newTest(),
    () => hook.result.current.setPlatform('android'),
  ]) {
    act(change);
    await checked(hook);
    expect(hook.result.current.targetId).toBe('');
    expect(hook.result.current.canStart).toBe(false);
  }
  hook.rerender({ active: false });
  hook.rerender({ active: true });
  await checked(hook);
  expect(hook.result.current.targetId).toBe('');
  act(() => hook.result.current.setPlatform('web'));
  await checked(hook);
  expect(hook.result.current.targetId).toBe('');
  act(() => hook.result.current.setPlatform('android'));
  await waitFor(() => expect(hook.result.current.targetId).toBe('one'));
  act(() => hook.result.current.setTargetId(''));
  await checked(hook);
  hook.unmount();
  const fresh = setup();
  await waitFor(() => expect(fresh.result.current.targetId).toBe('one'));
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it('preserves a manually chosen available device on refresh', async () => {
  inventory = [phone('one'), phone('two')];
  const hook = setup();
  await checked(hook);
  act(() => hook.result.current.setTargetId('two'));
  await waitFor(() => expect(hook.result.current.canStart).toBe(true));
  act(() => hook.result.current.refresh());
  await checked(hook);
  expect(hook.result.current.targetId).toBe('two');
});

it.each(['clear', 'choose', 'platform'] as const)('does not let a late discovery override newer manual intent: %s', async intent => {
  const pending = deferred<QuickTestContext>();
  vi.mocked(controlPlaneClient.quickTestContext).mockReturnValueOnce(pending.promise);
  const hook = setup();
  await waitFor(() => expect(controlPlaneClient.quickTestContext).toHaveBeenCalledTimes(1));
  expect(hook.result.current.targetId).toBe('');
  expect(hook.result.current.canStart).toBe(false);
  // Resolve in the same batch as the newer intent, before effect cleanup.
  await act(async () => {
    if (intent === 'platform') hook.result.current.setPlatform('web');
    else hook.result.current.setTargetId(intent === 'choose' ? 'manual' : '');
    pending.resolve(context([phone('old')]));
  });
  expect(hook.result.current.targetId).toBe(intent === 'choose' ? 'manual' : '');
  expect(hook.result.current.context?.choices.some(choice => choice.id === 'old')).toBe(intent === 'clear');
  expect(hook.result.current.canStart).toBe(false);
});

it('does not use stale inventory after a failed refresh or an invalid transport response', async () => {
  inventory = [];
  const hook = setup();
  await checked(hook);
  vi.mocked(controlPlaneClient.quickTestContext).mockRejectedValueOnce(new Error('Discovery failed'));
  act(() => hook.result.current.refresh());
  await waitFor(() => expect(hook.result.current.error).not.toBeNull());
  expect(hook.result.current.targetId).toBe('');
  expect(hook.result.current.canStart).toBe(false);
  vi.mocked(controlPlaneClient.quickTestContext).mockRestore();
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...context([phone('one')]), choices: [{ id: 'one', selectable: 'yes', status: 'ready' }] })));
  act(() => hook.result.current.refresh());
  await waitFor(() => expect(hook.result.current.error?.code).toBe('invalid_response'));
  expect(hook.result.current.targetId).toBe('');
  expect(hook.result.current.canStart).toBe(false);
});

it('does not auto-select from configuration-error context', async () => {
  const data = context([]);
  const error: DiagnosticDetail = { status: 'error', code: 'doctor.configuration_invalid', message: 'Configuration unavailable', action: 'Check Settings.' };
  data.diagnostics!.checks.configuration = error;
  data.diagnostics!.commands.prepare = error;
  vi.mocked(controlPlaneClient.quickTestContext).mockResolvedValue(data);
  const hook = setup();
  await checked(hook);
  expect(hook.result.current.targetId).toBe('');
  expect(hook.result.current.canStart).toBe(false);
});

it.each(['preparing', 'running'] as const)('gives a restored %s task priority over pending discovery', async status => {
  const discovery = deferred<QuickTestContext>();
  const restore = deferred<BootstrapResponse>();
  vi.mocked(controlPlaneClient.quickTestContext).mockReturnValue(discovery.promise);
  const hook = setup();
  await waitFor(() => expect(hook.result.current.bootstrap).not.toBeNull());
  vi.mocked(controlPlaneClient.bootstrap).mockReturnValueOnce(restore.promise);
  act(() => hook.result.current.refresh());
  await act(async () => {
    restore.resolve({ ...bootstrap, busy: true, activeTask: { requestId: 'restored', workflow: 'quick-test', platform: 'android', targetId: 'frozen', status, runId: status === 'running' ? 'r1' : null, workspaceName: null, mode: 'explore', application: null, platformRevision: null } });
    discovery.resolve(context([phone('one')]));
  });
  expect(hook.result.current.targetId).toBe('frozen');
  expect(hook.result.current.locked).toBe(true);
  act(() => { hook.result.current.setTargetId(''); hook.result.current.setPlatform('web'); });
  expect(hook.result.current.targetId).toBe('frozen');
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it.each(['accepted', 'unknown'] as const)('locks Android intent during a pending Start and its %s outcome', async outcome => {
  const post = deferred<{ requestId: string }>();
  vi.mocked(controlPlaneClient.startQuickTest).mockReturnValue(post.promise);
  const hook = setup();
  await waitFor(() => expect(hook.result.current.canStart).toBe(true));
  act(() => { void hook.result.current.start(); hook.result.current.setTargetId(''); });
  expect(hook.result.current.targetId).toBe('one');
  expect(hook.result.current.locked).toBe(true);
  inventory = [phone('other')];
  act(() => hook.result.current.refresh());
  await act(async () => {
    if (outcome === 'accepted') post.resolve({ requestId: 'q1' });
    else post.reject(new TypeError('Response lost'));
  });
  expect(hook.result.current.targetId).toBe('one');
  expect(hook.result.current.locked).toBe(true);
  expect(hook.result.current.uncertain).toBe(outcome === 'unknown');
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
});

it('renders automatic selection, explicit clearing and retained missing identity in the existing Device control', async () => {
  const user = userEvent.setup();
  render(<QuickTestPage active onCaseManager={vi.fn()} onOpenReport={vi.fn()} />);
  await screen.findByRole('option', { name: 'Android' });
  await user.selectOptions(screen.getByLabelText('Platform'), 'android');
  const device = screen.getByRole('combobox', { name: 'Device' });
  await waitFor(() => expect(device).toHaveValue('one'));
  await user.selectOptions(device, '');
  await user.click(screen.getByRole('button', { name: 'Recheck environment' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Recheck environment' })).toBeEnabled());
  expect(device).toHaveValue('');
  await user.selectOptions(device, 'one');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Recheck environment' })).toBeEnabled());
  inventory = [phone('other')];
  await user.click(screen.getByRole('button', { name: 'Recheck environment' }));
  await screen.findByRole('option', { name: 'one — unavailable' });
  expect(device).toHaveValue('one');
  expect(screen.getByRole('option', { name: 'one — unavailable' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeDisabled();
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it.each([
  { name: 'empty identity', ids: [''] },
  { name: 'blank identity', ids: [' \t'] },
  { name: 'blank identity beside a valid device', ids: ['', 'valid'] },
])('F001: real decoder $name ends checking with a recoverable error, never fallback', async ({ ids }) => {
  inventory = ids.map(id => ({ ...phone(id), label: 'Discovery candidate' }));
  const requested: (string | null)[] = [];
  vi.mocked(controlPlaneClient.quickTestContext).mockRestore();
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = new URL(String(input), 'http://localhost');
    expect(url.pathname).toBe('/api/control-plane/quick-test/context');
    const target = url.searchParams.get('targetId');
    requested.push(target);
    return new Response(JSON.stringify(context(inventory, target ?? undefined)));
  });
  const user = userEvent.setup();
  render(<QuickTestPage active onCaseManager={vi.fn()} onOpenReport={vi.fn()} />);
  await screen.findByRole('option', { name: 'Android' });
  await user.selectOptions(screen.getByLabelText('Platform'), 'android');
  await screen.findByText('The Control Plane server returned an invalid response.');
  expect(screen.getByLabelText('Device')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeDisabled();
  const recheck = screen.getByRole('button', { name: 'Recheck environment' });
  expect(recheck).toBeEnabled();
  expect(screen.queryByText('Checking Android environment…')).not.toBeInTheDocument();
  expect(requested).toEqual([null]);
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();

  const serial = 'adb-device._@-1:5555';
  inventory = [phone(serial)];
  await user.click(recheck);
  await waitFor(() => expect(screen.getByLabelText('Device')).toHaveValue(serial));
  await waitFor(() => expect(recheck).toBeEnabled());
  expect(requested).toEqual([null, null, serial]);
  expect(screen.queryByText('The Control Plane server returned an invalid response.')).not.toBeInTheDocument();
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it.each(['emulator-5554', '192.0.2.1:5555', 'adb-device._@-1'])('F001: preserves opaque nonblank identity %s through the real decoder', async serial => {
  vi.mocked(controlPlaneClient.quickTestContext).mockRestore();
  vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
    const url = new URL(String(input), 'http://localhost');
    return new Response(JSON.stringify(context([phone(serial)], url.searchParams.get('targetId') ?? undefined)));
  });
  const hook = setup();
  await waitFor(() => expect(hook.result.current.canStart).toBe(true));
  expect(hook.result.current.targetId).toBe(serial);
  expect(hook.result.current.context?.targetId).toBe(serial);
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

async function nonAndroidSetup(platform: 'web' | 'macos' | 'windows') {
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue({
    ...bootstrap, platforms: [...bootstrap.platforms, { id: 'windows', label: 'Windows' }],
  });
  vi.mocked(controlPlaneClient.quickTestContext).mockImplementation(async (selectedPlatform, target) => {
    const data = context([phone('one')], target, selectedPlatform);
    if (selectedPlatform === 'macos' || selectedPlatform === 'windows') {
      data.canStart = true;
      data.diagnostics!.commands.prepare = { status: 'ready', code: null, message: 'Ready', action: '' };
    }
    return data;
  });
  const hook = renderHook(() => useQuickTest(true));
  act(() => { hook.result.current.setPlatform(platform); hook.result.current.setGoal('Verify the welcome page'); });
  await waitFor(() => expect(hook.result.current.context).not.toBeNull());
  if (platform === 'web') act(() => hook.result.current.setTargetId('one'));
  await waitFor(() => expect(hook.result.current.canStart).toBe(true));
  return hook;
}

it.each(['web', 'macos', 'windows'] as const)('F002: %s same-platform callback retains pre-request reset semantics', async platform => {
  const hook = await nonAndroidSetup(platform);
  const count = vi.mocked(controlPlaneClient.quickTestContext).mock.calls.length;
  act(() => hook.result.current.setPlatform(platform));
  expect(hook.result.current.targetId).toBe('');
  expect(hook.result.current.context).toBeNull();
  expect(hook.result.current.choices).toEqual([]);
  expect(hook.result.current.canStart).toBe(false);
  // Web's reset changes its target and requests fresh facts. Desktop has no
  // target change, so the baseline callback only invalidates its context.
  if (platform === 'web') await waitFor(() => expect(controlPlaneClient.quickTestContext).toHaveBeenCalledTimes(count + 1));
  else expect(controlPlaneClient.quickTestContext).toHaveBeenCalledTimes(count);
});

it.each(['web', 'macos', 'windows'] as const)('F002: %s same-target callback invalidates context as before', async platform => {
  const hook = await nonAndroidSetup(platform);
  const target = hook.result.current.targetId;
  const count = vi.mocked(controlPlaneClient.quickTestContext).mock.calls.length;
  act(() => hook.result.current.setTargetId(target));
  expect(hook.result.current.targetId).toBe(target);
  expect(hook.result.current.context).toBeNull();
  expect(hook.result.current.canStart).toBe(false);
  expect(controlPlaneClient.quickTestContext).toHaveBeenCalledTimes(count);
});

it.each(['web', 'macos', 'windows'] as const)('F002: %s same-target callback does not reject a baseline in-flight response', async platform => {
  const hook = await nonAndroidSetup(platform);
  const data = hook.result.current.context!;
  const pending = deferred<QuickTestContext>();
  vi.mocked(controlPlaneClient.quickTestContext).mockReturnValueOnce(pending.promise);
  act(() => hook.result.current.refresh());
  act(() => hook.result.current.setTargetId(hook.result.current.targetId));
  await act(async () => pending.resolve(data));
  expect(hook.result.current.context).toEqual(data);
  expect(hook.result.current.canStart).toBe(true);
});

it.each([
  { platform: 'web', setter: 'platform' }, { platform: 'web', setter: 'target' },
  { platform: 'macos', setter: 'platform' }, { platform: 'macos', setter: 'target' },
  { platform: 'windows', setter: 'platform' }, { platform: 'windows', setter: 'target' },
] as const)('F002: $platform same-batch Start/$setter preserves baseline callback semantics and global duplicate guard', async ({ platform, setter }) => {
  const hook = await nonAndroidSetup(platform);
  const post = deferred<{ requestId: string }>();
  vi.mocked(controlPlaneClient.startQuickTest).mockReturnValue(post.promise);
  const nextPlatform = platform === 'web' ? 'macos' : 'web';
  act(() => {
    void hook.result.current.start();
    if (setter === 'platform') hook.result.current.setPlatform(nextPlatform);
    else hook.result.current.setTargetId('other');
    void hook.result.current.start();
  });
  expect(hook.result.current.platform).toBe(setter === 'platform' ? nextPlatform : platform);
  expect(hook.result.current.targetId).toBe(setter === 'platform' ? '' : 'other');
  expect(hook.result.current.locked).toBe(true);
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledExactlyOnceWith({
    platform, goal: 'Verify the welcome page', ...(platform === 'web' ? { targetId: 'one' } : {}),
  });
  // Once locked state is committed, the pre-existing global guard still applies.
  act(() => { hook.result.current.setPlatform('android'); hook.result.current.setTargetId('late'); });
  expect(hook.result.current.platform).toBe(setter === 'platform' ? nextPlatform : platform);
  expect(hook.result.current.targetId).toBe(setter === 'platform' ? '' : 'other');
  await act(async () => post.resolve({ requestId: 'q1' }));
});

const takeoverResponses = (['web', 'macos', 'windows'] as const).flatMap(platform =>
  (['success', 'error'] as const).map(response => ({ platform, response })));

it.each(takeoverResponses)('F004: Android restore wins over same-batch late $platform/$response, including New test', async ({ platform, response }) => {
  const hook = await nonAndroidSetup(platform);
  const oldTarget = hook.result.current.targetId || undefined;
  const late = deferred<QuickTestContext>();
  const restore = deferred<BootstrapResponse>();
  const task: RunSnapshot = {
    requestId: 'restored-android', workflow: 'quick-test', platform: 'android', targetId: 'frozen',
    workspaceName: 'quick-test', runId: 'restored-run', mode: 'explore', status: 'running',
    application: null, platformRevision: null, source: { goal: 'Verify the restored goal' },
    startedAt: '', completedAt: null, cancelRequested: false, events: [], activeStep: null,
    result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false, error: null,
  };
  vi.mocked(controlPlaneClient.quickTestContext).mockReturnValueOnce(late.promise);
  vi.mocked(controlPlaneClient.bootstrap).mockReturnValueOnce(restore.promise);
  act(() => hook.result.current.refresh());
  expect(controlPlaneClient.quickTestContext).toHaveBeenLastCalledWith(platform, oldTarget, expect.any(AbortSignal));
  expect(hook.result.current.context).toBeNull();

  // Both continuations are queued in one React batch. The server restores its
  // Android identity first; the old request settles before effect cleanup.
  await act(async () => {
    restore.resolve({ ...bootstrap, busy: true, activeTask: task });
    if (response === 'success') late.resolve(context([phone('obsolete')], oldTarget, platform));
    else late.reject(new Error('Obsolete platform discovery failed'));
  });
  expect(hook.result.current.platform).toBe('android');
  expect(hook.result.current.targetId).toBe('frozen');
  expect(hook.result.current.context).toBeNull();
  expect(hook.result.current.error).toBeNull();
  expect(hook.result.current.choices.some(choice => choice.id === 'obsolete')).toBe(false);
  expect(hook.result.current.locked).toBe(true);
  act(() => {
    hook.result.current.setPlatform('web');
    hook.result.current.setTargetId('');
    hook.result.current.newTest();
  });
  expect(hook.result.current.targetId).toBe('frozen');
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();

  stream.snapshot = { ...task, status: 'success', terminal: true };
  hook.rerender();
  await waitFor(() => expect(hook.result.current.snapshot?.terminal).toBe(true));
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue(bootstrap);
  vi.mocked(controlPlaneClient.quickTestContext).mockImplementation(async (nextPlatform, target) => context([phone('other')], target, nextPlatform));
  act(() => hook.result.current.newTest());
  await waitFor(() => expect(hook.result.current.context?.platform).toBe('android'));
  expect(hook.result.current.targetId).toBe('frozen');
  expect(hook.result.current.context?.targetId).toBe('frozen');
  expect(hook.result.current.goal).toBe('Verify the restored goal');
  expect(hook.result.current.canStart).toBe(false);
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it.each(takeoverResponses)('F004: explicit Android takeover rejects a pre-takeover $platform/$response without waiting for cleanup', async ({ platform, response }) => {
  const hook = await nonAndroidSetup(platform);
  const oldTarget = hook.result.current.targetId || undefined;
  const late = deferred<QuickTestContext>();
  const android = deferred<QuickTestContext>();
  vi.mocked(controlPlaneClient.quickTestContext).mockReturnValueOnce(late.promise).mockReturnValueOnce(android.promise);
  act(() => hook.result.current.refresh());
  await act(async () => {
    hook.result.current.setPlatform('android');
    hook.result.current.setTargetId('chosen');
    if (response === 'success') late.resolve(context([phone('obsolete')], oldTarget, platform));
    else late.reject(new Error('Obsolete platform discovery failed'));
  });
  expect(hook.result.current.platform).toBe('android');
  expect(hook.result.current.targetId).toBe('chosen');
  expect(hook.result.current.context).toBeNull();
  expect(hook.result.current.error).toBeNull();
  expect(hook.result.current.choices).toEqual([]);
  expect(hook.result.current.canStart).toBe(false);
  await act(async () => android.resolve(context([phone('chosen')], 'chosen')));
  await waitFor(() => expect(hook.result.current.canStart).toBe(true));
  expect(hook.result.current.targetId).toBe('chosen');
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});
