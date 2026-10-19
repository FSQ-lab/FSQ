import type { DiagnosticDetail, PlatformId, QuickDiagnostics } from '../api/types';

export function quickDiagnostics(platform: PlatformId = 'macos', targetId: string | null = null): QuickDiagnostics {
  const ready: DiagnosticDetail = { status: 'ready', code: null, message: 'Ready', action: '' };
  const pending: DiagnosticDetail = { status: 'pending', code: 'context.pending', message: 'Application identification is pending.', action: 'Describe the application and goal, then start Quick Test.' };
  const notApplicable: DiagnosticDetail = { status: 'not_applicable', code: null, message: 'Not needed for this context.', action: '' };
  return {
    workspaceName: null, platformId: platform, workspace: pending, platform: pending, provider: ready, target: platform === 'web' ? ready : pending, strict: pending,
    prerequisites: platform === 'android' || platform === 'macos' ? [
      { identifier: platform === 'android' ? 'adb_cli' : 'appium_cli', ...ready, commands: [] },
      { identifier: 'application_installation', ...pending, commands: [] },
      { identifier: 'suggestion_analyzer', ...notApplicable, commands: [] },
    ] : [],
    commands: { prepare: ready, caseCreate: pending, caseTest: pending, caseTestSuggest: pending },
    checks: {
      configuration: pending, runtime: ready, target_configuration: pending, target_availability: pending,
      strict_core: pending, provider: ready, suggestion_analyzer: notApplicable, dynamic_agent: pending,
    },
    status: 'pending', checkedAt: '2026-10-17T15:40:00Z',
    ...(platform === 'android' ? { targetId } : {}),
  };
}
