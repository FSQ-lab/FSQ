import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import capturedBody from '../../test/quick-test-android-config-error.json?raw';
import { quickDiagnostics } from '../../test/quickTestFixtures';
import { controlPlaneClient } from '../../api/controlPlaneClient';
import { QuickTestPage } from './QuickTestPage';

afterEach(() => vi.restoreAllMocks());

// Byte-identical actual HTTP 200 capture from Application/Doctor/HTTP projection
// with only settings-load failure injected.
// Verified against the supplied capture with cmp/shasum:
// a0031f4e6651e1797a7225808d41e097bb47e71f517b3f3ca8361f1ecd231bbb
it('retains the captured HTTP body byte length', () => {
  expect(new TextEncoder().encode(capturedBody).byteLength).toBe(4125);
});

it('decodes the actual selected-device failure and displays it through shared Preflight without losing scope', async () => {
  const serial = 'selected-device';
  const goal = 'Open the application and verify its welcome page';
  const contextRequests: (string | null)[] = [];
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    if (init?.method && init.method !== 'GET') throw new Error('Unexpected mutation');
    const url = new URL(input instanceof Request ? input.url : String(input), 'http://localhost');
    if (url.pathname === '/api/control-plane/bootstrap') {
      return new Response(JSON.stringify({ apiVersion: '1.0', platforms: [{ id: 'android', label: 'Android' }], busy: false, activeTask: null }));
    }
    if (url.pathname !== '/api/control-plane/quick-test/context') throw new Error(`Unexpected request: ${url.pathname}`);
    expect(url.searchParams.get('platform')).toBe('android');
    const target = url.searchParams.get('targetId');
    contextRequests.push(target);
    if (target === serial) return new Response(capturedBody, { status: 200 });
    expect(target).toBeNull();
    // Controlled initial discovery only; the selected-device failure response
    // below is the unmodified real HTTP capture, not a reconstructed projection.
    return new Response(JSON.stringify({
      platform: 'android', targetId: null, canStart: true,
      choices: [
        { id: serial, label: 'Selected phone', selectable: true, status: 'ready' },
        { id: 'other-device', label: 'Other phone', selectable: true, status: 'ready' },
      ],
      checks: [], diagnostics: quickDiagnostics('android', null),
    }));
  });
  const start = vi.spyOn(controlPlaneClient, 'startQuickTest');
  const user = userEvent.setup();
  render(<QuickTestPage active onCaseManager={vi.fn()} onOpenReport={vi.fn()} />);
  await screen.findByRole('option', { name: 'Android' });
  await user.selectOptions(screen.getByRole('combobox', { name: 'Platform' }), 'android');
  await screen.findByRole('option', { name: 'Selected phone' });
  await user.type(screen.getByRole('textbox', { name: 'What do you want to test?' }), goal);
  await user.selectOptions(screen.getByRole('combobox', { name: 'Device' }), serial);
  await waitFor(() => expect(contextRequests).toContain(serial));

  const preflight = screen.getByRole('region', { name: 'Android environment' });
  await waitFor(() => {
    expect(within(preflight).getByRole('status')).toHaveTextContent('Platform configuration could not be loaded.');
    expect(screen.getByRole('combobox', { name: 'Device' })).toHaveValue(serial);
  });
  expect(preflight.closest('.composer-environment')).not.toBeNull();
  expect(screen.getByRole('option', { name: `${serial} — not checked` })).toBeDisabled();
  expect(preflight).toHaveTextContent('Check the saved Provider and platform installation.');
  expect(screen.queryByText('The Control Plane server returned an invalid response.')).not.toBeInTheDocument();
  expect(screen.queryByText('Environment checks do not match the selected platform or target.')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeDisabled();
  expect(screen.getByRole('textbox', { name: 'What do you want to test?' })).toHaveValue(goal);
  const recheck = within(preflight).getByRole('button', { name: 'Recheck environment' });
  expect(recheck).toBeEnabled();
  await user.click(recheck);
  await waitFor(() => expect(contextRequests).toEqual([null, serial, serial]));
  await waitFor(() => expect(recheck).toBeEnabled());
  expect(screen.getByRole('combobox', { name: 'Device' })).toHaveValue(serial);
  expect(screen.getByRole('button', { name: 'Start Quick Test' })).toBeDisabled();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Edit target configuration' })).not.toBeInTheDocument();
  expect(start).not.toHaveBeenCalled();
  expect(fetch.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
});
