import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useState, type MutableRefObject } from 'react';
import { EntryApp } from './EntryApp';
import { controlPlaneClient } from '../api/controlPlaneClient';

vi.mock('./ControlPlaneApp', () => ({ ControlPlaneApp: ({ entryGuard, onBackToStart }: { entryGuard: MutableRefObject<() => boolean>; onBackToStart: () => void }) => {
  const [workspace, setWorkspace] = useState('');
  const [pending, setPending] = useState(false);
  entryGuard.current = () => !pending;
  return <div>Manager content<input aria-label="Manager workspace" value={workspace} onChange={event => setWorkspace(event.target.value)} /><button onClick={() => setPending(value => !value)}>Toggle manager pending</button><button onClick={onBackToStart}>Manager back</button></div>;
} }));
vi.mock('../features/config/ConfigPage', () => ({ ConfigPage: ({ onDirtyChange, onSavePendingChange, onSaveUncertainChange }: { onDirtyChange: (dirty: boolean) => void; onSavePendingChange: (pending: boolean) => void; onSaveUncertainChange: (uncertain: boolean) => void }) =>
  <section aria-label="Shared ConfigPage"><button onClick={() => onDirtyChange(true)}>Edit Provider</button><button onClick={() => onSavePendingChange(true)}>Save pending</button><button onClick={() => { onSavePendingChange(false); onSaveUncertainChange(true); }}>Save unknown</button></section>,
}));
beforeEach(() => {
  window.history.replaceState(null, '', '/');
  vi.spyOn(controlPlaneClient, 'bootstrap').mockResolvedValue({ apiVersion: '1', platforms: [], busy: false, activeTask: null });
});
afterEach(() => vi.restoreAllMocks());
it('offers equal entries then exact Quick navigation with a quiet return link', async () => {
  render(<EntryApp />);
  expect(screen.getByRole('button', { name: 'Quick Test' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'FSQ Case Manager' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Quick Test' }));
  const sidebar = screen.getByRole('complementary', { name: 'Control Plane sidebar' });
  expect(within(sidebar).getByRole('button', { name: 'Quick Test' })).toBeInTheDocument();
  expect(within(sidebar).getByRole('button', { name: 'Settings' })).toBeInTheDocument();
  expect(within(sidebar).queryByText('Workspaces')).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Back to start' })).toBeInTheDocument();
});
it('routes old #runs links directly to Case Manager', () => {
  window.history.replaceState(null, '', '/#runs');
  render(<EntryApp />);
  expect(screen.getByText('Manager content')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Quick Test' })).not.toBeInTheDocument();
});
it('keeps the idle prompt mounted through shared Settings and honors dirty/pending/unknown guards', async () => {
  render(<EntryApp />);
  fireEvent.click(screen.getByRole('button', { name: 'Quick Test' }));
  fireEvent.change(screen.getByLabelText('What do you want to test?'), { target: { value: 'Check Notes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByRole('region', { name: 'Shared ConfigPage' })).toBeVisible();
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('button', { name: 'Edit Provider' }));
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  fireEvent.click(screen.getByRole('link', { name: 'Back to start' }));
  expect(window.location.hash).toBe('#quick-test/settings');
  expect(confirm).toHaveBeenCalledWith('Discard unsaved Provider changes?');
  confirm.mockClear();
  fireEvent.click(screen.getByRole('button', { name: 'Save pending' }));
  fireEvent.click(screen.getByRole('link', { name: 'Back to start' }));
  expect(confirm).not.toHaveBeenCalled();
  act(() => { window.history.pushState(null, '', '#start'); window.dispatchEvent(new PopStateEvent('popstate')); });
  expect(window.location.hash).toBe('#quick-test/settings');
  fireEvent.click(screen.getByRole('button', { name: 'Save unknown' }));
  fireEvent.click(screen.getByRole('button', { name: 'Quick Test' }));
  expect(confirm).toHaveBeenCalledWith('The save result is unknown. Leaving does not cancel it. Continue?');
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button', { name: 'Quick Test' }));
  expect(screen.getByLabelText('What do you want to test?')).toHaveValue('Check Notes');
  expect(screen.queryByRole('region', { name: 'Shared ConfigPage' })).not.toBeInTheDocument();
});
it('keeps independent Manager state mounted across entry switches and respects its pending guard', async () => {
  render(<EntryApp />);
  fireEvent.click(screen.getByRole('button', { name: 'FSQ Case Manager' }));
  fireEvent.change(screen.getByLabelText('Manager workspace'), { target: { value: 'my-workspace' } });
  fireEvent.click(screen.getByRole('button', { name: 'Toggle manager pending' }));
  fireEvent.click(screen.getByRole('button', { name: 'Manager back' }));
  expect(screen.getByText('Manager content')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Toggle manager pending' }));
  fireEvent.click(screen.getByRole('button', { name: 'Manager back' }));
  fireEvent.click(screen.getByRole('button', { name: 'Quick Test' }));
  fireEvent.click(screen.getByRole('link', { name: 'Back to start' }));
  fireEvent.click(screen.getByRole('button', { name: 'FSQ Case Manager' }));
  await waitFor(() => expect(screen.getByLabelText('Manager workspace')).toHaveValue('my-workspace'));
});
