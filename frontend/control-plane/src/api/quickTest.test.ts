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

it.each([['android', 'serial:5555'], ['ios', 'Exact-iOS-UDID'], ['web', 'chrome-beta']] as const)('binds %s without workspace, mode, Case, signing or Provider fields', async (platform, targetId) => {
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

it('allows optional iOS discovery facts but rejects unknown kinds, states, and private fields', async () => {
  const choice = { id: 'ios-udid', label: 'iPhone', selectable: true, status: 'ready' };
  const body = { ...context, platform: 'ios', targetId: choice.id, diagnostics: { ...quickDiagnostics('ios', choice.id), targetId: choice.id } };
  const fetch = vi.spyOn(globalThis, 'fetch');
  for (const facts of [{}, { kind: null, state: null }, { kind: 'device', state: 'connected' }]) {
    const response = { ...body, choices: [{ ...choice, ...facts }] };
    fetch.mockResolvedValueOnce(new Response(JSON.stringify(response)));
    expect(await controlPlaneClient.quickTestContext('ios', choice.id)).toEqual(response);
  }
  for (const facts of [{ kind: 'emulator' }, { state: 'private raw discovery' }, { xcodeOrgId: 'private' }]) {
    fetch.mockResolvedValueOnce(new Response(JSON.stringify({ ...body, choices: [{ ...choice, ...facts }] })));
    await expect(controlPlaneClient.quickTestContext('ios', choice.id)).rejects.toMatchObject({ body: { code: 'invalid_response' } });
  }
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({ ...body, choices: [choice], diagnostics: { ...body.diagnostics, targetId: 'other-udid' } })));
  await expect(controlPlaneClient.quickTestContext('ios', choice.id)).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});

it('retains shared iOS diagnostics when target inventory independently blocks Start', async () => {
  const targetId = 'Disconnected-UDID';
  const reply = { ...context, platform: 'ios', targetId, canStart: false, diagnostics: quickDiagnostics('ios', targetId) };
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(reply)));
  expect(await controlPlaneClient.quickTestContext('ios', targetId)).toEqual(reply);
});

it('validates iOS task recovery separately from runnable Case Manager platforms', async () => {
  const task = { ...preparation, platform: 'ios', targetId: 'physical-udid' };
  const body = { apiVersion: '1', platforms: context.choices, quickTestPlatforms: [{ id: 'ios', label: 'iOS' }], busy: true, activeTask: task };
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)));
  expect((await controlPlaneClient.bootstrap()).activeTask).toEqual(task);
  expect(validateRunSnapshot(task)).toEqual(task);
  expect(validateRunSnapshot({ ...task, source: {} }).source).toEqual({});
  expect(() => validateRunSnapshot({ ...task, source: {}, workspaceName: 'quick-test' })).toThrow();
  expect(() => validateRunSnapshot({ ...preparation, source: {} })).toThrow();
  expect(() => validateRunSnapshot({ ...task, workflow: 'case-manager', workspaceName: 'quick-test' })).toThrow();
  expect(() => validateRunSnapshot({ ...task, targetId: '' })).toThrow();
});


it.each(['android', 'ios', 'web'] as const)('accepts null targetId while %s auto-target quick-test is preparing or failed before bind', async platform => {
  const task = { ...preparation, platform, targetId: null };
  const body = { apiVersion: '1', platforms: [{ id: 'android', label: 'Android' }, { id: 'web', label: 'Web' }], quickTestPlatforms: [{ id: 'ios', label: 'iOS' }], busy: true, activeTask: task };
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)));
  expect((await controlPlaneClient.bootstrap()).activeTask).toEqual(task);
  expect(validateRunSnapshot(task)).toEqual(task);
  // Once a runId is allocated, a non-empty valid targetId is required.
  expect(() => validateRunSnapshot({ ...task, runId: 'run-1', workspaceName: 'quick-test' })).toThrow();
  expect(() => validateRunSnapshot({ ...task, targetId: '' })).toThrow();
});
