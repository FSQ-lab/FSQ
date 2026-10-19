import { act, renderHook, waitFor } from '@testing-library/react';
import { controlPlaneClient, ControlPlaneApiError } from '../../api/controlPlaneClient';
import type { BootstrapResponse, QuickTestContext, RunSnapshot } from '../../api/types';
import { useQuickTest } from './useQuickTest';
import { quickDiagnostics } from '../../test/quickTestFixtures';

const bootstrap: BootstrapResponse = { apiVersion: '1', platforms: [{ id: 'android', label: 'Android' }, { id: 'web', label: 'Web' }, { id: 'macos', label: 'macOS' }, { id: 'windows', label: 'Windows' }], busy: false, activeTask: null };
const prep: RunSnapshot = { requestId: 'q1', workflow: 'quick-test', workspaceName: null, platform: 'macos', targetId: null, application: null, platformRevision: null, runId: null, mode: 'explore', status: 'preparing', source: { goal: 'Test Notes' }, startedAt: '', completedAt: null, cancelRequested: false, events: [], activeStep: null, result: null, summary: 'Preparing', screenshotRevision: 0, uiSnapshotRevision: 0, evidenceAvailable: false, reportAvailable: false, terminal: false };
const context = (platform: QuickTestContext['platform'], targetId?: string): QuickTestContext => ({ platform, targetId: targetId ?? null, canStart: true, choices: ['one', 'two'].map(id => ({ id, label: id, selectable: true, status: 'ready' })), checks: [], diagnostics: quickDiagnostics(platform, targetId ?? null) });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
beforeEach(() => {
  vi.spyOn(controlPlaneClient, 'bootstrap').mockResolvedValue(bootstrap);
  vi.spyOn(controlPlaneClient, 'quickTestContext').mockImplementation(async (platform, target) => context(platform, target));
  vi.spyOn(controlPlaneClient, 'startQuickTest').mockResolvedValue({ requestId: 'q1' });
  vi.spyOn(controlPlaneClient, 'runSnapshot').mockResolvedValue(prep);
  vi.spyOn(controlPlaneClient, 'cancelRun').mockResolvedValue({ ...prep, cancelRequested: true });
});
afterEach(() => vi.restoreAllMocks());
it('rejects late device/platform checks and mismatched server bindings', async () => {
  const old = deferred<QuickTestContext>();
  const { result } = renderHook(() => useQuickTest(true));
  await waitFor(() => expect(result.current.bootstrap).not.toBeNull());
  act(() => { result.current.setPlatform('android'); result.current.setGoal('Test'); });
  await waitFor(() => expect(result.current.context?.platform).toBe('android'));
  vi.mocked(controlPlaneClient.quickTestContext).mockImplementationOnce(() => old.promise);
  act(() => result.current.setTargetId('one'));
  act(() => result.current.setTargetId('two'));
  await waitFor(() => expect(result.current.canStart).toBe(true));
  await act(async () => old.resolve(context('android', 'one')));
  expect(result.current.context?.targetId).toBe('two');
  vi.mocked(controlPlaneClient.quickTestContext).mockResolvedValueOnce(context('android', 'two'));
  act(() => result.current.setPlatform('web'));
  await waitFor(() => expect(result.current.error?.code).toBe('context_mismatch'));
  expect(result.current.canStart).toBe(false);
});
it('reconciles an unknown POST response into the server task without a duplicate start', async () => {
  const { result } = renderHook(() => useQuickTest(true));
  act(() => { result.current.setPlatform('macos'); result.current.setGoal('Test Notes'); });
  await waitFor(() => expect(result.current.canStart).toBe(true));
  vi.mocked(controlPlaneClient.startQuickTest).mockRejectedValue(new TypeError('Network response lost'));
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue({ ...bootstrap, busy: true, activeTask: prep });
  await act(async () => { await result.current.start(); });
  await waitFor(() => expect(result.current.snapshot?.requestId).toBe('q1'));
  await act(async () => { await result.current.start(); });
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
  expect(result.current.canStart).toBe(false);
});
it('keeps an unknown outcome locked until explicit successful reconciliation', async () => {
  const { result } = renderHook(() => useQuickTest(true));
  act(() => { result.current.setPlatform('windows'); result.current.setGoal('Test Notes'); });
  await waitFor(() => expect(result.current.canStart).toBe(true));
  vi.mocked(controlPlaneClient.startQuickTest).mockRejectedValue(new TypeError('Response lost'));
  await act(async () => { await result.current.start(); });
  expect(result.current.uncertain).toBe(true);
  expect(result.current.canStart).toBe(false);
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.canStart).toBe(true));
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
});
it('returning restores a running task; navigation/unmount never cancels', async () => {
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue({ ...bootstrap, busy: true, activeTask: prep });
  vi.mocked(controlPlaneClient.runSnapshot).mockResolvedValue({ ...prep, workspaceName: 'quick-test', runId: 'r1', application: 'Notes', status: 'running' });
  const { result, rerender, unmount } = renderHook(({ active }) => useQuickTest(active), { initialProps: { active: true } });
  await waitFor(() => expect(result.current.snapshot?.status).toBe('running'));
  rerender({ active: false });
  rerender({ active: true });
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
  unmount();
  expect(controlPlaneClient.cancelRun).not.toHaveBeenCalled();
});
it('service restart has no implicit resume or fabricated terminal success', async () => {
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValueOnce({ ...bootstrap, busy: true, activeTask: prep });
  vi.mocked(controlPlaneClient.runSnapshot).mockRejectedValue(new ControlPlaneApiError(404, { code: 'not_found', message: 'Not found', action: 'Recheck' }));
  const { result } = renderHook(() => useQuickTest(true));
  await waitFor(() => expect(result.current.ended).toBe(true));
  expect(result.current.snapshot).toBeNull();
  expect(result.current.canStart).toBe(false);
  act(() => result.current.newTest());
  await waitFor(() => expect(result.current.running).toBe(false));
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});
it('rejects a snapshot from another workflow instead of silently adopting it', async () => {
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue({ ...bootstrap, busy: true, activeTask: prep });
  vi.mocked(controlPlaneClient.runSnapshot).mockResolvedValue({ ...prep, workflow: 'case-manager', workspaceName: 'quick-test', targetId: 'app' });
  const { result } = renderHook(() => useQuickTest(true));
  await waitFor(() => expect(result.current.error?.code).toBe('task_mismatch'));
  expect(result.current.snapshot).toBeNull();
  expect(result.current.canStart).toBe(false);
});
it('cancels only on an explicit request', async () => {
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue({ ...bootstrap, busy: true, activeTask: prep });
  const { result } = renderHook(() => useQuickTest(true));
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());
  expect(controlPlaneClient.cancelRun).not.toHaveBeenCalled();
  await act(async () => { await result.current.cancel(); });
  expect(controlPlaneClient.cancelRun).toHaveBeenCalledExactlyOnceWith('q1');
});
it('locks duplicate starts and context edits during a deferred POST without queuing cancellation', async () => {
  const post = deferred<{ requestId: string }>();
  vi.mocked(controlPlaneClient.startQuickTest).mockReturnValue(post.promise);
  const { result } = renderHook(() => useQuickTest(true));
  act(() => { result.current.setPlatform('macos'); result.current.setGoal('Test Notes'); });
  await waitFor(() => expect(result.current.canStart).toBe(true));
  act(() => { void result.current.start(); void result.current.start(); });
  expect(result.current.starting).toBe(true);
  expect(result.current.snapshot).toBeNull();
  act(() => { result.current.setPlatform('web'); result.current.setTargetId('one'); result.current.newTest(); });
  await act(async () => result.current.cancel());
  expect(result.current.platform).toBe('macos');
  expect(result.current.targetId).toBe('');
  expect(result.current.canStart).toBe(false);
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
  expect(controlPlaneClient.cancelRun).not.toHaveBeenCalled();
  expect(controlPlaneClient.runSnapshot).not.toHaveBeenCalled();
  await act(async () => post.resolve({ requestId: 'q1' }));
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());
  expect(controlPlaneClient.cancelRun).not.toHaveBeenCalled();
  await act(async () => result.current.cancel());
  expect(controlPlaneClient.cancelRun).toHaveBeenCalledExactlyOnceWith('q1');
});
it.each(['', ' ', 'x\u0000y', 'x'.repeat(16001)])('blocks invalid basic input without invoking inference', async goal => {
  const { result } = renderHook(() => useQuickTest(true));
  act(() => { result.current.setPlatform('macos'); result.current.setGoal(goal); });
  await waitFor(() => expect(result.current.context).not.toBeNull());
  expect(result.current.canStart).toBe(false);
  await act(async () => { await result.current.start(); });
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});
