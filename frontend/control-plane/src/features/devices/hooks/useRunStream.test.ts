import { act, renderHook, waitFor } from '@testing-library/react';
import { ControlPlaneApiError, controlPlaneClient } from '../../../api/controlPlaneClient';
import type { RunSnapshot } from '../../../api/types';
import { useRunStream } from './useRunStream';

afterEach(() => {
  vi.restoreAllMocks();
  TestEventSource.instances = [];
});

const snapshot = (overrides: Partial<RunSnapshot> = {}): RunSnapshot => ({
  workflow: 'case-manager', application: null, platformRevision: null,
  requestId: 'request-1', runId: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'running',
  source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false,
  events: [], activeStep: null, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
  evidenceAvailable: false, reportAvailable: false, terminal: false, ...overrides,
});

class TestEventSource {
  static instances: TestEventSource[] = [];
  readonly listeners = new Map<string, (event: Event) => void>();
  onerror: ((event: Event) => void) | null = null;
  constructor(readonly url: string) { TestEventSource.instances.push(this); }
  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (event: Event) => void);
  }
  close() {}
  emit(type: string, value: RunSnapshot) {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(value) }));
  }
}

it('streams Quick preparation, allocated execution and terminal error without changing workflow identity', async () => {
    const original = window.EventSource;
    Object.defineProperty(window, 'EventSource', { configurable: true, value: TestEventSource });
    Object.defineProperty(globalThis, 'EventSource', { configurable: true, value: TestEventSource });
    const preparing = snapshot({ workflow: 'quick-test', application: null, platformRevision: null, platform: 'macos', targetId: null, workspaceName: null, runId: null, status: 'preparing', error: null });
    const running = { ...preparing, application: 'Notes', workspaceName: 'quick-test', runId: 'run-1', status: 'running' as const };
    const terminal = { ...running, status: 'error' as const, terminal: true, error: { code: 'execution_error', message: 'The action failed', action: 'Inspect evidence.' } };
    const load = vi.spyOn(controlPlaneClient, 'runSnapshot').mockResolvedValue(preparing);
    vi.spyOn(controlPlaneClient, 'streamUrl').mockReturnValue('/stream');
    const { result, unmount } = renderHook(() => useRunStream('request-1'));
    try {
      await waitFor(() => expect(result.current.snapshot?.workspaceName).toBeNull());
      act(() => TestEventSource.instances.at(-1)?.emit('snapshot', running));
      expect(result.current.snapshot?.runId).toBe('run-1');
      load.mockResolvedValue(terminal);
      act(() => TestEventSource.instances.at(-1)?.emit('snapshot', terminal));
      await waitFor(() => expect(result.current.connection).toBe('ended'));
      expect(result.current.snapshot?.workflow).toBe('quick-test');
      expect(result.current.snapshot?.error?.code).toBe('execution_error');
      expect(result.current.snapshot?.runId).toBe('run-1');
    } finally {
      unmount();
      Object.defineProperty(window, 'EventSource', { configurable: true, value: original });
      Object.defineProperty(globalThis, 'EventSource', { configurable: true, value: original });
    }
});

it('hydrates terminal stream snapshots so existing events receive step ids', async () => {
  const original = window.EventSource;
  Object.defineProperty(window, 'EventSource', { configurable: true, value: TestEventSource });
  Object.defineProperty(globalThis, 'EventSource', { configurable: true, value: TestEventSource });
  const runSnapshot = vi.spyOn(controlPlaneClient, 'runSnapshot')
    .mockResolvedValueOnce(snapshot({ events: [{ sequence: 4, label: 'Last event' }] }))
    .mockResolvedValueOnce(snapshot({
      status: 'success', terminal: true, completedAt: '2026-08-11T00:00:00Z', summary: 'Done',
      events: [{ sequence: 4, label: 'Last event', stepId: 'step-4' }],
    }));
  vi.spyOn(controlPlaneClient, 'streamUrl').mockReturnValue('/stream');
  const { result, unmount } = renderHook(() => useRunStream('request-1'));
  await waitFor(() => expect(result.current.snapshot?.events).toHaveLength(1));

  act(() => TestEventSource.instances.at(-1)?.emit('snapshot', snapshot({
    status: 'success', terminal: true, completedAt: '2026-08-11T00:00:00Z', summary: 'Done', events: [],
  })));

  expect(result.current.snapshot?.status).toBe('success');
  expect(result.current.snapshot?.summary).toBe('Done');
  expect(result.current.snapshot?.events).toHaveLength(1);
  expect(result.current.connection).toBe('ended');
  await waitFor(() => expect(result.current.snapshot?.events[0].stepId).toBe('step-4'));
  expect(runSnapshot).toHaveBeenCalledTimes(2);
  unmount();
  Object.defineProperty(window, 'EventSource', { configurable: true, value: original });
  Object.defineProperty(globalThis, 'EventSource', { configurable: true, value: original });
});

it('ends recoverably when polling finds a restarted backend', async () => {
  const original = window.EventSource;
  Reflect.deleteProperty(window, 'EventSource');
  Reflect.deleteProperty(globalThis, 'EventSource');
  vi.spyOn(controlPlaneClient, 'runSnapshot').mockRejectedValue(new ControlPlaneApiError(404, {
    code: 'request_not_found', message: 'missing', action: 'reload',
  }));

  const { result, unmount } = renderHook(() => useRunStream('request-1'));
  await waitFor(() => expect(result.current.connection).toBe('ended'));
  expect(result.current.error?.code).toBe('run_ended');
  expect(result.current.error?.action).toMatch(/new run|reattach/i);
  unmount();
  Object.defineProperty(window, 'EventSource', { configurable: true, value: original });
  Object.defineProperty(globalThis, 'EventSource', { configurable: true, value: original });
});

it('falls back to polling after bounded EventSource reconnect exhaustion', async () => {
  vi.useFakeTimers();
  const original = window.EventSource;
  Object.defineProperty(window, 'EventSource', { configurable: true, value: TestEventSource });
  Object.defineProperty(globalThis, 'EventSource', { configurable: true, value: TestEventSource });
  const client = {
    ...controlPlaneClient,
    runSnapshot: vi.fn().mockResolvedValue(snapshot()),
    streamUrl: vi.fn().mockReturnValue('/stream'),
  };
  const { result, unmount } = renderHook(() => useRunStream('request-1', client));
  await act(async () => Promise.resolve());
  for (const delay of [400, 800, 1600]) {
    act(() => TestEventSource.instances.at(-1)?.onerror?.(new Event('error')));
    await act(async () => { await vi.advanceTimersByTimeAsync(delay); });
  }
  act(() => TestEventSource.instances.at(-1)?.onerror?.(new Event('error')));
  await act(async () => Promise.resolve());
  expect(result.current.connection).toBe('polling');
  expect(client.runSnapshot).toHaveBeenCalled();
  unmount();
  vi.useRealTimers();
  Object.defineProperty(window, 'EventSource', { configurable: true, value: original });
  Object.defineProperty(globalThis, 'EventSource', { configurable: true, value: original });
});
