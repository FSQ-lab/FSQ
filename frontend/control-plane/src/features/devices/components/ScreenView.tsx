import { useEffect, useState } from 'react';
import { controlPlaneClient, toApiError } from '../../../api/controlPlaneClient';
import type { QuickTestPlatformId } from '../../../api/types';

export function ScreenView({ requestId, revision, platform, targetLabel }: { requestId: string | null; revision: number; platform: QuickTestPlatformId | null; targetLabel: string }) {
  const [state, setState] = useState<'empty' | 'loading' | 'available' | 'unavailable' | 'error'>('empty');
  const [url, setUrl] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  useEffect(() => {
    setUrl((previous) => { if (previous) URL.revokeObjectURL(previous); return null; });
    if (!requestId || revision <= 0) { setState('empty'); return; }
    const controller = new AbortController();
    setState('loading');
    void controlPlaneClient.screen(requestId, revision, controller.signal).then((blob) => {
      const objectUrl = URL.createObjectURL(blob);
      if (controller.signal.aborted) { URL.revokeObjectURL(objectUrl); return; }
      setUrl(objectUrl); setState('available');
    }).catch((error) => {
      if (controller.signal.aborted) return;
      setMessage(toApiError(error).message);
      setState(error instanceof Error && 'status' in error && error.status === 404 ? 'unavailable' : 'error');
    });
    return () => controller.abort();
  }, [requestId, revision]);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  if (state === 'available' && url) return <div className={`screen-canvas ${platform === 'android' ? 'screen-canvas--android' : ''}`}><img src={url} alt={`${platform ?? 'Selected platform'} screenshot evidence for ${targetLabel}, revision ${revision}`} /></div>;
  const copy = {
    empty: ['Screen not yet captured', 'Real screenshot evidence will appear here after capture.'],
    loading: ['Loading screen', 'Reading the latest screenshot revision…'],
    unavailable: ['Screen unavailable', message || 'No readable screenshot is available for this run.'],
    error: ['Screen failed to load', message || 'Retry when a newer revision is available.'],
  }[state as Exclude<typeof state, 'available'>];
  return <div className={`evidence-message evidence-message--${state}`} role={state === 'error' ? 'alert' : 'status'}><strong>{copy[0]}</strong><p>{copy[1]}</p></div>;
}
