import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrerequisiteList } from './PrerequisiteList';

it('renders pending context separately from failed and not-applicable prerequisite facts', () => {
  render(<PrerequisiteList items={[
    { identifier: 'application_installation', status: 'pending', message: 'Application pending', commands: [] },
    { identifier: 'bundle_identifier', status: 'not_applicable', message: 'No bundle identifier configured', commands: [] },
    { identifier: 'appium_connect_runtime', status: 'error', message: 'Managed runtime check failed', commands: [] },
  ]} />);
  expect(screen.getByText('Pending')).toBeVisible();
  expect(screen.getByText('Not applicable')).toBeVisible();
  expect(screen.getByText('Check failed')).toBeVisible();
  expect(screen.getByText('Application pending').closest('li')).toHaveClass('prerequisite-row--pending');
});

it('renders the Managed Appium runtime prerequisite label for appium_connect_runtime', () => {
  render(<PrerequisiteList items={[
    { identifier: 'appium_connect_runtime', status: 'unavailable', message: 'Managed runtime uninitialized', commands: [] },
  ]} />);
  expect(screen.getByText('Managed Appium runtime')).toBeVisible();
  expect(screen.getByText('Managed runtime uninitialized')).toBeVisible();
});

it('shows failures, distinguishes blocked checks, and collapses passed checks', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:copy}});
  render(<PrerequisiteList items={[
    {identifier:'appium_connect_runtime',status:'unavailable',message:'Managed runtime missing',action:'Initialize runtime',commands:['python -c "from appium_connect_agent.contracts import LocalSettings; from appium_connect_agent.runtime import InstallRequest, Runtime; Runtime(LocalSettings()).install(InstallRequest())"']},
    {identifier:'bundle_identifier',status:'not_applicable',message:'Path identity used',commands:[]},
    {identifier:'xcode_installation',status:'ready',message:'Full Xcode is installed',commands:[]},
  ]} />);
  expect(screen.getByText('Managed runtime missing')).toBeVisible();
  expect(screen.getByText('Path identity used')).toBeVisible();
  expect(screen.getByText('Full Xcode is installed')).not.toBeVisible();
  const cmd = 'python -c "from appium_connect_agent.contracts import LocalSettings; from appium_connect_agent.runtime import InstallRequest, Runtime; Runtime(LocalSettings()).install(InstallRequest())"';
  await userEvent.click(screen.getByRole('button',{name:`Copy command: ${cmd}`}));
  expect(copy).toHaveBeenCalledWith(cmd);
  const commandRow = screen.getByText(cmd).closest('.prerequisite-command')!;
  expect(within(commandRow as HTMLElement).getByRole('button')).toHaveAttribute('data-copy-state', 'copied');
});

it('keeps manual copying available on clipboard failure',async()=>{
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockRejectedValue(new Error('denied'))}});
  const cmd = 'python -c "from appium_connect_agent.contracts import LocalSettings; from appium_connect_agent.runtime import Runtime; Runtime(LocalSettings()).ensure_managed_driver(\'mac2\')"';
  render(<PrerequisiteList items={[{identifier:'appium_connect_runtime',status:'unavailable',message:'Missing',commands:[cmd]}]}/>);
  await userEvent.click(screen.getByRole('button',{name:`Copy command: ${cmd}`}));
  expect(screen.getByRole('status')).toHaveTextContent('Copy failed');
  expect(screen.getByText(cmd)).toBeVisible();
});

it('shows local pending feedback and restores the icon after two seconds', async () => {
  vi.useFakeTimers();
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockReturnValue(pending) } });
  const cmd = 'python -c "from appium_connect_agent.contracts import LocalSettings; from appium_connect_agent.runtime import Runtime; Runtime(LocalSettings()).ensure_managed_driver(\'mac2\')"';
  const view = render(<PrerequisiteList items={[{ identifier: 'appium_connect_runtime', status: 'unavailable', message: 'Missing', commands: [cmd] }]} />);
  try {
    const button = screen.getByRole('button', { name: `Copy command: ${cmd}` });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Copying…');
    await act(async () => { finish(); await pending; });
    expect(button).toBeEnabled();
    expect(screen.getByRole('status')).toHaveTextContent('Copied!');
    act(() => { vi.advanceTimersByTime(2000); });
    expect(button).toHaveAttribute('data-copy-state', 'idle');
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  } finally { view.unmount(); vi.useRealTimers(); }
});

it('keeps feedback scoped to each command when clipboard requests resolve out of order', async () => {
  const resolve: (() => void)[] = [];
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn(() => new Promise<void>(done => resolve.push(done))) } });
  const view = render(<PrerequisiteList items={[{ identifier: 'appium_connect_runtime', status: 'unavailable', message: 'Missing', commands: ['python -c "Runtime(LocalSettings()).cleanup_installation()"', 'python -c "Runtime(LocalSettings()).install(InstallRequest())"'] }]} />);
  const buttons = screen.getAllByRole('button');
  fireEvent.click(buttons[0]);
  fireEvent.click(buttons[1]);
  await act(async () => resolve[1]());
  expect(buttons[0]).toHaveAttribute('data-copy-state', 'copying');
  expect(buttons[1]).toHaveAttribute('data-copy-state', 'copied');
  view.unmount();
  await act(async () => resolve[0]());
});
