import { Check, Ellipsis, Minus, TriangleAlert } from 'lucide-react';
import { useId } from 'react';
import type { DiagnosticDetail, PlatformId, QuickDiagnostics, ReadinessRecord, ReadinessResponse, RunMode } from '../../../api/types';
import { PrerequisiteList } from './PrerequisiteList';
import ReactMarkdown from 'react-markdown';
import prerequisiteGuide from '../../../../../../docs/platform-prerequisites.md?raw';
import androidGuide from '../../../../../../docs/android-prerequisites.md?raw';

interface PreflightStatusProps {
  mode: RunMode;
  workspace?: ReadinessRecord;
  provider?: ReadinessRecord;
  target?: ReadinessRecord;
  strict?: ReadinessRecord;
  requiresProvider?: boolean;
  loading: boolean;
  diagnostics?: ReadinessResponse | null;
  macos?: boolean;
  android?: boolean;
  onRecheck?: () => void;
  onRepair?: () => void;
  locked?: boolean;
  pendingContext?: { platform: PlatformId | ''; diagnostics: QuickDiagnostics | null };
}

function PreflightItem({ label, record, loading }: { label: string; record?: ReadinessRecord | DiagnosticDetail; loading: boolean }) {
  const status = loading ? 'loading' : record?.status ?? 'unavailable';
  const detail = loading ? 'Checking readiness…' : record?.message ?? 'Readiness is unavailable.';
  return <li className={`preflight-item preflight-item--${status}`}>
    <span className="preflight-icon" aria-hidden="true">{status === 'ready' ? <Check/> : status === 'loading' || status === 'pending' ? <Ellipsis/> : status === 'not_applicable' ? <Minus/> : <TriangleAlert/>}</span>
    <span><strong>{label}</strong><small>{detail}</small>{record?.action && status !== 'ready' && <small className="preflight-action">{record.action}</small>}</span>
  </li>;
}

export function PreflightStatus({ mode, workspace, provider, target, strict, requiresProvider, loading, diagnostics, macos, android, onRecheck, onRepair, locked, pendingContext }: PreflightStatusProps) {
  const titleId = useId();
  if (macos || android || pendingContext) {
    const labels: Record<PlatformId, string> = { android: 'Android', macos: 'macOS', web: 'Web', windows: 'Windows' };
    const selectedPlatform = pendingContext ? pendingContext.platform : android ? 'android' : 'macos';
    const label = selectedPlatform ? labels[selectedPlatform] : '';
    const current = pendingContext ? pendingContext.diagnostics : diagnostics;
    const currentProvider = pendingContext ? current?.provider : provider;
    const verdict = mode === 'explore' ? current?.commands?.caseCreate : current?.commands?.caseTest;
    const prepare = pendingContext?.diagnostics?.commands.prepare;
    const ready = verdict?.status === 'ready' && (!requiresProvider || currentProvider?.status === 'ready') && (!pendingContext || prepare?.status === 'ready');
    // The shared Doctor's prepare verdict governs identification, not executable Case readiness.
    const pending = Boolean(pendingContext && ((prepare?.status === 'ready' && verdict?.status === 'pending') || prepare?.status === 'pending'));
    const message = pendingContext ? (pending ? verdict?.message : prepare?.message)
      : requiresProvider && currentProvider?.status !== 'ready' ? currentProvider?.message : verdict?.message;
    const guide = selectedPlatform === 'android' ? androidGuide : prerequisiteGuide.split(`## ${label}\n`)[1]?.split('\n## ')[0];
    return <section className="preflight preflight--macos" aria-labelledby={titleId} aria-busy={loading}>
      <div className="preflight-heading"><h3 id={titleId}>{label ? `${label} environment` : 'Preflight'}</h3><button type="button" className="button" disabled={loading||locked||!onRecheck} onClick={onRecheck}>{loading?'Checking…':'Recheck environment'}</button></div>
      <div className={`preflight-summary${ready && !loading ? ' preflight-summary--ready' : pending ? ' preflight-summary--pending' : ''}`} role="status">
        <strong>{loading ? `Checking ${label} environment…` : !selectedPlatform ? 'Select a platform' : !current ? 'Environment check unavailable' : pending ? 'Awaiting application identification' : ready ? `${label} is ready` : `${label} needs attention`}</strong>
        <p>{loading ? 'Start is unavailable until checks complete.' : !selectedPlatform ? 'Select a platform to check its environment.' : !current ? 'Recheck the environment to obtain current results.' : ready ? 'Preflight checks passed. Readiness is checked again before starting.' : message || 'Review the failed checks below.'}</p>
        {pendingContext && prepare?.status !== 'ready' && prepare?.action && <p>{prepare.action}</p>}
      </div>
      {!loading && current?.prerequisites && <PrerequisiteList key={current.checkedAt} items={current.prerequisites}/>}
      {!loading && current && <ul>
        {pendingContext && <PreflightItem label="Target" record={current.target} loading={false}/>}
        <PreflightItem label="Workspace" record={pendingContext ? current.workspace : workspace} loading={false}/>
        <PreflightItem label={mode==='explore'||requiresProvider?'Provider':'Strict runner'} record={mode==='explore'||requiresProvider?currentProvider:strict} loading={false}/>
      </ul>}
      {onRepair && <button className="button prerequisite-repair" type="button" disabled={locked||loading} onClick={onRepair}>Edit target configuration</button>}
      {guide && <details className="prerequisite-guide"><summary>{label} installation and troubleshooting</summary><ReactMarkdown>{guide}</ReactMarkdown></details>}
      {current?.checkedAt && !loading && <p className="field-help">Checked at {new Date(current.checkedAt).toLocaleTimeString()}</p>}
      {selectedPlatform && <p className="field-help">{selectedPlatform === 'android' ? 'Checks do not start ADB, initialize device automation or grant permissions. Ready does not guarantee Case success.' : selectedPlatform === 'macos' ? 'Checks do not grant macOS Accessibility or Automation permissions.' : 'Checks do not launch the application. Readiness does not guarantee Case success.'}</p>}
    </section>;
  }
  return <section className="preflight" aria-labelledby={titleId}>
    <h3 id={titleId}>Preflight</h3>
    <ul>
      <PreflightItem label="Target" record={target} loading={loading} />
      <PreflightItem label="Workspace" record={workspace} loading={loading} />
      <PreflightItem label={mode === 'explore' || requiresProvider ? 'Provider' : 'Strict runner'} record={mode === 'explore' || requiresProvider ? provider : strict} loading={loading} />
    </ul>
  </section>;
}
