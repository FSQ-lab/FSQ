import { controlPlaneClient, validateRunSnapshot } from './controlPlaneClient';
import { quickDiagnostics } from '../test/quickTestFixtures';

afterEach(() => vi.restoreAllMocks());
it('F007: malformed JSON has a fixed safe error, while valid server errors retain their facts', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response('<html>bad gateway</html>', { status: 502 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ code: 'busy', message: 'Another task is active.', action: 'Wait for completion.', details: { requestId: 'other' } }), { status: 409 }));
  await expect(controlPlaneClient.quickTestContext('macos')).rejects.toMatchObject({ status: 502, body: { code: 'invalid_error_response' } });
  await expect(controlPlaneClient.startQuickTest({ platform: 'macos', goal: 'Check Notes' })).rejects.toMatchObject({ status: 409, body: { code: 'busy', message: 'Another task is active.', action: 'Wait for completion.', details: { requestId: 'other' } } });
  expect(fetch).toHaveBeenCalledTimes(2);
});
it.each([null, [], { code: 'failure', message: { unexpected: 'shape' }, action: 'Recheck' }, { code: 'failure', message: 'Failed', action: {} }, { code: 42, message: 'Failed', action: 'Recheck' }])('F007: safely rejects malformed error envelopes while retaining HTTP status', async body => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status: 503 }));
  await expect(controlPlaneClient.startQuickTest({ platform: 'macos', goal: 'Check Notes' })).rejects.toMatchObject({
    status: 503, body: { code: 'invalid_error_response', message: 'The Control Plane server returned an invalid error response.', action: 'Recheck the server task state before retrying.' },
  });
});
const context = { platform: 'macos', targetId: null, canStart: true, choices: [], checks: [{ id: 'application', status: 'pending', message: 'Awaiting identification', action: '' }], diagnostics: quickDiagnostics() };
const preparation = {
  requestId: 'q1', workflow: 'quick-test', application: null, platformRevision: null,
  workspaceName: null, targetId: null, runId: null, platform: 'macos', mode: 'explore', status: 'preparing',
  source: { goal: 'Open Notes and verify a new note' }, startedAt: '', completedAt: null, cancelRequested: false,
  events: [], activeStep: null, result: null, summary: 'Identifying application', screenshotRevision: 0,
  uiSnapshotRevision: 0, evidenceAvailable: false, reportAvailable: false, terminal: false, error: null,
};

it('accepts unresolved Quick preparation without invented identities', () => {
  expect(validateRunSnapshot(preparation)).toEqual(preparation);
  expect(() => validateRunSnapshot({ ...preparation, workflow: 'other' })).toThrow();
  expect(() => validateRunSnapshot({ ...preparation, workflow: undefined })).toThrow();
  expect(() => validateRunSnapshot({ ...preparation, workflow: undefined, workspaceName: 'quick-test', targetId: 'app' })).toThrow();
  expect(() => validateRunSnapshot({ ...preparation, error: { code: 3 } })).toThrow();
});

it('accepts pending application checks and omits desktop targetId from context and start', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(context)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ requestId: 'q1' }), { status: 202 }));
  expect(await controlPlaneClient.quickTestContext('macos')).toEqual(context);
  expect(String(fetch.mock.calls[0][0])).toBe('/api/control-plane/quick-test/context?platform=macos');
  await controlPlaneClient.startQuickTest({ platform: 'macos', goal: 'Verify Notes' });
  expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({ platform: 'macos', goal: 'Verify Notes' });
});

it.each([
  { ...context, checks: [{ id: 'application', status: 'unknown', message: '', action: '' }] },
  { ...context, choices: [{ id: 'x', label: 'x', selectable: true, status: 'unknown' }] },
  { ...context, canStart: 'true' },
])('rejects malformed context without fallback', async (body) => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)));
  await expect(controlPlaneClient.quickTestContext('macos')).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});

it.each([['android', 'serial:5555'], ['web', 'chrome-beta']] as const)('binds %s without workspace, mode, Case or Provider fields', async (platform, targetId) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ ...context, platform, targetId, diagnostics: quickDiagnostics(platform, targetId) })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ requestId: 'q1' }), { status: 202 }));
  await controlPlaneClient.quickTestContext(platform, targetId);
  const url = new URL(String(fetch.mock.calls[0][0]), 'http://localhost');
  expect([...url.searchParams]).toEqual([['platform', platform], ['targetId', targetId]]);
  await controlPlaneClient.startQuickTest({ platform, targetId, goal: 'Check the application' });
  expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({ platform, targetId, goal: 'Check the application' });
});

it('accepts Quick bootstrap provenance but never treats unknown workflow as Manager', async () => {
  const body = { apiVersion: '1', platforms: [{ id: 'macos', label: 'macOS' }], busy: true, activeTask: preparation };
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)));
  expect((await controlPlaneClient.bootstrap()).activeTask?.workflow).toBe('quick-test');
  fetch.mockResolvedValue(new Response(JSON.stringify({ ...body, activeTask: { ...preparation, workflow: 'unknown' } })));
  await expect(controlPlaneClient.bootstrap()).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});
