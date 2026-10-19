import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { EntryApp } from './EntryApp';
import { controlPlaneClient } from '../api/controlPlaneClient';
import { quickDiagnostics } from '../test/quickTestFixtures';
import type { BootstrapResponse, RunSnapshot, WorkspaceRegistryEntry } from '../api/types';

// Real EntryApp + Manager + Overview/navigation; only the Provider editor is a guard fixture.
vi.mock('../features/config/ConfigPage', () => ({
  ConfigPage: ({ onDirtyChange, onSavePendingChange, onSaveUncertainChange }: {
    onDirtyChange: (value: boolean) => void;
    onSavePendingChange: (value: boolean) => void;
    onSaveUncertainChange: (value: boolean) => void;
  }) => <section aria-label="Provider editor">
    <button onClick={() => onDirtyChange(true)}>Dirty Provider</button>
    <button onClick={() => onSavePendingChange(true)}>Pending Provider</button>
    <button onClick={() => { onSavePendingChange(false); onSaveUncertainChange(true); }}>Unknown Provider</button>
  </section>,
}));
const workspace: WorkspaceRegistryEntry = { name: 'existing', rootPath: '/isolated/existing', status: 'available', message: 'Available', platforms: [{ platform: 'web', status: 'available', message: 'Available', configPath: '/isolated/existing/config.web.yaml' }] };
beforeEach(() => {
  window.history.replaceState(null, '', '/');
  vi.spyOn(controlPlaneClient, 'workspaces').mockResolvedValue({ workspaces: [workspace] });
  vi.spyOn(controlPlaneClient, 'config').mockResolvedValue({ configured: false, provider: null });
  vi.spyOn(controlPlaneClient, 'configStatus').mockResolvedValue({ configured: false, provider: null, modelName: null, sessionStatus: 'not_configured', authenticated: false, message: 'Not configured' });
  vi.spyOn(controlPlaneClient, 'bootstrap').mockResolvedValue({ apiVersion: '1', platforms: [], busy: false, activeTask: null });
});
afterEach(() => vi.restoreAllMocks());
async function travel(direction: 'back' | 'forward' | number, hash: string) {
  await act(async () => {
    const event = new Promise<void>(resolve => window.addEventListener('popstate', () => resolve(), { once: true }));
    if (typeof direction === 'number') window.history.go(direction);
    else window.history[direction]();
    await event;
  });
  await waitFor(() => expect(window.location.hash).toBe(hash));
}
async function enterManager() {
  fireEvent.click(screen.getByRole('button', { name: 'FSQ Case Manager' }));
  await screen.findByRole('heading', { name: 'Choose where to test' });
  await waitFor(() => expect(controlPlaneClient.workspaces).toHaveBeenCalled());
}
it('F002: Back/Forward distinguishes original chooser from Manager internal hashless pages', async () => {
  render(<EntryApp />);
  await enterManager();
  await travel('back', '');
  expect(screen.getByRole('button', { name: 'FSQ Case Manager' })).toBeVisible();
  await travel('forward', '#case-manager');
  expect(screen.getByRole('heading', { name: 'Choose where to test' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(window.location.hash).toBe('');
  expect(screen.getByRole('region', { name: 'Provider editor' })).toBeVisible();
  await travel('back', '#case-manager');
  expect(screen.getByRole('heading', { name: 'Choose where to test' })).toBeVisible();
  await travel('forward', '');
  expect(screen.getByRole('region', { name: 'Provider editor' })).toBeVisible();
});
it('F002: multi-entry Back followed by Forward restores the recorded Manager page, not its last hidden page', async () => {
  render(<EntryApp />);
  await enterManager();
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  await travel(-2, '');
  expect(screen.getByRole('button', { name: 'FSQ Case Manager' })).toBeVisible();
  await travel('forward', '#case-manager');
  expect(screen.getByRole('heading', { name: 'Choose where to test' })).toBeVisible();
  await travel('forward', '');
  expect(screen.getByRole('region', { name: 'Provider editor' })).toBeVisible();
});
it.each(['Dirty', 'Pending', 'Unknown'])('F002: real Manager %s guard survives browser navigation', async state => {
  render(<EntryApp />);
  await enterManager();
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  fireEvent.click(screen.getByRole('button', { name: `${state} Provider` }));
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  await travel('back', '');
  expect(screen.getByRole('region', { name: 'Provider editor' })).toBeVisible();
  if (state === 'Pending') expect(confirm).not.toHaveBeenCalled();
  else expect(confirm).toHaveBeenCalled();
  // A second Back attempts the original chooser rather than another Manager page.
  await travel('back', '');
  expect(screen.getByRole('region', { name: 'Provider editor' })).toBeVisible();
  expect(window.history.state?.cpEntry).toBe('case-manager');
});
it.each(['create', 'add-platform'])('F005: returning refreshes %s inventory without replacing explicit selection', async change => {
  const quick: WorkspaceRegistryEntry = { ...workspace, name: 'quick-test' };
  if (change === 'add-platform') vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [workspace, quick] });
  render(<EntryApp />);
  await enterManager();
  const chooser = screen.getByRole('region', { name: 'Choose a Workspace' });
  fireEvent.click(within(chooser).getByRole('button', { name: /existing/ }));
  await screen.findByRole('heading', { name: 'Start testing in existing' });
  fireEvent.click(screen.getByRole('link', { name: 'Back to start' }));
  fireEvent.click(screen.getByRole('button', { name: 'Quick Test' }));
  const updated = { ...quick, platforms: [...quick.platforms, { platform: 'android' as const, status: 'available' as const, message: 'Available', configPath: '/isolated/quick/config.android.yaml' }] };
  let complete!: (value: { workspaces: WorkspaceRegistryEntry[] }) => void;
  vi.mocked(controlPlaneClient.workspaces).mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  fireEvent.click(screen.getByRole('link', { name: 'Back to start' }));
  fireEvent.click(screen.getByRole('button', { name: 'FSQ Case Manager' }));
  expect(screen.getByText('Loading registered Workspaces…')).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Start testing in existing' })).not.toBeInTheDocument();
  await act(async () => complete({ workspaces: [workspace, updated] }));
  await screen.findByRole('button', { name: /quick-test.*web, android/ });
  expect(screen.getByRole('heading', { name: 'Start testing in existing' })).toBeVisible();
  expect(controlPlaneClient.workspaces).toHaveBeenCalledTimes(2);
});

it.each(['valid', 'removed', 'unavailable'])('F004/F005 residual: real Runner preserves only %s platform/target through activation registry revalidation', async validity => {
  const ready = { status: 'ready' as const, message: 'Ready', action: '' };
  let busy = false;
  const task: RunSnapshot = {
    requestId: 'quick-request', workflow: 'quick-test', platform: 'web', targetId: 'chrome', mode: 'explore',
    workspaceName: null, application: null, platformRevision: null, runId: null, status: 'preparing',
    source: { goal: 'Check https://example.test title' }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [], activeStep: null, result: null, summary: 'Preparing', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false, error: null,
  };
  const idle: BootstrapResponse = { apiVersion: '1', platforms: [{ id: 'web', label: 'Web' }], busy: false, activeTask: null };
  vi.mocked(controlPlaneClient.bootstrap).mockImplementation(async () => busy ? { ...idle, busy: true, activeTask: task } : idle);
  vi.spyOn(controlPlaneClient, 'readiness').mockResolvedValue({ workspaceName: workspace.name, platformId: 'web', workspace: ready, platform: ready, provider: ready, target: ready, strict: ready });
  vi.spyOn(controlPlaneClient, 'targets').mockResolvedValue({ platform: 'web', targetLabel: 'Browser', targets: ['chrome', 'chrome-beta'].map((id, index) => ({ id, label: id, description: '', status: 'ready', selectable: true, isDefault: index === 0, metadata: {} })) });
  vi.spyOn(controlPlaneClient, 'cases').mockResolvedValue({ platform: 'web', cases: [], truncated: false });
  vi.spyOn(controlPlaneClient, 'startRun').mockResolvedValue({ requestId: 'must-not-start' });
  vi.spyOn(controlPlaneClient, 'quickTestContext').mockImplementation(async (platform, targetId) => ({ platform, targetId: targetId ?? null, canStart: true, checks: [], choices: [{ id: 'chrome', label: 'Chrome', selectable: true, status: 'ready' }], diagnostics: quickDiagnostics(platform, targetId ?? null) }));
  vi.spyOn(controlPlaneClient, 'startQuickTest').mockImplementation(async () => { busy = true; return { requestId: task.requestId }; });
  vi.spyOn(controlPlaneClient, 'runSnapshot').mockResolvedValue(task);
  vi.spyOn(controlPlaneClient, 'cancelRun').mockResolvedValue(task);
  render(<EntryApp />);
  await enterManager();
  fireEvent.click(screen.getByRole('button', { name: 'Test Runner' }));
  fireEvent.change(screen.getByLabelText('Workspace'), { target: { value: workspace.name } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Platform' }), { target: { value: 'web' } });
  await screen.findByRole('option', { name: 'chrome-beta' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Browser' }), { target: { value: 'chrome-beta' } });
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Keep Manager goal' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start exploration' })).toBeEnabled());
  fireEvent.click(screen.getByRole('link', { name: 'Back to start' }));
  fireEvent.click(screen.getByRole('button', { name: 'Quick Test' }));
  await screen.findByRole('option', { name: 'Web' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Platform' }), { target: { value: 'web' } });
  await screen.findByRole('option', { name: 'Chrome' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Browser' }), { target: { value: 'chrome' } });
  fireEvent.change(screen.getByLabelText('What do you want to test?'), { target: { value: task.source.goal } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Start Quick Test' }));
  await screen.findByText('Preparing');
  let complete!: (value: { workspaces: WorkspaceRegistryEntry[] }) => void;
  vi.mocked(controlPlaneClient.workspaces).mockImplementationOnce(() => new Promise(resolve => { complete = resolve; }));
  fireEvent.click(screen.getByRole('link', { name: 'Back to start' }));
  fireEvent.click(screen.getByRole('button', { name: 'FSQ Case Manager' }));
  expect(screen.getByRole('button', { name: 'Start exploration' })).toBeDisabled();
  const after: WorkspaceRegistryEntry = {
    ...workspace,
    platforms: validity === 'removed' ? [] : validity === 'unavailable' ? [{ ...workspace.platforms[0], status: 'unavailable' }] : workspace.platforms,
  };
  await act(async () => complete({ workspaces: [after, { ...workspace, name: 'quick-test' }] }));
  expect(screen.getByLabelText('Workspace')).toHaveValue(workspace.name);
  expect(screen.getByRole('option', { name: 'quick-test' })).toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveValue('Keep Manager goal');
  if (validity === 'valid') {
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Platform' })).toHaveValue('web'));
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Browser' })).toHaveValue('chrome-beta'));
    expect(screen.getByRole('button', { name: 'Start exploration' })).toBeDisabled();
    const refresh = screen.getByRole('button', { name: 'Refresh readiness, targets, and cases' });
    await waitFor(() => expect(refresh).toBeEnabled());
    busy = false;
    fireEvent.click(refresh);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start exploration' })).toBeEnabled());
    expect(screen.getByRole('combobox', { name: 'Browser' })).toHaveValue('chrome-beta');
  } else {
    expect(screen.getByRole('combobox', { name: 'Platform' })).toHaveValue('');
    expect(screen.getByRole('combobox', { name: 'Target' })).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Start exploration' })).toBeDisabled();
  }
  expect(controlPlaneClient.startQuickTest).toHaveBeenCalledTimes(1);
  expect(controlPlaneClient.startRun).not.toHaveBeenCalled();
  expect(controlPlaneClient.cancelRun).not.toHaveBeenCalled();
});
