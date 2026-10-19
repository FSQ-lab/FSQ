import { useEffect, useRef, useState } from 'react';
import { ControlPlaneApp } from './ControlPlaneApp';
import { ControlPlaneShell } from './shell/ControlPlaneShell';
import { ConfigPage } from '../features/config/ConfigPage';
import { QuickTestPage } from '../features/quick-test/QuickTestPage';
import { parseRunRoute, runHash } from '../features/runs/runRoute';
import './entry.css';

type Entry = 'start' | 'quick-test' | 'case-manager';
function entryForHash(hash: string): Entry {
  if (parseRunRoute(hash) || hash === '#case-manager') return 'case-manager';
  if (hash === '#quick-test' || hash === '#quick-test/settings') return 'quick-test';
  return 'start';
}
function entryForLocation(): Entry {
  if (!window.location.hash && window.history.state?.cpEntry === 'case-manager') return 'case-manager';
  return entryForHash(window.location.hash);
}

export function EntryApp() {
  const [entry, setEntry] = useState<Entry>(entryForLocation);
  const [settings, setSettings] = useState(() => window.location.hash === '#quick-test/settings');
  const [visited, setVisited] = useState(() => new Set<Entry>([entry]));
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const managerGuard = useRef(() => true);
  const acceptedLocation = useRef({ href: window.location.href, state: window.history.state });
  const state = useRef({ entry, settings, dirty, pending, uncertain });
  state.current = { entry, settings, dirty, pending, uncertain };

  const canLeave = (destination: Entry, nextSettings: boolean) => {
    const current = state.current;
    if (current.entry === 'case-manager' && destination !== 'case-manager') return managerGuard.current();
    if (current.entry !== 'quick-test' || !current.settings || (destination === 'quick-test' && nextSettings)) return true;
    if (current.pending) return false;
    if (current.uncertain && !window.confirm('The save result is unknown. Leaving does not cancel it. Continue?')) return false;
    if (current.dirty && !window.confirm('Discard unsaved Provider changes?')) return false;
    return true;
  };
  const accept = (destination: Entry, nextSettings: boolean) => {
    setEntry(destination);
    setSettings(nextSettings);
    setVisited(previous => new Set([...previous, destination]));
    if (destination !== 'quick-test' || !nextSettings) { setDirty(false); setUncertain(false); }
    acceptedLocation.current = { href: window.location.href, state: window.history.state };
  };
  const navigate = (hash: string) => {
    const destination = entryForHash(hash);
    const nextSettings = hash === '#quick-test/settings';
    if (!canLeave(destination, nextSettings)) return;
    window.history.pushState({ cpEntry: destination }, '', hash);
    accept(destination, nextSettings);
  };
  const locationHandler = useRef(() => {});
  locationHandler.current = () => {
    if (window.location.href === acceptedLocation.current.href && window.history.state?.cpEntry === acceptedLocation.current.state?.cpEntry) return;
    const hash = window.location.hash;
    const destination = entryForLocation();
    // Only positively identified Manager history entries belong to its page router.
    if (state.current.entry === 'case-manager' && destination === 'case-manager') {
      acceptedLocation.current = { href: window.location.href, state: window.history.state };
      return;
    }
    const nextSettings = hash === '#quick-test/settings';
    if (!canLeave(destination, nextSettings)) {
      const previous = acceptedLocation.current;
      window.history.replaceState(previous.state, '', previous.href);
      return;
    }
    accept(destination, nextSettings);
  };
  useEffect(() => {
    window.history.replaceState({ ...window.history.state, cpEntry: state.current.entry }, '', window.location.href);
    acceptedLocation.current = { href: window.location.href, state: window.history.state };
    const handler = () => locationHandler.current();
    window.addEventListener('hashchange', handler);
    window.addEventListener('popstate', handler);
    return () => { window.removeEventListener('hashchange', handler); window.removeEventListener('popstate', handler); };
  }, []);
  return <>
    {entry === 'start' && <main className="entry-chooser"><header><h1>FSQ</h1><p>Choose how you want to test.</p></header><div className="entry-cards">
      <section className="entry-card"><h2>Quick Test</h2><p>Natural-language AI exploration without Workspace setup.</p><button className="button" onClick={() => navigate('#quick-test')}>Quick Test</button></section>
      <section className="entry-card"><h2>FSQ Case Manager</h2><p>Complete Workspace and Case management, exploration, strict replay, and Run inspection.</p><button className="button" onClick={() => navigate('#case-manager')}>FSQ Case Manager</button></section>
    </div></main>}
    {visited.has('case-manager') && <div hidden={entry !== 'case-manager'} inert={entry !== 'case-manager' ? true : undefined}>
      <ControlPlaneApp active={entry === 'case-manager'} entryGuard={managerGuard} onBackToStart={() => navigate('#start')} />
    </div>}
    {visited.has('quick-test') && <div hidden={entry !== 'quick-test'} inert={entry !== 'quick-test' ? true : undefined}>
      <ControlPlaneShell variant="quick-test" activePage={settings ? 'config' : 'quick-test'} title={settings ? 'Settings' : 'Quick Test'} description={settings ? 'Manage the active model provider.' : 'Natural-language exploration without Workspace setup.'}
        onNavigate={page => navigate(page === 'config' ? '#quick-test/settings' : '#quick-test')} onBackToStart={() => navigate('#start')} interactionLocked={pending}>
        <div className="quick-test-outlet" hidden={settings}>
          <QuickTestPage active={entry === 'quick-test' && !settings} onCaseManager={() => navigate('#case-manager')}
            onOpenReport={(workspace, platform, run) => navigate(runHash({ workspace, platform, run }))} />
        </div>
        {entry === 'quick-test' && settings && <ConfigPage onDirtyChange={setDirty} onSavePendingChange={setPending} onSaveUncertainChange={setUncertain} />}
      </ControlPlaneShell>
    </div>}
  </>;
}
