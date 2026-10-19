import { controlPlaneClient } from './controlPlaneClient';
import { quickDiagnostics } from '../test/quickTestFixtures';
import androidConfigError from '../test/quick-test-android-config-error.json';

afterEach(() => vi.restoreAllMocks());
const diagnostics = quickDiagnostics();
const context = { platform: 'macos', targetId: null, canStart: true, choices: [], checks: [], diagnostics };
it('decodes the real HTTP configuration failure with matching selected-device identity', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(androidConfigError)));
  const decoded = await controlPlaneClient.quickTestContext('android', 'selected-device');
  expect(decoded).toEqual(androidConfigError);
  expect(decoded.targetId).toBe('selected-device');
  expect(decoded.diagnostics?.targetId).toBe('selected-device');
  expect(decoded.diagnostics?.checks.configuration).toMatchObject({ code: 'doctor.configuration_invalid', status: 'error' });
  expect(decoded.diagnostics?.commands.prepare.status).toBe('error');
  expect(decoded.canStart).toBe(false);
});
it('still rejects the old null nested identity in the actual captured configuration failure', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    ...androidConfigError, diagnostics: { ...androidConfigError.diagnostics, targetId: null },
  })));
  await expect(controlPlaneClient.quickTestContext('android', 'selected-device')).rejects.toMatchObject({
    body: { code: 'invalid_response' },
  });
});
it('accepts full pending diagnostics, nullable raw facts and not-applicable compatibility checks', async () => {
  const body = { ...context, checks: [{ id: 'suggestion_analyzer', status: 'not_applicable', message: 'Not needed', action: '' }], diagnostics: { ...diagnostics, checks: { ...diagnostics.checks, suggestion_analyzer: { status: 'not_applicable', code: null, message: null, action: null } } } };
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)));
  expect(await controlPlaneClient.quickTestContext('macos')).toEqual(body);
});
it.each([
  ['missing diagnostics', { ...context, diagnostics: undefined }],
  ['missing command', { ...context, diagnostics: { ...diagnostics, commands: { ...diagnostics.commands, prepare: undefined } } }],
  ['bad command status', { ...context, diagnostics: { ...diagnostics, commands: { ...diagnostics.commands, prepare: { ...diagnostics.commands.prepare, status: 'unknown' } } } }],
  ['bad prerequisite commands', { ...context, diagnostics: { ...diagnostics, prerequisites: [{ ...diagnostics.prerequisites[0], commands: [42] }] } }],
  ['bad raw detail', { ...context, diagnostics: { ...diagnostics, checks: { ...diagnostics.checks, runtime: { ...diagnostics.checks.runtime, message: {} } } } }],
  ['missing raw check', { ...context, diagnostics: { ...diagnostics, checks: { ...diagnostics.checks, dynamic_agent: undefined } } }],
  ['missing code', { ...context, diagnostics: { ...diagnostics, target: { ...diagnostics.target, code: undefined } } }],
  ['invalid checked time', { ...context, diagnostics: { ...diagnostics, checkedAt: 'not-a-date' } }],
  ['invented workspace', { ...context, diagnostics: { ...diagnostics, workspaceName: 'quick-test' } }],
  ['different platform', { ...context, diagnostics: { ...diagnostics, platformId: 'web' } }],
  ['inconsistent prepare verdict', { ...context, canStart: false }],
  ['different Android target', { ...context, platform: 'android', targetId: 'serial', diagnostics: quickDiagnostics('android', 'other') }],
  ['lost Android target in configuration failure', {
    ...context, platform: 'android', targetId: 'serial', canStart: false,
    diagnostics: {
      ...quickDiagnostics('android', null),
      commands: {
        ...quickDiagnostics('android', null).commands,
        prepare: { status: 'error', code: 'configuration_error', message: 'Unable to load configuration.', action: 'Repair configuration.' },
      },
    },
  }],
])('rejects %s without flattening or fallback', async (_name, body) => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body)));
  await expect(controlPlaneClient.quickTestContext('macos')).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});
it('accepts explicit legacy null without manufacturing diagnostics', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...context, diagnostics: null })));
  expect((await controlPlaneClient.quickTestContext('macos')).diagnostics).toBeNull();
});
