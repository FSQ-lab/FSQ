import { useCallback, useEffect, useRef, useState } from 'react';
import { ControlPlaneApiError, controlPlaneClient, toApiError } from '../../api/controlPlaneClient';
import type { ApiErrorBody, BootstrapResponse, PlatformId, QuickTestContext } from '../../api/types';
import { useRunStream } from '../devices/hooks/useRunStream';

/** Preparation owns only context and submission. Execution remains in the shared run stream. */
export function useQuickTest(active: boolean) {
  const [bootstrap, setBootstrap] = useState<BootstrapResponse | null>(null);
  const [bootstrapError, setBootstrapError] = useState<ApiErrorBody | null>(null);
  const [platform, setPlatform] = useState<PlatformId | ''>('');
  const [targetId, setTargetId] = useState('');
  const androidSelectionCleared = useRef(false);
  const contextGeneration = useRef(0);
  const contextController = useRef<AbortController | null>(null);
  const [goal, setGoal] = useState('');
  const [context, setContext] = useState<QuickTestContext | null>(null);
  const [choices, setChoices] = useState<QuickTestContext['choices']>([]);
  const [contextError, setContextError] = useState<ApiErrorBody | null>(null);
  const [revision, setRevision] = useState(0);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState<ApiErrorBody | null>(null);
  const startLock = useRef(false);
  const requestRef = useRef(requestId);
  requestRef.current = requestId;
  const restoredRequest = useRef<string | null>(null);
  const bootstrapGeneration = useRef(0);
  const { snapshot: streamed, connection, error: streamError } = useRunStream(requestId);
  const snapshot = streamed?.requestId === requestId && streamed.workflow === 'quick-test' ? streamed : null;
  const identityError: ApiErrorBody | null = streamed && requestId && !snapshot ? {
    code: 'task_mismatch', message: 'The server snapshot does not match this Quick Test task.',
    action: 'Check the server task before starting another test.',
  } : null;
  const foreign = bootstrap?.activeTask && bootstrap.activeTask.workflow !== 'quick-test' && bootstrap.busy;
  const preparationFailed = snapshot?.terminal === true && snapshot.runId === null;
  const running = Boolean(requestId) && !preparationFailed;
  const locked = starting || uncertain || running;

  const reconcile = useCallback(async (explicit = false) => {
    const generation = ++bootstrapGeneration.current;
    try {
      const data = await controlPlaneClient.bootstrap();
      if (generation !== bootstrapGeneration.current) return;
      setBootstrap(data);
      setBootstrapError(null);
      if (data.activeTask?.workflow === 'quick-test' && data.activeTask.requestId !== requestRef.current) {
        // Android ownership begins before React commits the restored task.
        // Abort even a non-Android request that could still write this state.
        if (data.activeTask.platform === 'android') contextController.current?.abort();
        ++contextGeneration.current;
        restoredRequest.current = data.activeTask.requestId;
        setRequestId(data.activeTask.requestId);
        setPlatform(data.activeTask.platform);
        setTargetId(data.activeTask.platform === 'android' || data.activeTask.platform === 'web' ? data.activeTask.targetId ?? '' : '');
        setUncertain(false);
      } else if (explicit && !data.busy) {
        setUncertain(false);
      }
    } catch (caught) {
      if (generation === bootstrapGeneration.current) setBootstrapError(toApiError(caught));
    }
  }, []);
  useEffect(() => {
    if (active) void reconcile();
    return () => { ++bootstrapGeneration.current; };
  }, [active, reconcile]);
  useEffect(() => {
    if (snapshot && restoredRequest.current === snapshot.requestId) {
      setGoal(snapshot.source.goal ?? '');
      restoredRequest.current = null;
    }
  }, [snapshot]);

  useEffect(() => {
    if (!platform || locked || !active) return;
    const controller = new AbortController();
    contextController.current = controller;
    const generation = ++contextGeneration.current;
    const applies = () => !controller.signal.aborted
      && (platform !== 'android' || (generation === contextGeneration.current && !startLock.current));
    setContext(null);
    setContextError(null);
    void controlPlaneClient.quickTestContext(platform, targetId || undefined, controller.signal).then(data => {
      if (!applies()) return;
      const expectedTarget = platform === 'android' || platform === 'web' ? targetId || null : null;
      if (data.platform !== platform || data.targetId !== expectedTarget) {
        setContextError({ code: 'context_mismatch', message: 'Environment checks do not match the selected platform or target.', action: 'Recheck environment.' });
        return;
      }
      // Empty/blank identities cannot represent a selection. Reject the response
      // rather than normalize an identity or silently choose another candidate.
      if (platform === 'android' && data.choices.some(choice => !choice.id.trim())) {
        setContextError({ code: 'invalid_response', message: 'The Control Plane server returned an invalid response.', action: 'Recheck environment.' });
        return;
      }
      setChoices(data.choices);
      const deviceDiscoveryBlocked = data.platform === 'android' && data.diagnostics?.checks.configuration.status === 'error';
      if (platform === 'android') {
        // Discovery never replaces a selected identity, even if it is unavailable.
        // Explicit clearing lasts for this platform's mounted selection lifecycle.
        const eligible = data.choices.filter(choice => choice.selectable && choice.status === 'ready');
        if (!targetId && !androidSelectionCleared.current && !deviceDiscoveryBlocked && eligible.length === 1) {
          setTargetId(eligible[0].id);
          setContext(null); // The new identity still needs its own authoritative checks.
          return;
        }
      } else if (targetId && !data.choices.some(choice => choice.id === targetId && choice.selectable && choice.status === 'ready')) {
        setTargetId('');
      }
      setContext(data);
    }).catch(caught => { if (applies()) setContextError(toApiError(caught)); });
    return () => controller.abort();
  }, [platform, targetId, revision, locked, active]);
  const matchingContext = context?.platform === platform && context.targetId === (targetId || null);
  const targetNeeded = platform === 'android' || platform === 'web';
  const validTarget = !targetNeeded || Boolean(targetId && context?.choices.some(choice => choice.id === targetId && choice.selectable && choice.status === 'ready'));
  const blockedDeviceDiagnosis = context?.platform === 'android' && context.diagnostics?.checks.configuration.status === 'error' ? context.diagnostics : null;
  const deviceSelectionStatus = platform === 'android' && targetId && !locked
    ? !matchingContext || blockedDeviceDiagnosis ? 'not checked' : !validTarget ? 'unavailable' : null
    : null;
  const inputError = !goal.trim() ? 'Describe the application, actions, and expected result.'
    : goal.length > 16000 ? 'Use at most 16000 characters.'
      : /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(goal) ? 'Remove control characters from the prompt.' : null;
  const busy = bootstrap?.busy && !(snapshot?.terminal && bootstrap.activeTask?.requestId === snapshot.requestId);
  const blockedReason = locked ? (uncertain ? 'The start outcome is unknown. Recheck the server task before another start.' : 'A task is in progress.')
    : !bootstrap ? 'Checking server task state.' : bootstrapError ? bootstrapError.message
      : busy ? 'Another task is using this server.'
        : !platform || !bootstrap.platforms.some(item => item.id === platform) ? 'Select a platform.' : !matchingContext ? 'Checking environment.'
          : blockedDeviceDiagnosis ? blockedDeviceDiagnosis.commands.prepare.message
            : !validTarget ? `Select a ${platform === 'web' ? 'browser' : 'device'}.`
            : !context?.diagnostics ? 'Environment diagnostics are unavailable. Recheck environment.'
              : !context.canStart || context.diagnostics.commands.prepare.status !== 'ready' ? 'Resolve the environment checks before starting.' : inputError;
  const canStart = blockedReason === null;

  const start = async () => {
    if (startLock.current || !canStart || !platform) return;
    startLock.current = true;
    ++contextGeneration.current;
    setStarting(true);
    setError(null);
    setRequestId(null);
    try {
      const payload = platform === 'android' || platform === 'web' ? { platform, targetId, goal } : { platform, goal };
      const response = await controlPlaneClient.startQuickTest(payload);
      setRequestId(response.requestId);
    } catch (caught) {
      setError(toApiError(caught));
      // Only an explicit HTTP rejection proves this POST was not accepted.
      if (!(caught instanceof ControlPlaneApiError) || caught.status < 400 || caught.status >= 500 || caught.body.code === 'invalid_error_response') setUncertain(true);
      await reconcile();
    } finally {
      startLock.current = false;
      setStarting(false);
    }
  };
  const newTest = () => {
    if (starting || uncertain || (requestId && !snapshot?.terminal && streamError?.code !== 'run_ended')) return;
    ++contextGeneration.current;
    setRequestId(null);
    setError(null);
    setContext(null);
    setBootstrap(null);
    setRevision(value => value + 1);
    void reconcile(true);
  };
  const cancel = async () => {
    if (!requestId) return;
    try { await controlPlaneClient.cancelRun(requestId); }
    catch (caught) { setError(toApiError(caught)); }
  };
  return {
    bootstrap, platform, targetId, goal, context, choices, snapshot, connection, running, locked, starting, uncertain,
    checkingContext: Boolean(platform && !context && !contextError && !locked && active),
    deviceSelectionStatus,
    foreign, canStart, blockedReason, preparationFailed,
    error: identityError ?? error ?? snapshot?.error ?? bootstrapError ?? contextError ?? streamError,
    setGoal, setPlatform: (value: PlatformId | '') => {
      if (locked || (platform === 'android' && (startLock.current || value === platform))) return;
      if (value === 'android' && platform !== 'android') contextController.current?.abort();
      ++contextGeneration.current;
      androidSelectionCleared.current = false;
      setPlatform(value); setTargetId(''); setContext(null); setChoices([]);
    },
    setTargetId: (value: string) => {
      if (locked || (platform === 'android' && startLock.current)) return;
      if (platform === 'android') {
        androidSelectionCleared.current = value === '';
        if (value === targetId) return;
      }
      ++contextGeneration.current;
      setTargetId(value); setContext(null);
    },
    start, cancel, newTest, refresh: () => { ++contextGeneration.current; setContext(null); setRevision(value => value + 1); void reconcile(true); },
    ended: streamError?.code === 'run_ended',
  };
}
