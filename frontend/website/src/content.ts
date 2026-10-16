import logoUrl from '../../../docs/assets/logo-light.svg';
import mediaPosterUrl from '../../../docs/media/fsq-v0.1.0-demo-thumbnail.png';
import mediaSourceUrl from '../../../docs/media/fsq-control-plane-demo.gif';

const repository = 'https://github.com/FSQ-lab/FSQ';
const repositoryFile = `${repository}/blob/main`;

export type WebsiteMedia = Readonly<{
  poster: string;
  source: string;
  destination: string;
  alt: string;
  caption: string;
  provenanceLabel: string;
  provenanceUrl: string;
  width: number;
  height: number;
}>;

export const websiteContent = {
  canonicalUrl: 'https://fsq-lab.github.io/FSQ/',
  logo: {
    source: logoUrl,
    alt: 'FSQ — Fully Self Quality',
  },
  navigation: {
    label: 'Main navigation',
    home: 'FSQ website home',
    workflow: 'How it works',
    setup: 'Get started',
    repository: 'GitHub repository',
  },
  skipLink: 'Skip to content',
  hero: {
    eyebrow: 'Open source · Alpha · Human in control',
    title: 'Describe a goal. Test the real app.',
    description:
      'FSQ is open-source alpha software for goal-driven testing of real user interfaces. Inspect the evidence, review any returned Case, and decide separately whether to replay it.',
    setupAction: 'Choose a setup path',
    mediaAction: 'See the demonstration',
  },
  workflow: {
    title: 'From goal to evidence.',
    introduction:
      'FSQ explores the real app with your configured Provider while you keep authority over the target, boundaries, and what happens next.',
    steps: [
      {
        title: 'Describe the outcome.',
        body: 'Choose the platform and exact target. State the goal, observable success criteria, allowed actions, and forbidden actions.',
      },
      {
        title: 'Inspect what happened.',
        body: 'FSQ operates the real UI and records screenshots, interface state, and events so the result can be examined instead of taken on trust.',
      },
      {
        title: 'Review before replay.',
        body: 'A run may produce a Case. Review the evidence and any returned Case first; deterministic replay is a separate user choice.',
      },
    ],
  },
  platforms: ['Web', 'Android', 'Windows', 'macOS'],
  platformLine: {
    label: 'Supported targets:',
    qualifier: 'subject to',
    action: 'platform-specific prerequisites',
  },
  setup: {
    label: 'Get started locally',
    title: 'Choose how to begin.',
    introduction:
      'Coding Agent and FSQ UI are equal alternatives. Both run FSQ locally and require Python 3.11 plus platform-specific prerequisites.',
    codingAgent: {
      title: 'Coding Agent',
      body: 'Install the FSQ Skill in your project, then ask your Coding Agent to test a real user goal through the approval-aware workflow.',
      warning: 'Never paste Provider credentials into Coding Agent chat.',
      action: 'Open Coding Agent setup',
    },
    fsqUi: {
      title: 'FSQ UI',
      body: 'Open the local interface to configure a Workspace, select an exact target, describe a goal, and inspect the resulting evidence.',
      warning: 'Configure your Provider and model in the local FSQ UI.',
      action: 'Open FSQ UI setup',
    },
  },
  setupCommands: ['python -m pip install --upgrade fsq-agent', 'fsq ui'],
  commandPanel: {
    label: 'Terminal',
    copyAction: 'Copy commands',
    copiedAction: 'Copied',
    accessibleName: 'Commands to install FSQ and open the local UI',
    success: 'Commands copied. Paste them into your terminal when ready.',
    failure: 'Could not copy automatically. Select and copy the commands above.',
  },
  boundary: {
    title: 'Alpha software, local evidence.',
    body:
      'Evidence is stored locally and may contain private app data. Review it before sharing. AI operations send necessary provider-bound inputs to your configured Provider; this website does not run tests, collect credentials, or submit visitor data.',
    action: 'Read support and stability guidance',
  },
  media: {
    poster: mediaPosterUrl,
    source: mediaSourceUrl,
    destination: 'https://youtu.be/QqCahxGDdS0',
    alt:
      'FSQ Control Plane demonstration preview showing a goal-driven real application test and its inspectable evidence.',
    caption:
      'Recorded demonstration of a real public-target FSQ workflow. This is not a live testing interface or a capture of current HEAD.',
    provenanceLabel: 'Recorded from FSQ 0.1.0 · September 2026',
    provenanceUrl: `${repositoryFile}/docs/media/fsq-control-plane-demo-provenance.md`,
    width: 1920,
    height: 1080,
  } satisfies WebsiteMedia,
  mediaActions: {
    play: 'Play the recorded demonstration',
    youtube: 'Watch the full demonstration on YouTube',
  },
  mediaChrome: {
    title: 'FSQ / recorded demonstration',
    context: 'Evidence-first workflow',
  },
  footer: {
    credit: 'FSQ · Built by FSQ Lab and the community.',
    navigationLabel: 'Project links',
    gettingStarted: 'Getting started',
    security: 'Security',
    contributing: 'Contribute',
    license: 'MIT License',
  },
  links: {
    repository,
    codingAgent: `${repository}#1-coding-agent`,
    fsqUi: `${repository}#2-fsq-ui`,
    gettingStarted: `${repositoryFile}/docs/getting-started.md`,
    prerequisites: `${repositoryFile}/docs/platform-prerequisites.md`,
    stability: `${repositoryFile}/docs/support-and-stability.md`,
    security: `${repositoryFile}/SECURITY.md`,
    contributing: `${repositoryFile}/CONTRIBUTING.md`,
    license: `${repositoryFile}/LICENSE`,
  },
} as const;
