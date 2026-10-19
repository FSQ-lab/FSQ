import { useRef, useState } from 'react';
import type { EvidenceTab, PlatformId } from '../../api/types';
import { LiveEvidencePanel } from '../devices/components/LiveEvidencePanel';
import { RunTimeline } from '../devices/components/RunTimeline';
import { PreflightStatus } from '../devices/components/PreflightStatus';
import { useQuickTest } from './useQuickTest';
import './quick-test.css';

interface QuickTestPageProps {
  active: boolean;
  onCaseManager: () => void;
  onOpenReport: (workspace: string, platform: PlatformId, runId: string) => void;
}
export function QuickTestPage({ active, onCaseManager, onOpenReport }: QuickTestPageProps) {
  const test = useQuickTest(active);
  const [tab, setTab] = useState<EvidenceTab>('screen');
  const [step, setStep] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const prompt = useRef<HTMLTextAreaElement>(null);
  const snapshot = test.snapshot;
  const showWorkbench = test.starting || test.running;
  const status = snapshot?.status ?? (showWorkbench ? 'preparing' : null);
  const statusLabel = status === 'preparing' ? 'Preparing' : status === 'running' ? 'Running' : status ?? 'Awaiting server snapshot';
  const connectionNotice = snapshot?.terminal ? null
    : test.connection === 'reconnecting' ? 'Live updates were interrupted. Reconnecting…'
      : test.connection === 'polling' ? 'Live updates are unavailable. Checking test status periodically.' : null;
  const newTest = () => {
    test.newTest();
    setStep(null);
    setTab('screen');
    requestAnimationFrame(() => prompt.current?.focus());
  };
  return <div className={`devices-page quick-test-page${showWorkbench ? ' quick-test-page--workbench' : ''}`}>
    <header className="devices-intro"><h1>Quick Test</h1><p>Describe what to test, the actions to take, and the expected result.</p></header>
    {test.foreign && <div className="page-notice" role="status"><p>FSQ Case Manager has an active task. Wait for it to finish.</p><button className="button" onClick={onCaseManager}>Open FSQ Case Manager</button></div>}
    {test.error && <div className="page-notice" role="alert" data-error-code={test.error.code}><strong>{test.error.message}</strong><span>{test.error.action}</span></div>}
    {test.preparationFailed && !snapshot?.error && <p role="status">{snapshot?.status}: {snapshot?.summary}</p>}
    {test.uncertain && <p role="status">The start outcome is unknown. Recheck the server task before another start. No request will be retried automatically.</p>}
    {showWorkbench && connectionNotice && <p className="page-notice" role="status">{connectionNotice}</p>}
    {!showWorkbench && <div className="operation-composer quick-test-setup">
      <section className="operation-card" aria-label="Quick Test preparation">
        <div className="quick-test-fields">
          <label htmlFor="quick-platform">Platform</label>
          <select id="quick-platform" value={test.platform} disabled={test.locked} onChange={event => test.setPlatform(event.target.value as PlatformId | '')}>
            <option value="">Select a platform</option>
            {test.bootstrap?.platforms.map(platform => <option key={platform.id} value={platform.id}>{platform.label}</option>)}
          </select>
          {(test.platform === 'android' || test.platform === 'web') && <>
            <label htmlFor="quick-target">{test.platform === 'web' ? 'Browser' : 'Device'}</label>
            <select id="quick-target" value={test.targetId} disabled={test.locked} onChange={event => test.setTargetId(event.target.value)}>
              <option value="">Select a {test.platform === 'web' ? 'browser' : 'device'}</option>
              {(test.locked || test.platform === 'android') && test.targetId && !test.choices.some(choice => choice.id === test.targetId) && <option value={test.targetId} disabled={Boolean(test.deviceSelectionStatus)}>{test.targetId}{test.deviceSelectionStatus ? ` — ${test.deviceSelectionStatus}` : ''}</option>}
              {test.choices.map(choice => <option key={choice.id} value={choice.id} disabled={!choice.selectable || choice.status !== 'ready' || (choice.id === test.targetId && Boolean(test.deviceSelectionStatus))}>{choice.label}{choice.id === test.targetId && test.deviceSelectionStatus ? ` — ${test.deviceSelectionStatus}` : ''}</option>)}
            </select>
          </>}
          <label htmlFor="quick-goal">What do you want to test?</label>
          <p id="quick-goal-help">Name {test.platform === 'web' ? 'the site or search subject' : 'the app'}, describe what to do, and explain what should happen.{test.platform === 'web' && ' For Web, provide a URL or describe a clear search goal.'} You can include technical identifiers here.</p>
          <textarea ref={prompt} id="quick-goal" aria-describedby="quick-goal-help" rows={7} value={test.goal} disabled={test.locked} onChange={event => test.setGoal(event.target.value)} />
          {!test.running && <><button className="button button--primary" disabled={!test.canStart} onClick={() => void test.start()}>{test.starting ? 'Starting Quick Test…' : 'Start Quick Test'}</button>{test.blockedReason && <p role="status">{test.blockedReason}</p>}</>}
        </div>
      </section>
      <aside className="composer-environment" aria-label="Environment checks">
        <PreflightStatus mode="explore" pendingContext={{ platform: test.platform, diagnostics: test.context?.diagnostics ?? null }}
          loading={test.checkingContext} locked={test.starting || (test.running && !test.ended)} onRecheck={test.refresh} />
      </aside>
    </div>}
    {showWorkbench && <div className="run-context-bar"><div aria-label="Run context"><span className={`status-badge status-badge--${status ?? 'preparing'}`}>{statusLabel}</span>{snapshot?.application && <strong>{snapshot.application}</strong>}{snapshot && <><span>{snapshot.platform}</span><span role="status">{snapshot.summary}</span></>}</div>
      {snapshot?.terminal && snapshot.workspaceName && snapshot.runId && <button className="button" onClick={() => onOpenReport(snapshot.workspaceName!, snapshot.platform, snapshot.runId!)}>Open report</button>}
    </div>}
    {showWorkbench && <div className="devices-workbench">
      <section className="operation-card"><header className="card-header"><h2>Activity</h2></header><div className="operation-body operation-body--run">
        <RunTimeline variant="quick-test" snapshot={snapshot} pendingGoal={test.goal} connection={test.connection} selectedStepId={step} resultHeadingRef={heading} onSelectStep={setStep} onCancel={() => void test.cancel()} onNewRun={newTest} />
      </div></section>
      <LiveEvidencePanel tab={tab} snapshot={snapshot} selectedStepId={step} platform={snapshot?.platform ?? (test.platform || null)} targetLabel={snapshot?.application ?? 'Application identity unavailable'} onTabChange={setTab} onClearStep={() => setStep(null)} />
    </div>}
    {test.ended && <button className="button" onClick={newTest}>New test</button>}
  </div>;
}
