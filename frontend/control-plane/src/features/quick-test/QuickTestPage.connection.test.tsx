import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ControlPlaneApiError, controlPlaneClient } from '../../api/controlPlaneClient';
import type { RunSnapshot } from '../../api/types';
import { quickDiagnostics } from '../../test/quickTestFixtures';
import * as runStream from '../devices/hooks/useRunStream';
import { QuickTestPage } from './QuickTestPage';

const reconnecting = 'Live updates were interrupted. Reconnecting…';
const polling = 'Live updates are unavailable. Checking test status periodically.';
const goal = 'Check live content after playback ended';
const snapshot: RunSnapshot = {
  requestId: 'q1', workflow: 'quick-test', application: 'Notes', platformRevision: null,
  workspaceName: 'quick-test', targetId: null, runId: 'run-1', platform: 'macos', mode: 'explore',
  status: 'running', source: { goal }, startedAt: '', completedAt: null, cancelRequested: false,
  events: [{ sequence: 1, label: 'live', message: 'ended' }], activeStep: null, result: null,
  summary: 'Checking the application', screenshotRevision: 0, uiSnapshotRevision: 0,
  evidenceAvailable: false, reportAvailable: false, terminal: false, error: null,
};
const bootstrap = {
  apiVersion: '1', platforms: [{ id: 'macos' as const, label: 'macOS' }],
  busy: true, activeTask: snapshot,
};
const props = { active: true, onCaseManager: vi.fn(), onOpenReport: vi.fn() };

beforeEach(() => {
  vi.spyOn(controlPlaneClient, 'bootstrap').mockResolvedValue(bootstrap);
  vi.spyOn(controlPlaneClient, 'runSnapshot').mockResolvedValue(snapshot);
  vi.spyOn(controlPlaneClient, 'startQuickTest').mockResolvedValue({ requestId: 'q1' });
  vi.spyOn(controlPlaneClient, 'cancelRun').mockResolvedValue({ ...snapshot, cancelRequested: true });
  vi.spyOn(controlPlaneClient, 'replayVideo').mockResolvedValue({ available: false, videoUrl: null });
  vi.spyOn(controlPlaneClient, 'replayFrames').mockResolvedValue({ available: false, frames: [], message: 'No frames' });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  TestEventSource.instances = [];
});

function noTransportLabels() {
  // Inspect only connection/status surfaces: identical words in user content must survive.
  expect(screen.getByLabelText('Run context')).not.toHaveTextContent(/\b(idle|connecting|live|ended|polling|reconnecting)\b/);
  for (const status of screen.queryAllByRole('status')) {
    expect(status.textContent).not.toMatch(/^(idle|connecting|live|ended|polling|reconnecting)$/);
  }
}

it.each(['idle', 'connecting', 'live', 'ended'] as const)('does not render or announce normal %s, without filtering Goal, events or Logs', async connection => {
  vi.spyOn(runStream, 'useRunStream').mockReturnValue({
    snapshot: { ...snapshot, ...(connection === 'ended' ? { status: 'success', terminal: true } as const : {}) },
    connection, error: null,
  });
  render(<QuickTestPage {...props} />);
  await screen.findByLabelText('Run context');
  noTransportLabels();
  expect(screen.queryByText(reconnecting)).not.toBeInTheDocument();
  expect(screen.queryByText(polling)).not.toBeInTheDocument();
  expect(screen.getByText(goal)).toBeVisible();
  expect(screen.getByText('live', { exact: true })).toBeVisible();
  expect(screen.getByText('ended', { exact: true })).toBeVisible();
  fireEvent.click(screen.getByRole('tab', { name: 'Logs' }));
  expect(screen.getByRole('tabpanel')).toHaveTextContent('live');
  expect(screen.getByRole('tabpanel')).toHaveTextContent('ended');
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
  expect(controlPlaneClient.cancelRun).not.toHaveBeenCalled();
});

it.each(['reconnecting', 'polling'] as const)('shows readable %s status and silently removes it on recovery', async connection => {
  const stream = vi.spyOn(runStream, 'useRunStream').mockReturnValue({ snapshot, connection, error: null });
  const { rerender } = render(<QuickTestPage {...props} />);
  const notice = await screen.findByText(connection === 'reconnecting' ? reconnecting : polling);
  expect(notice.closest('.page-notice')).toHaveAttribute('role', 'status');
  expect(screen.getAllByText(connection === 'reconnecting' ? reconnecting : polling)).toHaveLength(1);
  expect(screen.getByLabelText('Run context').querySelector('.status-badge')).toHaveTextContent(/^Running$/);
  stream.mockReturnValue({ snapshot, connection: 'live', error: null });
  rerender(<QuickTestPage {...props} />);
  noTransportLabels();
  expect(screen.queryByText(reconnecting)).not.toBeInTheDocument();
  expect(screen.queryByText(polling)).not.toBeInTheDocument();
  expect(screen.queryByText(/recovered|connected successfully/i)).not.toBeInTheDocument();
  expect(controlPlaneClient.runSnapshot).not.toHaveBeenCalled();
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it.each(['failed', 'cancelled', 'success'] as const)('retains authoritative %s completion, report and source but no ended or stale notice', async status => {
  const terminal: RunSnapshot = { ...snapshot, status, terminal: true, result: { status } };
  const stream = vi.spyOn(runStream, 'useRunStream').mockReturnValue({ snapshot: terminal, connection: 'ended', error: null });
  const { rerender } = render(<QuickTestPage {...props} />);
  await screen.findByLabelText('Run context');
  for (const connection of ['ended', 'reconnecting', 'polling'] as const) {
    stream.mockReturnValue({ snapshot: terminal, connection, error: null });
    rerender(<QuickTestPage {...props} />);
    noTransportLabels();
    expect(screen.queryByText(reconnecting)).not.toBeInTheDocument();
    expect(screen.queryByText(polling)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Run context').querySelector('.status-badge')).toHaveTextContent(status);
    expect(screen.getByText(goal)).toBeVisible();
    expect(screen.getByRole('button', { name: 'New test' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Open report' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Cancel run' })).not.toBeInTheDocument();
  }
});

it.each(['invalid_stream', 'run_ended'] as const)('retains %s error and guidance even with ended transport and a terminal task', async code => {
  const error = { code, message: 'A real stream failure remains visible.', action: 'Check the server task.' };
  vi.spyOn(runStream, 'useRunStream').mockReturnValue({
    snapshot: { ...snapshot, terminal: true, status: 'failed' }, connection: 'ended', error,
  });
  render(<QuickTestPage {...props} />);
  await screen.findByLabelText('Run context');
  expect(screen.getByRole('alert')).toHaveAttribute('data-error-code', code);
  expect(screen.getByRole('alert')).toHaveTextContent(error.message);
  expect(screen.getByRole('alert')).toHaveTextContent(error.action);
  noTransportLabels();
});

it('retains task mismatch protection when transport has ended', async () => {
  vi.spyOn(runStream, 'useRunStream').mockReturnValue({
    snapshot: { ...snapshot, workflow: 'case-manager' }, connection: 'ended', error: null,
  });
  render(<QuickTestPage {...props} />);
  expect(await screen.findByRole('alert')).toHaveAttribute('data-error-code', 'task_mismatch');
  noTransportLabels();
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

class TestEventSource {
  static instances: TestEventSource[] = [];
  listeners = new Map<string, EventListener>();
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(readonly url: string) { TestEventSource.instances.push(this); }
  addEventListener(type: string, listener: EventListener) { this.listeners.set(type, listener); }
  open() { this.listeners.get('open')?.(new Event('open')); }
}

it('keeps existing reconnect delays, fallback polling interval, cancel and single-start behavior', async () => {
  vi.stubGlobal('EventSource', TestEventSource);
  render(<QuickTestPage {...props} />);
  await screen.findByText(snapshot.summary);
  expect(TestEventSource.instances).toHaveLength(1);
  noTransportLabels(); // initial connection
  act(() => TestEventSource.instances[0].open());
  noTransportLabels();
  vi.useFakeTimers();
  for (const delay of [400, 800, 1600]) {
    act(() => TestEventSource.instances.at(-1)?.onerror?.());
    expect(screen.getByText(reconnecting)).toHaveAttribute('role', 'status');
    const count = TestEventSource.instances.length;
    await act(async () => vi.advanceTimersByTimeAsync(delay - 1));
    expect(TestEventSource.instances).toHaveLength(count);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(TestEventSource.instances).toHaveLength(count + 1);
  }
  await act(async () => TestEventSource.instances.at(-1)?.onerror?.());
  expect(screen.getByText(polling)).toHaveAttribute('role', 'status');
  expect(controlPlaneClient.runSnapshot).toHaveBeenCalledTimes(2);
  await act(async () => vi.advanceTimersByTimeAsync(1999));
  expect(controlPlaneClient.runSnapshot).toHaveBeenCalledTimes(2);
  await act(async () => vi.advanceTimersByTimeAsync(1));
  expect(controlPlaneClient.runSnapshot).toHaveBeenCalledTimes(3);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
  expect(controlPlaneClient.cancelRun).toHaveBeenCalledExactlyOnceWith('q1');
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it('explains periodic checks without EventSource and retains server-restart guidance when polling ends', async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'EventSource')!;
  Reflect.deleteProperty(globalThis, 'EventSource');
  try {
    vi.useFakeTimers();
    await act(async () => { render(<QuickTestPage {...props} />); });
    const notice = screen.getByText(polling);
    expect(notice).toHaveAttribute('role', 'status');
    expect(controlPlaneClient.runSnapshot).toHaveBeenCalledTimes(2);
    vi.mocked(controlPlaneClient.runSnapshot).mockRejectedValue(new ControlPlaneApiError(404, {
      code: 'request_not_found', message: 'Missing', action: 'Reload',
    }));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(screen.queryByText(polling)).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveAttribute('data-error-code', 'run_ended');
    expect(screen.getByRole('alert')).toHaveTextContent('server may have restarted');
    noTransportLabels();
    await act(async () => vi.advanceTimersByTimeAsync(6000));
    expect(controlPlaneClient.runSnapshot).toHaveBeenCalledTimes(3);
    expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
  } finally {
    Object.defineProperty(globalThis, 'EventSource', original);
  }
});

it('keeps an unknown start response visible, locked and never automatically resubmitted', async () => {
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue({ ...bootstrap, busy: false, activeTask: null });
  vi.spyOn(controlPlaneClient, 'quickTestContext').mockResolvedValue({
    platform: 'macos', targetId: null, canStart: true, choices: [], checks: [], diagnostics: quickDiagnostics('macos', null),
  });
  vi.mocked(controlPlaneClient.startQuickTest).mockRejectedValue(new Error('Unknown response'));
  render(<QuickTestPage {...props} />);
  await screen.findByRole('option', { name: 'macOS' });
  fireEvent.change(screen.getByLabelText('Platform'), { target: { value: 'macos' } });
  fireEvent.change(screen.getByLabelText('What do you want to test?'), { target: { value: goal } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  await screen.findByText(/No request will be retried automatically/);
  expect(screen.getByRole('alert')).toHaveTextContent('Unknown response');
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeDisabled();
  expect(screen.getByLabelText('What do you want to test?')).toHaveValue(goal);
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
  expect(controlPlaneClient.bootstrap).toHaveBeenCalledTimes(2);
  expect(within(screen.getByRole('alert')).queryByRole('button')).not.toBeInTheDocument();
});
