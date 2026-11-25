import { useCallback, useEffect, useRef, useState } from 'react';
import { ControlPlaneApiError, controlPlaneClient, toApiError } from '../../api/controlPlaneClient';
import type { ApiErrorBody, BootstrapResponse, QuickTestContext, QuickTestPlatformId } from '../../api/types';
import { useRunStream } from '../devices/hooks/useRunStream';

function needsTarget(platform: QuickTestPlatformId | ''): platform is 'android' | 'ios' | 'web' {
  return platform === 'android' || platform === 'ios' || platform === 'web';
}

/** Preparation owns only context and submission. Execution remains in the shared run stream. */
export function useQuickTest(active: boolean) {
  const [bootstrap, setBootstrap] = useState<BootstrapResponse | null>(null);
  const [bootstrapError, setBootstrapError] = useState<ApiErrorBody | null>(null);
  const [platform, setPlatform] = useState<QuickTestPlatformId | ''>('');
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
  const submitted = useRef<{ requestId: string; goal: string } | null>(null);
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
  const platforms = bootstrap?.quickTestPlatforms ?? bootstrap?.platforms;
  const targetNeeded = needsTarget(platform);
  const hasDeviceSelection = platform === 'android' || platform === 'ios';

  const reconcile = useCallback(async (explicit = false) => {
    const generation = ++bootstrapGeneration.current;
    try {
      const data = await controlPlaneClient.bootstrap();
      if (generation !== bootstrapGeneration.current) return;
      setBootstrap(data);
      setBootstrapError(null);
      if (data.activeTask?.workflow === 'quick-test' && data.activeTask.requestId !== requestRef.current) {
        // Mobile device selection owns its scope before React commits the restored task.
        if (data.activeTask.platform === 'android' || data.activeTask.platform === 'ios') contextController.current?.abort();
        ++contextGeneration.current;
        restoredRequest.current = data.activeTask.requestId;
        setRequestId(data.activeTask.requestId);
        setPlatform(data.activeTask.platform);
        setTargetId(needsTarget(data.activeTask.platform) ? data.activeTask.targetId ?? '' : '');
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
      if (snapshot.source.goal !== undefined) setGoal(snapshot.source.goal);
      if (snapshot.source.goal !== undefined || snapshot.terminal) restoredRequest.current = null;
    }
  }, [snapshot]);

  useEffect(() => {
    if (!platform || locked || !active) return;
    const controller = new AbortController();
    contextController.current = controller;
    const generation = ++contextGeneration.current;
    const applies = () => !controller.signal.aborted && (!hasDeviceSelection || (generation === contextGeneration.current && !startLock.current));
    setContext(null);
    setContextError(null);
    void controlPlaneClient.quickTestContext(platform, targetId || undefined, controller.signal).then(data => {
      if (!applies()) return;
      const expectedTarget = targetNeeded ? targetId || null : null;
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
      } else if (platform !== 'ios' && targetId && !data.choices.some(choice => choice.id === targetId && choice.selectable && choice.status === 'ready')) {
        setTargetId('');
      }
      setContext(data);
    }).catch(caught => { if (applies()) setContextError(toApiError(caught)); });
    return () => controller.abort();
  }, [platform, targetId, targetNeeded, hasDeviceSelection, revision, locked, active]);
  const matchingContext = context?.platform === platform && context.targetId === (targetId || null);
  const validTarget = !targetNeeded || Boolean(targetId && context?.choices.some(choice => choice.id === targetId && choice.selectable && choice.status === 'ready'));
  const blockedDeviceDiagnosis = context?.platform === 'android' && context.diagnostics?.checks.configuration.status === 'error' ? context.diagnostics : null;
  const deviceSelectionStatus = hasDeviceSelection && targetId && !locked
    ? !matchingContext || blockedDeviceDiagnosis ? 'not checked' : !validTarget ? 'unavailable' : null
    : null;
  const inputError = !goal.trim() ? 'Describe the application, actions, and expected result.'
    : goal.length > 16000 ? 'Use at most 16000 characters.'
      : /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(goal) ? 'Remove control characters from the prompt.' : null;
  const busy = bootstrap?.busy && !(snapshot?.terminal && bootstrap.activeTask?.requestId === snapshot.requestId);
  const blockedReason = locked ? (uncertain ? 'The start outcome is unknown. Recheck the server task before another start.' : 'A task is in progress.')
    : !bootstrap ? 'Checking server task state.' : bootstrapError ? bootstrapError.message
      : busy ? 'Another task is using this server.'
        : !platform || !platforms?.some(item => item.id === platform) ? 'Select a platform.' : !matchingContext ? 'Checking environment.'
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
    submitted.current = null;
    setRequestId(null);
    try {
      const payload = needsTarget(platform) ? { platform, targetId, goal } : { platform, goal };
      const response = await controlPlaneClient.startQuickTest(payload);
      submitted.current = { requestId: response.requestId, goal: payload.goal };
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
    submitted.current = null;
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
  const pendingGoal = starting && requestId === null ? goal
    : (!snapshot || snapshot.status === 'preparing') && submitted.current?.requestId === requestId ? submitted.current?.goal : undefined;
  return {
    bootstrap, platforms, platform, targetId, goal, context, choices, snapshot, connection, running, locked, starting, uncertain,
    pendingGoal,
    checkingContext: Boolean(platform && !context && !contextError && !locked && active),
    deviceSelectionStatus, targetNeeded, hasDeviceSelection,
    foreign, canStart, blockedReason, preparationFailed,
    error: identityError ?? error ?? snapshot?.error ?? bootstrapError ?? contextError ?? streamError,
    setGoal, setPlatform: (value: QuickTestPlatformId | '') => {
      if (locked || (hasDeviceSelection && (startLock.current || value === platform))) return;
      if (needsTarget(value) && value !== 'web' && value !== platform) contextController.current?.abort();
      ++contextGeneration.current;
      androidSelectionCleared.current = false;
      setPlatform(value); setTargetId(''); setContext(null); setChoices([]);
    },
    setTargetId: (value: string) => {
      if (locked || (hasDeviceSelection && startLock.current)) return;
      if (platform === 'android') androidSelectionCleared.current = value === '';
      if (hasDeviceSelection && value === targetId) return;
      ++contextGeneration.current;
      setTargetId(value); setContext(null);
    },
    start, cancel, newTest, refresh: () => { ++contextGeneration.current; setContext(null); setRevision(value => value + 1); void reconcile(true); },
    ended: streamError?.code === 'run_ended',
  };
}
