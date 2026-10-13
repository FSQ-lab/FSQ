import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { controlPlaneClient } from '../../api/controlPlaneClient';
import { buildSafeRunResult, RunsPage } from './RunsPage';

beforeEach(() => vi.restoreAllMocks());

const detail = (overrides: Record<string, unknown> = {}): Awaited<ReturnType<typeof controlPlaneClient.runReport>> => ({
  workspace: 'Demo workspace',
  run_id: 'run/1',
  platform: 'web',
  warnings: [],
  report: {
    schema_version: 'fsq.report/v1',
    run: { run_id: 'run/1', platform: 'web', mode: 'strict', gate: { status: 'passed', reasons: [] } },
    source: { case_id: 'must-not-copy', path: '/secret/case.fsq.yaml' },
    execution: { outcome: 'success', summary: 'must-not-copy' },
    verification: {},
    evidence: { status: 'complete', secret: 'must-not-copy' },
    processing: {},
    steps: [],
    artifacts: [],
    tool_calls: [],
    logs: [{ message: 'must-not-copy' }],
    metrics: {},
    comparison: {},
    lineage: {},
    warnings: [],
  },
  ...overrides,
} as Awaited<ReturnType<typeof controlPlaneClient.runReport>>);

it('builds the exact safe result payload with fixed encoding and one trailing newline', () => {
  expect(buildSafeRunResult(detail(), { workspace: 'Demo workspace', platform: 'web', run: 'run/1' })).toBe(
    'FSQ Run result\n' +
    'Workspace: Demo workspace\n' +
    'Platform: web\n' +
    'Run ID: run/1\n' +
    'Mode: strict\n' +
    'Gate: passed\n' +
    'Open report: /#runs?workspace=Demo+workspace&platform=web&run=run%2F1\n',
  );
});

it('allows only recognized mode and gate values and excludes unsafe report fields', () => {
  const recognized = detail();
  recognized.report.run.mode = 'explore';
  recognized.report.run.gate = { status: 'failed', reasons: ['must-not-copy'] };
  const payload = buildSafeRunResult(recognized, { workspace: 'Demo workspace', platform: 'web', run: 'run/1' })!;
  expect(payload).toContain('Mode: explore\nGate: failed\n');
  for (const excluded of ['must-not-copy', '/secret/case.fsq.yaml', 'complete', 'success']) expect(payload).not.toContain(excluded);

  const unknown = detail();
  unknown.report.run.mode = 'future-mode';
  unknown.report.run.gate = { status: 'future-gate' };
  expect(buildSafeRunResult(unknown, { workspace: 'Demo workspace', platform: 'web', run: 'run/1' })).toContain('Mode: unknown\nGate: unknown\n');
});

it.each(['passed', 'failed', 'error', 'incomplete'])('preserves the recognized public gate %s', gate => {
  const response = detail();
  response.report.run.gate = { status: gate };
  expect(buildSafeRunResult(response, { workspace: 'Demo workspace', platform: 'web', run: 'run/1' })).toContain(`Gate: ${gate}\n`);
});

it.each(['bad\nworkspace', 'bad\u0085platform', 'bad\u2028run'])('rejects control-character or Unicode-line-separator identity %s', identity => {
  const response = detail({ workspace: identity, platform: identity, run_id: identity });
  response.report.run.run_id = identity;
  response.report.run.platform = identity;
  expect(buildSafeRunResult(response, { workspace: identity, platform: identity as 'web', run: identity })).toBeNull();
});

it('offers copy only after persisted detail matches the complete route identity', async () => {
  let resolve!: (value: Awaited<ReturnType<typeof controlPlaneClient.runReport>>) => void;
  vi.spyOn(controlPlaneClient, 'runReport').mockImplementation(() => new Promise(done => { resolve = done; }));
  render(<RunsPage route={{ workspace: 'Demo workspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(screen.queryByRole('button', { name: 'Copy safe result' })).not.toBeInTheDocument();
  await act(async () => resolve(detail({ run_id: 'different' })));
  expect(screen.queryByRole('button', { name: 'Copy safe result' })).not.toBeInTheDocument();
});

it('copies the immutable safe payload and announces success without moving focus', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue(detail());
  render(<RunsPage route={{ workspace: 'Demo workspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  const button = await screen.findByRole('button', { name: 'Copy safe result' });
  button.focus();
  await userEvent.click(button);
  expect(writeText).toHaveBeenCalledWith(buildSafeRunResult(detail(), { workspace: 'Demo workspace', platform: 'web', run: 'run/1' }));
  expect(await screen.findByText('Safe Run result copied.')).toHaveAttribute('aria-live', 'polite');
  expect(screen.getByRole('button', { name: 'Copied' })).toHaveFocus();
});

it('exposes a disabled busy state while clipboard writing is pending', async () => {
  let finish!: () => void;
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn(() => new Promise<void>(resolve => { finish = resolve; })) } });
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue(detail());
  render(<RunsPage route={{ workspace: 'Demo workspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Copy safe result' }));
  expect(screen.getByRole('button', { name: 'Copying…' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Copying…' })).toHaveAttribute('aria-busy', 'true');
  await act(async () => finish());
});

it.each(['failure', 'unsupported'])('shows and selects the identical manual fallback on clipboard %s', async kind => {
  if (kind === 'failure') Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
  else Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
  const select = vi.spyOn(HTMLTextAreaElement.prototype, 'select');
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue(detail());
  render(<RunsPage route={{ workspace: 'Demo workspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Copy safe result' }));
  expect(await screen.findByText('Copy failed. Select and copy the safe result below.')).toHaveAttribute('role', 'alert');
  const fallback = screen.getByRole('textbox', { name: 'Safe Run result for manual copy' });
  expect(fallback).toHaveValue(buildSafeRunResult(detail(), { workspace: 'Demo workspace', platform: 'web', run: 'run/1' }));
  expect(fallback).toHaveAttribute('readonly');
  expect(fallback).toHaveFocus();
  expect(select).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'Copy safe result' })).toBeEnabled();
});

it('allows retry after copy failure', async () => {
  const writeText = vi.fn().mockRejectedValueOnce(new Error('denied')).mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue(detail());
  render(<RunsPage route={{ workspace: 'Demo workspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Copy safe result' }));
  await screen.findByText('Copy failed. Select and copy the safe result below.');
  await userEvent.click(screen.getByRole('button', { name: 'Copy safe result' }));
  expect(await screen.findByText('Safe Run result copied.')).toBeVisible();
  expect(writeText).toHaveBeenCalledTimes(2);
});

it('disables and resets safe copy while persisted detail is being revalidated, then restores it', async () => {
  let finishRefresh!: (value: Awaited<ReturnType<typeof controlPlaneClient.runReport>>) => void;
  vi.spyOn(controlPlaneClient, 'runReport')
    .mockResolvedValueOnce(detail())
    .mockImplementationOnce(() => new Promise(resolve => { finishRefresh = resolve; }));
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });
  render(<RunsPage route={{ workspace: 'Demo workspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Copy safe result' }));
  expect(await screen.findByRole('button', { name: 'Copied' })).toBeEnabled();

  await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));

  expect(await screen.findByRole('button', { name: 'Copy safe result' })).toBeDisabled();
  expect(screen.getByText('Copy is unavailable while persisted Run detail is being revalidated.')).toBeVisible();
  expect(screen.queryByText('Safe Run result copied.')).not.toBeInTheDocument();
  await act(async () => finishRefresh(detail()));
  expect(await screen.findByRole('button', { name: 'Copy safe result' })).toBeEnabled();
  expect(screen.queryByText('Copy is unavailable while persisted Run detail is being revalidated.')).not.toBeInTheDocument();
});

it('keeps stale detail visible but disables copy after failed refresh until revalidation succeeds', async () => {
  vi.spyOn(controlPlaneClient, 'runReport')
    .mockResolvedValueOnce(detail())
    .mockRejectedValueOnce(new Error('refresh failed'))
    .mockResolvedValueOnce(detail());
  render(<RunsPage route={{ workspace: 'Demo workspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByRole('button', { name: 'Copy safe result' })).toBeEnabled();

  await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));

  expect(await screen.findByText(/Previously loaded report is stale/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Copy safe result' })).toBeDisabled();
  expect(screen.getByText('Copy is unavailable until persisted Run detail is fully revalidated.')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Result and failure' })).toBeVisible();

  await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  expect(await screen.findByRole('button', { name: 'Copy safe result' })).toBeEnabled();
});

it('disables safe copy immediately when the requested comparison no longer matches displayed detail', async () => {
  vi.spyOn(controlPlaneClient, 'runReport')
    .mockResolvedValueOnce(detail())
    .mockImplementationOnce(() => new Promise(() => undefined));
  render(<RunsPage route={{ workspace: 'Demo workspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByRole('button', { name: 'Copy safe result' })).toBeEnabled();
  await userEvent.type(screen.getByLabelText('Baseline Run ID'), 'baseline-2');
  await userEvent.click(screen.getByRole('button', { name: 'Compare baseline' }));
  expect(screen.getByRole('button', { name: 'Copy safe result' })).toBeDisabled();
  expect(screen.getByText('Copy is unavailable while the requested persisted detail is unresolved.')).toBeVisible();
});

it('disables copy for an unsafe persisted identity with the required reason', async () => {
  const unsafe = detail({ workspace: 'bad\nworkspace' });
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue(unsafe);
  render(<RunsPage route={{ workspace: 'bad\nworkspace', platform: 'web', run: 'run/1' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByRole('button', { name: 'Copy safe result' })).toBeDisabled();
  expect(screen.getByText('A persistent Run ID is not available.')).toBeVisible();
});

it.each(['baseline', 'related', 'format', 'refresh', 'failed-refresh'])('invalidates pending and completed exports after %s changes', async change => {
  const response: Awaited<ReturnType<typeof controlPlaneClient.runReport>> = {workspace:'demo',run_id:'r1',platform:'web',warnings:[],report:{schema_version:'fsq.report/v1',run:{run_id:'r1',platform:'web',gate:{status:'passed',reasons:[]}},source:{},execution:{outcome:'success'},verification:{status:'success'},evidence:{status:'complete'},processing:{},steps:[],artifacts:[],tool_calls:[],logs:[],metrics:{},comparison:{},lineage:{},warnings:[]}};
  const reports = vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue(response);
  let finish!: (value: Awaited<ReturnType<typeof controlPlaneClient.exportReport>>) => void;
  let signal: AbortSignal | undefined;
  const exports = vi.spyOn(controlPlaneClient, 'exportReport').mockImplementation((_workspace, _platform, _run, _format, _baseline, selectedSignal) => {
    signal = selectedSignal;
    return new Promise(resolve => { finish = resolve; });
  });
  const result: Awaited<ReturnType<typeof controlPlaneClient.exportReport>> = {run_id:'r1',platform:'web',export_id:'old',format:'html',files:[{file_id:'report',name:'old-report.html',mime_type:'text/html',size_bytes:10,download_url:'/old-export'}],warnings:[]};
  render(<RunsPage route={{workspace:'demo',platform:'web',run:'r1'}} workspaces={[]} onNavigate={vi.fn()} />);
  await screen.findByText('Gate: passed');
  const changeContext = async (suffix: string) => {
    if (change === 'baseline') {
      await userEvent.clear(screen.getByLabelText('Baseline Run ID'));
      await userEvent.type(screen.getByLabelText('Baseline Run ID'), 'baseline-' + suffix);
      await userEvent.click(screen.getByRole('button',{name:'Compare baseline'}));
    } else if (change === 'related') {
      await userEvent.clear(screen.getByLabelText('Related Run IDs'));
      await userEvent.type(screen.getByLabelText('Related Run IDs'), 'related-' + suffix);
      await userEvent.click(screen.getByRole('button',{name:'Load related Runs'}));
    } else if (change === 'format') {
      await userEvent.selectOptions(screen.getByLabelText('Format'), suffix === 'first' ? 'json' : 'junit');
    } else {
      if (change === 'failed-refresh') reports.mockRejectedValueOnce(new Error('Revalidation failed'));
      await userEvent.click(screen.getByRole('button',{name:'Refresh'}));
    }
  };
  await userEvent.click(screen.getByRole('button',{name:'Export report'}));
  await changeContext('first');
  expect(signal?.aborted).toBe(true);
  await act(async () => finish(result));
  expect(screen.queryByRole('link',{name:/old-report/})).not.toBeInTheDocument();
  if (change === 'failed-refresh') await userEvent.click(screen.getByRole('button',{name:'Refresh'}));
  await waitFor(() => expect(screen.getByRole('button',{name:'Export report'})).toBeEnabled());
  exports.mockResolvedValue(result);
  await userEvent.click(screen.getByRole('button',{name:'Export report'}));
  expect(await screen.findByRole('link',{name:/old-report/})).toBeVisible();
  await changeContext('second');
  expect(screen.queryByRole('link',{name:/old-report/})).not.toBeInTheDocument();
});

it('explains omitted logs and offers the full persisted source', async () => {
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue({ workspace: 'demo', run_id: 'r1', platform: 'web', warnings: [], report: { schema_version: 'fsq.report/v1', run: { run_id: 'r1', platform: 'web', gate: { status: 'failed', reasons: [] }, display_omissions: { logs: { original_count: 10000, returned_count: 0, original_size_bytes: 9000000, reason: 'aggregate_log_display_budget', full_artifacts: [{ run_id: 'r1', artifact_id: 'runtime-events' }] } } }, source: {}, execution: {}, verification: {}, evidence: {}, processing: {}, steps: [{ step_execution_id: 's1', status: 'failed' }], artifacts: [], tool_calls: [], logs: [], metrics: {}, comparison: {}, lineage: {}, warnings: [] } });
  render(<RunsPage route={{ workspace: 'demo', platform: 'web', run: 'r1', step: 's1' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByText(/10000 persisted entries/)).toBeVisible();
  expect(screen.getByRole('link', { name: 'Download complete log' })).toBeVisible();
  expect(screen.queryByText('No persisted logs for this execution.')).not.toBeInTheDocument();
});

it('displays canonical event kinds when persisted labels are blank', async () => {
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue({ workspace: 'demo', run_id: 'r1', platform: 'web', warnings: [], report: { schema_version: 'fsq.report/v1', run: { run_id: 'r1', platform: 'web', gate: { status: 'passed', reasons: [] } }, source: {}, execution: {}, verification: {}, evidence: {}, processing: {}, steps: [{ step_execution_id: 's1', status: 'passed' }], artifacts: [], tool_calls: [], logs: [{ step_execution_id: 's1', label: '', tool: '', event_kind: 'phase_finish' }], metrics: {}, comparison: {}, lineage: {}, warnings: [] } });
  render(<RunsPage route={{ workspace: 'demo', platform: 'web', run: 'r1', step: 's1' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByRole('cell', { name: 'phase_finish' })).toBeVisible();
});

it('shows bounded persisted history and keeps failed outcome visible', async () => {
  vi.spyOn(controlPlaneClient, 'history').mockResolvedValue({ workspace: 'demo', platforms: ['web'], filters: {}, matched_count: 4, returned_count: 1, truncated: true, warnings: [], runs: [{ run_id: 'failed-run', platform: 'web', mode: 'strict', status: 'failed', source: { case_id: 'checkout' }, warnings: [] }] });
  render(<RunsPage route={{ workspace: 'demo' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByText('failed-run')).toBeInTheDocument();
  expect(screen.getByText('Showing 1 of 4 matching Runs. Narrow the filters or increase the limit.')).toBeInTheDocument();
  expect(screen.getByText('failed', { selector: 'span' })).toBeInTheDocument();
});

it('labels a failed step on a successful Run as a recovered attempt', async () => {
  vi.spyOn(controlPlaneClient, 'history').mockResolvedValue({ workspace: 'demo', platforms: ['web'], filters: {}, matched_count: 1, returned_count: 1, truncated: false, warnings: [], runs: [{ run_id: 'recovered-run', platform: 'web', mode: 'explore', status: 'success', result: { summary: 'Verification succeeded.', failed_step: 'attempt-1' }, warnings: [] }] });
  render(<RunsPage route={{ workspace: 'demo' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByText('Recovered attempt: attempt-1')).toBeVisible();
  expect(screen.queryByText('Failed execution: attempt-1')).not.toBeInTheDocument();
});

it('inspects snapshot-only evidence and preserves missing verification', async () => {
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue({ workspace: 'demo', run_id: 'r1', platform: 'web', warnings: [], report: { schema_version: 'fsq.report/v1', run: { run_id: 'r1', platform: 'web', status: 'failed', gate: { status: 'incomplete', reasons: ['verification_unavailable'] } }, source: { case_id: 'checkout' }, execution: { outcome: 'failed' }, verification: { status: 'inconclusive' }, evidence: { status: 'partial' }, processing: {}, steps: [{ step_execution_id: 's1', action_name: 'assertText', status: 'failed', error_message: 'Expected Checkout' }], tool_calls: [], logs: [], artifacts: [{ artifact_id: 'tree-1', run_id: 'r1', step_execution_id: 's1', kind: 'ui_snapshot', status: 'available', content: 'Observed Basket' }], metrics: {}, lineage: {}, comparison: {}, warnings: [] } });
  render(<RunsPage route={{ workspace: 'demo', platform: 'web', run: 'r1', step: 's1' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByRole('heading', { name: 'Selected execution evidence' })).toBeInTheDocument();
  expect(screen.getByText('inconclusive')).toBeInTheDocument();
  expect(await screen.findByText('Observed Basket')).toBeInTheDocument();
  await waitFor(() => expect(controlPlaneClient.runReport).toHaveBeenCalledTimes(1));
});

it('ignores a stale history response after Workspace scope changes', async () => {
  let resolveOld!: (value: Awaited<ReturnType<typeof controlPlaneClient.history>>) => void;
  vi.spyOn(controlPlaneClient, 'history').mockImplementation(name => name === 'old' ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve({ workspace: 'new', platforms: ['web'], filters: {}, matched_count: 0, returned_count: 0, truncated: false, warnings: [], runs: [] }));
  const view = render(<RunsPage route={{ workspace: 'old' }} workspaces={[]} onNavigate={vi.fn()} />);
  view.rerender(<RunsPage route={{ workspace: 'new' }} workspaces={[]} onNavigate={vi.fn()} />);
  await screen.findByText(/No Runs have been recorded/);
  resolveOld({ workspace: 'old', platforms: ['web'], filters: {}, matched_count: 1, returned_count: 1, truncated: false, warnings: [], runs: [{ run_id: 'stale-run', platform: 'web', status: 'success', warnings: [] }] });
  await waitFor(() => expect(screen.queryByText('stale-run')).not.toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  expect(controlPlaneClient.history).toHaveBeenLastCalledWith('new', expect.any(Object), expect.any(AbortSignal));
});

it('distinguishes registry loading, empty history and complete list counts', async () => {
  const view = render(<RunsPage route={{}} workspaces={[]} registryStatus="loading" onNavigate={vi.fn()} />);
  expect(screen.getByText('Loading registered Workspaces…')).toBeVisible();
  expect(screen.queryByText('No registered Workspaces are available.')).not.toBeInTheDocument();
  vi.spyOn(controlPlaneClient, 'history').mockResolvedValue({ workspace: 'demo', platforms: ['web'], filters: {}, matched_count: 0, returned_count: 0, truncated: false, warnings: [], runs: [] });
  view.rerender(<RunsPage route={{ workspace: 'demo' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByText('No Runs have been recorded in this Workspace scope.')).toBeVisible();
  expect(screen.getByText('0 returned · 0 matching Runs')).toBeVisible();
});

it('identifies an old comparison as stale after a new baseline fails', async () => {
  const report: Awaited<ReturnType<typeof controlPlaneClient.runReport>> = { workspace: 'demo', run_id: 'r1', platform: 'web', warnings: [], report: { schema_version: 'fsq.report/v1', run: { run_id: 'r1', platform: 'web', status: 'failed', gate: { status: 'failed', reasons: [] } }, source: {}, execution: { outcome: 'failed' }, verification: {}, evidence: {}, processing: {}, steps: [], tool_calls: [], logs: [], artifacts: [], metrics: {}, lineage: {}, comparison: {}, warnings: [] } };
  vi.spyOn(controlPlaneClient, 'runReport').mockImplementation((_w, _p, _r, baseline) => baseline === 'bad' ? Promise.reject(new Error('Incompatible baseline')) : Promise.resolve(report));
  render(<RunsPage route={{ workspace: 'demo', platform: 'web', run: 'r1' }} workspaces={[]} onNavigate={vi.fn()} />);
  await screen.findByRole('heading', { name: 'Comparison' });
  await userEvent.type(screen.getByLabelText('Baseline Run ID'), 'bad');
  await userEvent.click(screen.getByRole('button', { name: 'Compare baseline' }));
  expect(await screen.findByText('Incompatible baseline')).toBeVisible();
  expect(screen.getByText(/Showing the previous report/)).toBeVisible();
});

it('keeps the previous snapshot while replacement evidence is loading', async () => {
  let resolveArtifact!: (value: string) => void;
  vi.spyOn(controlPlaneClient, 'historyArtifactText').mockImplementation(() => new Promise(resolve => { resolveArtifact = resolve; }));
  const response: Awaited<ReturnType<typeof controlPlaneClient.runReport>> = { workspace: 'demo', run_id: 'r1', platform: 'web', warnings: [], report: { schema_version: 'fsq.report/v1', run: { run_id: 'r1', platform: 'web', gate: { status: 'failed', reasons: [] } }, source: {}, execution: { outcome: 'failed' }, verification: {}, evidence: {}, processing: {}, steps: [{ step_execution_id: 's1', status: 'failed' }, { step_execution_id: 's2', status: 'passed' }], artifacts: [{ artifact_id: 'a1', step_execution_id: 's1', kind: 'ui_snapshot', availability: 'available', content: 'Previous evidence' }, { artifact_id: 'a2', step_execution_id: 's2', kind: 'ui_snapshot', availability: 'available' }], tool_calls: [], logs: [], metrics: {}, comparison: {}, lineage: {}, warnings: [] } };
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue(response);
  const view = render(<RunsPage route={{ workspace: 'demo', platform: 'web', run: 'r1', step: 's1' }} workspaces={[]} onNavigate={vi.fn()} />);
  await screen.findByText('Previous evidence');
  view.rerender(<RunsPage route={{ workspace: 'demo', platform: 'web', run: 'r1', step: 's2' }} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByText(/Showing evidence for s1 while loading selected execution s2/)).toBeVisible();
  expect(screen.getByText('Previous evidence')).toBeVisible();
  resolveArtifact('Replacement evidence');
  expect(await screen.findByText('Replacement evidence')).toBeVisible();
  expect(screen.queryByText('Previous evidence')).not.toBeInTheDocument();
});

it('does not fetch server-omitted image display and retains explicit download', async () => {
  const image = vi.spyOn(controlPlaneClient, 'historyArtifactImage');
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue({ workspace: 'demo', run_id: 'r1', platform: 'web', warnings: [], report: { schema_version: 'fsq.report/v1', run: { run_id: 'r1', platform: 'web', gate: { status: 'error', reasons: [] } }, source: {}, execution: {}, verification: {}, evidence: {}, processing: {}, steps: [{step_execution_id:'s1',status:'failed'}], artifacts: [{artifact_id:'omitted',step_execution_id:'s1',kind:'screenshot',availability:'available',display_availability:'omitted',unavailable_reason:'raster_resource_limit'}], tool_calls: [], logs: [], metrics: {}, comparison: {}, lineage: {}, warnings: [] } });
  render(<RunsPage route={{workspace:'demo',platform:'web',run:'r1',step:'s1'}} workspaces={[]} onNavigate={vi.fn()} />);
  expect(await screen.findByText(/Display omitted/)).toBeVisible();
  expect(screen.getByRole('link',{name:'Download artifact'})).toBeVisible();
  expect(image).not.toHaveBeenCalled();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

it('keeps the screenshot header directly attached before the image and qualifiers after it', async () => {
  const OriginalImage = globalThis.Image;
  class LoadedImage {
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    set src(_value: string) { queueMicrotask(() => this.onload?.()); }
  }
  vi.stubGlobal('Image', LoadedImage);
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  vi.spyOn(controlPlaneClient, 'historyArtifactImage').mockResolvedValue(new Blob(['image']));
  vi.spyOn(controlPlaneClient, 'runReport').mockResolvedValue({ workspace: 'demo', run_id: 'r1', platform: 'web', warnings: [], report: { schema_version: 'fsq.report/v1', run: { run_id: 'r1', platform: 'web', gate: { status: 'failed', reasons: [] } }, source: {}, execution: { outcome: 'failed' }, verification: {}, evidence: {}, processing: {}, steps: [{ step_execution_id: 's1', status: 'failed' }], artifacts: [{ artifact_id: 'shot', step_execution_id: 's1', kind: 'screenshot', availability: 'available', capture_reason: 'before-action' }], tool_calls: [], logs: [], metrics: {}, comparison: {}, lineage: {}, warnings: [] } });
  render(<RunsPage route={{workspace:'demo',platform:'web',run:'r1',step:'s1'}} workspaces={[]} onNavigate={vi.fn()} />);
  const image = await screen.findByRole('img');
  const figure = image.closest('figure')!;
  expect(figure.children[0].tagName).toBe('FIGCAPTION');
  expect(figure.children[1]).toBe(image);
  expect(figure.children[2]).toHaveAttribute('aria-label', 'Capture qualifications');
  vi.stubGlobal('Image', OriginalImage);
});

it('sends a supported Since duration and shows safe comparison repair guidance', async () => {
  vi.spyOn(controlPlaneClient,'history').mockResolvedValue({workspace:'demo',platforms:['web'],filters:{},matched_count:0,returned_count:0,truncated:false,warnings:[],runs:[]});
  const view=render(<RunsPage route={{workspace:'demo'}} workspaces={[]} onNavigate={vi.fn()}/>);
  await userEvent.type(screen.getByLabelText('Since'),'7d');
  await waitFor(()=>expect(controlPlaneClient.history).toHaveBeenLastCalledWith('demo',expect.objectContaining({since:'7d'}),expect.any(AbortSignal)));
  const {ControlPlaneApiError}=await import('../../api/controlPlaneClient');
  vi.spyOn(controlPlaneClient,'runReport').mockRejectedValue(new ControlPlaneApiError(400,{code:'comparison_failed',message:'Run report operation failed.',action:'Choose a Run with matching source.',details:{reason:'incompatible_source'}}));
  view.rerender(<RunsPage route={{workspace:'demo',platform:'web',run:'r1'}} workspaces={[]} onNavigate={vi.fn()}/>);
  expect(await screen.findByText('Choose a Run with matching source.')).toBeVisible();
  expect(screen.getByText('incompatible_source')).toBeVisible();
});

it('refresh retries failed artifact fetch even when report facts are unchanged', async () => {
  const response: Awaited<ReturnType<typeof controlPlaneClient.runReport>>={workspace:'demo',run_id:'r1',platform:'web',warnings:[],report:{schema_version:'fsq.report/v1',run:{run_id:'r1',platform:'web',gate:{status:'error',reasons:[]}},source:{},execution:{},verification:{},evidence:{},processing:{},steps:[{step_execution_id:'s1',status:'failed'}],artifacts:[{artifact_id:'tree',step_execution_id:'s1',kind:'ui_snapshot',availability:'available'}],tool_calls:[],logs:[],metrics:{},comparison:{},lineage:{},warnings:[]}};
  vi.spyOn(controlPlaneClient,'runReport').mockResolvedValue(response);
  const read=vi.spyOn(controlPlaneClient,'historyArtifactText').mockRejectedValueOnce(new Error('Temporary read failure')).mockResolvedValue('Retried evidence');
  render(<RunsPage route={{workspace:'demo',platform:'web',run:'r1',step:'s1'}} workspaces={[]} onNavigate={vi.fn()}/>);
  expect(await screen.findByText('Temporary read failure')).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Refresh',}));
  expect(await screen.findByText('Retried evidence')).toBeVisible();
  expect(read.mock.calls.length).toBeGreaterThan(1);
});

it.each(['workspace_unavailable','network_error'])('marks a retained passed report stale and disables export after %s revalidation failure',async code=>{
  const {ControlPlaneApiError}=await import('../../api/controlPlaneClient');
  const response:Awaited<ReturnType<typeof controlPlaneClient.runReport>>={workspace:'demo',run_id:'r1',platform:'web',warnings:[],report:{schema_version:'fsq.report/v1',run:{run_id:'r1',platform:'web',gate:{status:'passed',reasons:[]}},source:{},execution:{outcome:'success'},verification:{status:'success'},evidence:{status:'complete'},processing:{},steps:[],artifacts:[],tool_calls:[],logs:[],metrics:{},comparison:{},lineage:{},warnings:[]}};
  vi.spyOn(controlPlaneClient,'runReport').mockResolvedValueOnce(response).mockRejectedValue(new ControlPlaneApiError(code==='workspace_unavailable'?404:503,{code,message:'History scope is unavailable.',action:'Restore access and retry.'}));
  render(<RunsPage route={{workspace:'demo',platform:'web',run:'r1'}} workspaces={[]} onNavigate={vi.fn()}/>);
  expect(await screen.findByText('Gate: passed')).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Refresh'}));
  expect(await screen.findByText(/Previously loaded report is stale/)).toBeVisible();
  expect(screen.getByRole('button',{name:'Export report'})).toBeDisabled();
  expect(screen.getByText('Restore access and retry.')).toBeVisible();
});
