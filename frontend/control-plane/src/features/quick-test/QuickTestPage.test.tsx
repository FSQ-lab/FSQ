import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { quickDiagnostics } from '../../test/quickTestFixtures';
import { controlPlaneClient } from '../../api/controlPlaneClient';
import type { RunSnapshot } from '../../api/types';
import { QuickTestPage } from './QuickTestPage';

const platforms = [{ id: 'android', label: 'Android' }, { id: 'web', label: 'Web' }, { id: 'windows', label: 'Windows' }, { id: 'macos', label: 'macOS' }] as const;
const bootstrap = { apiVersion: '1', platforms: [...platforms], busy: false, activeTask: null };
const prep = {
  requestId: 'q1', workflow: 'quick-test' as const, application: null, platformRevision: null,
  workspaceName: null, targetId: null, runId: null, platform: 'macos' as const, mode: 'explore' as const, status: 'preparing' as const,
  source: { goal: 'Open Notes and verify a new note' }, startedAt: '', completedAt: null, cancelRequested: false,
  events: [], activeStep: null, result: null, summary: 'Identifying application', screenshotRevision: 0,
  uiSnapshotRevision: 0, evidenceAvailable: false, reportAvailable: false, terminal: false, error: null,
};
beforeEach(() => {
  vi.spyOn(controlPlaneClient, 'bootstrap').mockResolvedValue(bootstrap);
  vi.spyOn(controlPlaneClient, 'quickTestContext').mockImplementation(async (platform, targetId) => ({
    platform, targetId: targetId ?? null, canStart: true,
    choices: platform === 'web' ? [{ id: 'chrome', label: 'Chrome', selectable: true, status: 'ready' }] : [],
    checks: [{ id: 'application', status: 'pending', message: 'Awaiting identification', action: '' }],
    diagnostics: quickDiagnostics(platform, targetId ?? null),
  }));
  vi.spyOn(controlPlaneClient, 'startQuickTest').mockResolvedValue({ requestId: 'q1' });
  vi.spyOn(controlPlaneClient, 'runSnapshot').mockResolvedValue(prep);
});
afterEach(() => vi.restoreAllMocks());
const props = { active: true, onCaseManager: vi.fn(), onOpenReport: vi.fn() };
it.each(['android', 'macos'] as const)('F001: directly reuses %s pending diagnostics, copy, passed disclosure, guide and recheck', async platform => {
  const diagnostics = quickDiagnostics(platform, platform === 'android' ? 'serial' : null);
  const command = platform === 'android' ? 'adb start-server' : 'appium driver install mac2';
  diagnostics.prerequisites.push({ identifier: platform === 'android' ? 'adb_server' : 'appium_mac2_driver', status: 'unavailable', code: 'runtime.missing', message: 'Independent runtime check failed.', action: 'Install manually.', commands: [command] });
  diagnostics.commands.prepare = { status: 'unavailable', code: 'runtime.missing', message: 'Independent runtime check failed.', action: 'Install manually.' };
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
  vi.mocked(controlPlaneClient.quickTestContext).mockImplementation(async (_platform, targetId) => ({
    platform, targetId: targetId ?? null, canStart: false, checks: [],
    choices: platform === 'android' ? [{ id: 'serial', label: 'Phone', status: 'ready', selectable: true }] : [],
    diagnostics: { ...diagnostics, ...(platform === 'android' ? { targetId: targetId ?? null } : {}) },
  }));
  render(<QuickTestPage {...props} />);
  await screen.findByRole('option', { name: platform === 'android' ? 'Android' : 'macOS' });
  fireEvent.change(screen.getByLabelText('Platform'), { target: { value: platform } });
  const environment = await screen.findByRole('region', { name: platform === 'android' ? 'Android environment' : 'macOS environment' });
  expect(environment.closest('.composer-environment')).not.toBeNull();
  await within(environment).findByRole('button', { name: `Copy command: ${command}` });
  expect(screen.queryByRole('heading', { name: 'Environment checks' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Open Settings' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Edit target configuration' })).not.toBeInTheDocument();
  expect(within(environment).getByText('Not applicable')).toBeInTheDocument();
  expect(within(environment).getByText('Pending')).toBeInTheDocument();
  expect(within(environment).getByText(/needs attention/)).toBeInTheDocument();
  await user.click(within(environment).getByRole('button', { name: `Copy command: ${command}` }));
  expect(copy).toHaveBeenCalledWith(command);
  expect(within(environment).getByText('Copied!')).toBeVisible();
  await user.click(within(environment).getByText('Show passed checks (1)'));
  expect(within(environment).getByText(platform === 'android' ? 'ADB CLI' : 'Appium CLI')).toBeVisible();
  expect(within(environment).getByText(/Checked at/)).toBeInTheDocument();
  const guide = within(environment).getByText(/installation and troubleshooting/, { selector: 'summary' });
  await user.click(guide);
  expect(guide.parentElement).toHaveAttribute('open');
  const before = vi.mocked(controlPlaneClient.quickTestContext).mock.calls.length;
  await user.click(within(environment).getByRole('button', { name: 'Recheck environment' }));
  await waitFor(() => expect(controlPlaneClient.quickTestContext).toHaveBeenCalledTimes(before + 1));
});
it('F001: prepare-ready/caseCreate-pending stays neutral and permits identification', async () => {
  await compose();
  expect(screen.getByText('Awaiting application identification')).toBeInTheDocument();
  expect(screen.queryByText('macOS is ready')).not.toBeInTheDocument();
  expect(screen.queryByText('macOS needs attention')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeEnabled();
});
it('F001: null diagnostics remains unavailable even if legacy canStart is true', async () => {
  vi.mocked(controlPlaneClient.quickTestContext).mockResolvedValue({ platform: 'macos', targetId: null, canStart: true, choices: [], checks: [], diagnostics: null });
  render(<QuickTestPage {...props} />);
  await screen.findByRole('option', { name: 'macOS' });
  fireEvent.change(screen.getByLabelText('Platform'), { target: { value: 'macos' } });
  fireEvent.change(screen.getByLabelText('What do you want to test?'), { target: { value: 'Verify Notes' } });
  await screen.findByText('Environment check unavailable');
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Recheck environment' })).toBeEnabled();
});
async function compose() {
  render(<QuickTestPage {...props} />);
  await screen.findByRole('option', { name: 'macOS' });
  fireEvent.change(screen.getByLabelText('Platform'), { target: { value: 'macos' } });
  fireEvent.change(screen.getByLabelText('What do you want to test?'), { target: { value: prep.source.goal } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeEnabled());
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
it('immediately shows the shared workbench and submitted source while POST is pending, without evidence or cancellation requests', async () => {
  const post = deferred<{ requestId: string }>();
  vi.mocked(controlPlaneClient.startQuickTest).mockReturnValue(post.promise);
  const cancel = vi.spyOn(controlPlaneClient, 'cancelRun');
  const screenshot = vi.spyOn(controlPlaneClient, 'screen');
  const tree = vi.spyOn(controlPlaneClient, 'uiSnapshot');
  const replay = vi.spyOn(controlPlaneClient, 'replayVideo');
  const frames = vi.spyOn(controlPlaneClient, 'replayFrames');
  await compose();
  const start = screen.getByRole('button', { name: 'Start Quick Test' });
  fireEvent.click(start);
  fireEvent.click(start);
  expect(screen.queryByLabelText('What do you want to test?')).not.toBeInTheDocument();
  const activity = screen.getByRole('heading', { name: 'Activity' });
  expect(activity.closest('.devices-workbench')).not.toBeNull();
  expect(screen.getByRole('heading', { name: 'Live evidence' }).closest('.devices-workbench')).toBe(activity.closest('.devices-workbench'));
  expect(screen.getByLabelText('Run context').querySelector('.status-badge')).toHaveTextContent(/^Preparing$/);
  expect(screen.getByText(prep.source.goal).closest('.run-source-summary')).not.toBeNull();
  expect(screen.getByText('No run allocated')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Cancel run' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'UI Tree' }));
  fireEvent.click(screen.getByRole('tab', { name: 'Logs' }));
  fireEvent.click(screen.getByRole('tab', { name: 'Screen' }));
  expect(controlPlaneClient.runSnapshot).not.toHaveBeenCalled();
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
  for (const spy of [cancel, screenshot, tree, replay, frames]) expect(spy).not.toHaveBeenCalled();
  await act(async () => post.resolve({ requestId: 'q1' }));
  await screen.findByText('Identifying application');
  expect(screen.getByLabelText('Run context').querySelector('.status-badge')).toHaveTextContent(/^Preparing$/);
  expect(screen.getByRole('heading', { name: 'Live evidence' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Cancel run' })).toBeEnabled();
  for (const spy of [cancel, screenshot, tree, replay, frames]) expect(spy).not.toHaveBeenCalled();
});
it.each(['running', 'cancelled', 'error'] as const)('QT-IW-F001: keeps Preparing after POST acceptance until the first authoritative %s snapshot', async status => {
  const post = deferred<{ requestId: string }>();
  const firstStatus = deferred<RunSnapshot>();
  vi.mocked(controlPlaneClient.startQuickTest).mockReturnValue(post.promise);
  // Hold every initial snapshot request; the shared test EventSource emits no updates.
  vi.mocked(controlPlaneClient.runSnapshot).mockReturnValue(firstStatus.promise);
  const cancel = vi.spyOn(controlPlaneClient, 'cancelRun');
  const screenshot = vi.spyOn(controlPlaneClient, 'screen');
  const tree = vi.spyOn(controlPlaneClient, 'uiSnapshot');
  const replay = vi.spyOn(controlPlaneClient, 'replayVideo').mockResolvedValue({ available: false, videoUrl: null });
  const frames = vi.spyOn(controlPlaneClient, 'replayFrames').mockResolvedValue({ available: false, frames: [], message: 'No frames' });
  await compose();
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  const workbench = screen.getByRole('heading', { name: 'Activity' }).closest('.devices-workbench');
  const badge = screen.getByLabelText('Run context').querySelector('.status-badge');
  expect(badge).toHaveTextContent(/^Preparing$/);
  expect(controlPlaneClient.runSnapshot).not.toHaveBeenCalled();

  await act(async () => post.resolve({ requestId: 'q1' }));
  expect(controlPlaneClient.runSnapshot).toHaveBeenCalledWith('q1', expect.any(AbortSignal));
  expect(screen.getByRole('heading', { name: 'Activity' }).closest('.devices-workbench')).toBe(workbench);
  expect(screen.getByRole('heading', { name: 'Live evidence' }).closest('.devices-workbench')).toBe(workbench);
  expect(screen.getByLabelText('Run context').querySelector('.status-badge')).toBe(badge);
  expect(screen.getByLabelText('Run context').querySelectorAll('.status-badge')).toHaveLength(1);
  expect(badge).toHaveTextContent(/^Preparing$/);
  expect(screen.getAllByText('Preparing', { exact: true })).toHaveLength(1);
  expect(screen.getByText(prep.source.goal).closest('.run-source-summary')).not.toBeNull();
  expect(screen.getByText('No run allocated')).toBeVisible();
  expect(screen.getByText('Screen not yet captured')).toBeVisible();
  expect(screen.queryByText('Identifying application')).not.toBeInTheDocument();
  expect(screen.queryByText('Running', { exact: true })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('What do you want to test?')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Cancel run' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Open report' })).not.toBeInTheDocument();
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
  for (const spy of [cancel, screenshot, tree, replay, frames]) expect(spy).not.toHaveBeenCalled();

  await act(async () => firstStatus.resolve({
    ...prep, status, terminal: status !== 'running',
    runId: status === 'cancelled' ? 'real-terminal-run' : null,
    workspaceName: status === 'cancelled' ? 'quick-test' : null,
    error: status === 'error' ? { code: 'quick_test.inference_failed', message: 'Direct preparation error', action: 'Correct the prompt.' } : null,
  }));
  if (status === 'error') {
    expect(screen.getByRole('alert')).toHaveTextContent('Direct preparation error');
    expect(screen.getByLabelText('What do you want to test?')).toHaveValue(prep.source.goal);
    expect(screen.queryByLabelText('Run context')).not.toBeInTheDocument();
  } else {
    expect(screen.getByLabelText('Run context').querySelector('.status-badge')).toBe(badge);
    expect(badge).toHaveTextContent(status === 'running' ? /^Running$/ : /^cancelled$/);
  }
  expect(screen.queryByText('Preparing', { exact: true })).not.toBeInTheDocument();
  expect(cancel).not.toHaveBeenCalled();
});
it.each([
  ['preparing', 'allocated', 'Preparing'],
  ['running', null, 'Running'],
  ['cancelled', 'allocated', 'cancelled'],
] as const)('uses server %s status with runId %s rather than allocation or connection', async (status, runId, label) => {
  vi.spyOn(controlPlaneClient, 'replayVideo').mockResolvedValue({ available: false, videoUrl: null });
  vi.spyOn(controlPlaneClient, 'replayFrames').mockResolvedValue({ available: false, frames: [], message: 'No frames' });
  vi.mocked(controlPlaneClient.runSnapshot).mockResolvedValue({ ...prep, status, runId, workspaceName: runId ? 'quick-test' : null, terminal: status === 'cancelled' });
  await compose();
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  await waitFor(() => expect(screen.getByLabelText('Run context').querySelector('.status-badge')).toHaveTextContent(label));
  expect(screen.queryByLabelText('What do you want to test?')).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Live evidence' }).closest('.devices-workbench')).not.toBeNull();
});
it('never shows the previous run source, application or status during a new pending start', async () => {
  vi.spyOn(controlPlaneClient, 'replayVideo').mockResolvedValue({ available: false, videoUrl: null });
  vi.spyOn(controlPlaneClient, 'replayFrames').mockResolvedValue({ available: false, frames: [], message: 'No frames' });
  vi.mocked(controlPlaneClient.runSnapshot).mockResolvedValue({ ...prep, status: 'success', terminal: true, runId: 'old-run', workspaceName: 'quick-test', application: 'Old application', source: { goal: 'Old source' } });
  await compose();
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  fireEvent.click(await screen.findByRole('button', { name: 'New test' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('What do you want to test?'), { target: { value: 'New submitted goal' } });
  const post = deferred<{ requestId: string }>();
  vi.mocked(controlPlaneClient.startQuickTest).mockReturnValue(post.promise);
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  expect(screen.getByText('New submitted goal').closest('.run-source-summary')).not.toBeNull();
  expect(screen.getByLabelText('Run context').querySelector('.status-badge')).toHaveTextContent(/^Preparing$/);
  for (const text of ['Old source', 'Old application', 'Run old-run']) expect(screen.queryByText(text)).not.toBeInTheDocument();
});
it('disabled invalid Start keeps the composer and sends nothing', async () => {
  await compose();
  fireEvent.change(screen.getByLabelText('What do you want to test?'), { target: { value: ' ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  expect(screen.getByLabelText('What do you want to test?')).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Activity' })).not.toBeInTheDocument();
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});
it.each([400, 503])('F007: malformed %s start errors remain renderable and conservatively locked', async status => {
  vi.mocked(controlPlaneClient.startQuickTest).mockRestore();
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ code: 'failure', message: { bad: 'shape' }, action: {} }), { status }));
  await compose();
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  await screen.findByText('The Control Plane server returned an invalid error response.');
  expect(screen.getByLabelText('What do you want to test?')).toHaveValue(prep.source.goal);
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeDisabled();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('shows four platforms without workspace or strict controls; pending application can start exactly once', async () => {
  await compose();
  for (const p of platforms) expect(screen.getByRole('option', { name: p.label })).toBeInTheDocument();
  expect(screen.queryByText('Strict Replay')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Workspace')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Browser')).not.toBeInTheDocument();
  const start = screen.getByRole('button', { name: 'Start Quick Test' });
  fireEvent.click(start);
  fireEvent.click(start);
  await screen.findByText('Identifying application');
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledExactlyOnceWith({ platform: 'macos', goal: prep.source.goal });
});
it.each(['insufficient', 'application_missing', 'application_not_found', 'application_ambiguous', 'invalid_web_address', 'discovery_failed', 'inference_failed', 'prerequisites', 'workspace_conflict', 'preparation_failed'])('preserves input on %s without clarification or retry', async (code) => {
  vi.mocked(controlPlaneClient.runSnapshot).mockResolvedValue({ ...prep, status: 'error', terminal: true, error: { code: `quick_test.${code}`, message: `Failure: ${code}`, action: 'Correct the prompt and try explicitly.' } });
  await compose();
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  await screen.findByText(`Failure: ${code}`);
  expect(screen.getByLabelText('What do you want to test?')).toHaveValue(prep.source.goal);
  expect(screen.getByLabelText('Platform')).toHaveValue('macos');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
});
it('restores Quick preparation without POST', async () => {
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue({ ...bootstrap, busy: true, activeTask: prep });
  render(<QuickTestPage {...props} />);
  await screen.findByText('Identifying application');
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});
it('does not adopt a Case Manager task in the quick-test workspace', async () => {
  vi.mocked(controlPlaneClient.bootstrap).mockResolvedValue({ ...bootstrap, busy: true, activeTask: { ...prep, workflow: 'case-manager', workspaceName: 'quick-test', targetId: 'app' } });
  render(<QuickTestPage {...props} />);
  await screen.findByRole('button', { name: 'Open FSQ Case Manager' });
  expect(controlPlaneClient.runSnapshot).not.toHaveBeenCalled();
  expect(controlPlaneClient.startQuickTest).not.toHaveBeenCalled();
});

it('uses shared terminal evidence and stable report identity; New test rechecks without rerunning', async () => {
  vi.spyOn(controlPlaneClient, 'replayVideo').mockResolvedValue({ available: false, videoUrl: null });
  vi.spyOn(controlPlaneClient, 'replayFrames').mockResolvedValue({ available: false, frames: [], message: 'No frames' });
  vi.mocked(controlPlaneClient.runSnapshot).mockResolvedValue({ ...prep, status: 'failed', terminal: true, runId: 'run-stable', workspaceName: 'quick-test', application: 'Notes', summary: 'Assertion failed' });
  await compose();
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  await screen.findByRole('button', { name: 'New test' });
  expect(screen.queryByRole('button', { name: 'Save yaml' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText('What do you want to test?')).not.toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Screen' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Open report' }));
  expect(props.onOpenReport).toHaveBeenCalledWith('quick-test', 'macos', 'run-stable');
  fireEvent.click(screen.getByRole('button', { name: 'New test' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeEnabled());
  expect(screen.getByLabelText('What do you want to test?')).toHaveValue(prep.source.goal);
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
});

it.each([
  'Open https://example.com and verify the title',
  '搜索 FSQ github，并且帮我找到FSQ 的官网地址',
])('accepts the Web goal "%s" without extra modes and requires browser selection', async goal => {
  vi.mocked(controlPlaneClient.runSnapshot).mockResolvedValue({
    ...prep, platform: 'web', targetId: 'chrome', source: { goal },
  });
  render(<QuickTestPage {...props} />);
  await screen.findByRole('option', { name: 'Web' });
  fireEvent.change(screen.getByLabelText('Platform'), { target: { value: 'web' } });
  await screen.findByRole('option', { name: 'Chrome' });
  fireEvent.change(screen.getByLabelText('What do you want to test?'), { target: { value: goal } });
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeDisabled();
  expect(screen.getByText(/provide a URL or describe a clear search goal/)).toBeInTheDocument();
  expect(screen.queryByText(/Include an explicit test URL/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Search engine')).not.toBeInTheDocument();
  expect(screen.queryByText('Strict Replay')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Browser'), { target: { value: 'chrome' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeEnabled());
  expect(screen.getByLabelText('Browser')).toHaveValue('chrome');
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  await waitFor(() => expect(controlPlaneClient.startQuickTest).toHaveBeenCalledWith({ platform: 'web', targetId: 'chrome', goal }));
  await screen.findByRole('heading', { name: 'Activity' });
});
