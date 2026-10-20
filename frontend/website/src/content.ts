import logoUrl from '../../../docs/assets/logo-light.svg';
import mediaPosterUrl from '../../../docs/media/quick-test-poster.png';
import taskUrl from '../../../docs/media/quick-test-task.png';
import executionUrl from '../../../docs/media/quick-test-execution.png';
import resultUrl from '../../../docs/media/quick-test-result.png';
import captionsUrl from '../../../docs/media/quick-test.en.vtt?url&no-inline';

const repository = 'https://github.com/FSQ-lab/FSQ';
const repositoryFile = repository + '/blob/main';

export type WebsiteMedia = Readonly<{
  poster: string;
  source: string;
  destination: string;
  captions: string;
  alt: string;
  caption: string;
  provenanceLabel: string;
  provenanceUrl: string;
  width: number;
  height: number;
  duration: string;
}>;

export const websiteContent = {
  canonicalUrl: 'https://fsq-lab.github.io/FSQ/',
  logo: { source: logoUrl, alt: 'FSQ — Fully Self Quality' },
  navigation: {
    label: 'Main navigation', home: 'FSQ website home', workflow: 'How it works',
    setup: 'Try FSQ', repository: 'GitHub repository',
  },
  skipLink: 'Skip to content',
  hero: {
    eyebrow: 'Open source · Alpha · Human in control',
    title: 'Describe a goal. Test the real app.',
    description: 'Describe what to do and what should happen. Let AI operate the real app and verify the outcome. Inspect screenshots, actions, and results to see what happened.',
    setupAction: 'Try FSQ locally', mediaAction: 'Watch the demo',
  },
  workflow: {
    title: 'From goal to evidence.',
    introduction: 'One journey: find the FSQ repository, follow its website link, and check the homepage. These recorded Android screens show the task, execution, and result; the surrounding explanations are editorial.',
    steps: [
      {
        title: 'Describe the journey and the checks.',
        body: 'Search for FSQ-lab/FSQ on GitHub, open the website linked in About, and verify “Describe a goal.” and “Test the real app.” on the destination page.',
        image: taskUrl, width: 1800, height: 1040,
        alt: 'Editorial task card with a real Quick Test goal: find the FSQ repository, follow About to its website, and verify both homepage headlines.',
        caption: 'Task explanation around a recorded Quick Test goal. Define the outcome before execution.',
      },
      {
        title: 'Watch AI operate the real app.',
        body: 'The recorded Android run navigates the browser while FSQ captures actions and screen evidence. The execution view connects the live device screen with the action timeline.',
        image: executionUrl, width: 1593, height: 705,
        alt: 'Recorded FSQ execution view showing the Android browser beside the test action timeline.',
        caption: 'Real execution capture from the Android demonstration.',
      },
      {
        title: 'Check the result against the evidence.',
        body: 'The captured destination shows both required headlines. The Run report separately shows execution success, verification success, and complete evidence. Review the recorded page as well as the reported outcome.',
        image: resultUrl, width: 1800, height: 1040,
        alt: 'Editorial result card pairing the captured FSQ homepage headlines with the same test’s Run report showing execution and verification success.',
        caption: 'Recorded page and Run report, with editorial explanation. A result remains inspectable.',
      },
    ],
  },
  platforms: ['Web', 'Android', 'Windows', 'macOS'],
  platformLine: { label: 'Supported targets:', qualifier: 'subject to', action: 'platform-specific prerequisites' },
  setup: {
    label: 'Get started locally', title: 'Choose how to begin.',
    introduction: 'Two equal ways to run one Quick Test. You need Python 3.11+, a supported configured Provider and selected model, and platform-specific prerequisites.',
    quickTest: 'Quick Test needs no manual project Workspace setup and does not save a formal Case or perform Strict Replay. Review the result and evidence first.',
    codingAgent: {
      title: 'Coding Agent',
      body: 'Add the FSQ Skill, then ask your Coding Agent to run the goal below with FSQ.',
      warning: 'Never paste Provider credentials into Coding Agent chat.',
      details: 'Skill setup and first run',
      detail: 'Follow the approval-aware setup guide to install or upgrade fsq-agent and run fsq init --agent skill in your project. Reload the Coding Agent, choose Web and an available browser, then ask: “Use FSQ Quick Test for this goal” and paste the goal below. The Skill discovers readiness and reports actual progress and evidence.',
      action: 'Open Coding Agent setup',
    },
    fsqUi: {
      title: 'FSQ UI',
      body: 'Install FSQ, open the local UI, and choose Quick Test. Paste the same goal below.',
      warning: 'Configure your Provider and model in the local FSQ UI.',
      details: 'Install and start Quick Test',
      detail: 'Run the commands below. In Settings, configure a supported Provider and explicitly select a model. Return to Quick Test, select Web and an available browser, paste the goal, and choose Start Quick Test. Open the report to inspect the result and evidence.',
      action: 'Open FSQ UI setup',
    },
  },
  setupCommands: ['python -m pip install --upgrade fsq-agent', 'fsq ui'],
  commandPanel: {
    label: 'Terminal', copyAction: 'Copy commands', copiedAction: 'Copied commands',
    accessibleName: 'Commands to install FSQ and open the local UI',
    success: 'Commands copied.', failure: 'Could not copy automatically. Select and copy the commands above.',
  },
  example: {
    title: 'Try a complete user journey.',
    introduction: 'Start with Web in your selected browser. The recorded demonstration uses Android; platform setup and execution differ. Search results and outcomes can vary.',
    goal: 'Use the selected browser to search for the FSQ-lab/FSQ GitHub repository. Open that repository, follow the website link in its About section, and verify that the destination page displays both “Describe a goal.” and “Test the real app.” Report the outcome with the captured page evidence. Only browse public pages. Do not sign in, change repository content, or submit forms. Stop and report a blocker if the repository or About website link cannot be identified.',
    label: 'First Quick Test goal', copyAction: 'Copy test goal', copiedAction: 'Copied test goal',
    success: 'Test goal copied.', failure: 'Could not copy automatically. Select and copy the test goal above.',
  },
  capabilities: {
    title: 'Go further when you need to.',
    introduction: 'Quick Test starts with a goal and inspectable evidence. Case management and replay are separate workflows.',
    items: [
      { title: 'Inspect the run.', body: 'Review screenshots, interface state, and events in the report. Keep failures and uncertain outcomes visible alongside successes.' },
      { title: 'Manage reusable Cases.', body: 'Use FSQ Case Manager for registered Workspaces, explicit Case creation or saving when a run returns a candidate, and run history. A run may produce a Case; generation is conditional.' },
      { title: 'Choose deterministic replay.', body: 'Review any returned Case unchanged. Deterministic replay is a separate user choice with its own target checks and repeated side effects; Quick Test does not run it for you.' },
    ],
  },
  community: {
    title: 'Help shape FSQ.',
    body: 'If FSQ is useful to you, Star the repository to help others discover it. Tried it? Share what worked, where you got stuck, and what you want to test next.',
    feedback: 'Include the FSQ version, platform, sanitized goal, expected and actual outcome, and only diagnostic excerpts you have reviewed. Remove credentials, personal data, private URLs, paths, and identifiers before sharing.',
  },
  boundary: {
    title: 'Alpha software, local evidence.',
    body: 'FSQ is open-source alpha software. Evidence is stored locally and may contain private app data. Review it before sharing. AI operations send necessary provider-bound inputs to your configured Provider; this website does not run tests, collect credentials, or submit visitor data.',
    action: 'Read support and stability guidance',
  },
  media: {
    poster: mediaPosterUrl,
    source: 'https://github.com/user-attachments/assets/3acfecf4-bf48-4c76-8245-2eeef0dedb1f',
    destination: 'https://youtu.be/f1sobvVHNg0', captions: captionsUrl,
    alt: 'Android Quick Test demonstration cover showing the real device browser and FSQ execution evidence.',
    caption: 'Android Quick Test: search for the FSQ repository, follow its website link, and verify both homepage headlines. English captions are available in the player.',
    provenanceLabel: 'Media sources and review status',
    provenanceUrl: repositoryFile + '/docs/media/quick-test-demo-provenance.md',
    width: 1920, height: 1080, duration: '2:11',
  } satisfies WebsiteMedia,
  mediaActions: { play: 'Play Android Quick Test demonstration', github: 'Open GitHub video', youtube: 'Watch on YouTube' },
  mediaChrome: { title: 'FSQ / Android Quick Test', context: 'Recorded real-app testing' },
  footer: {
    credit: 'FSQ · Built by FSQ Lab and the community.', navigationLabel: 'Project links',
    gettingStarted: 'Getting started', security: 'Security', contributing: 'Contribute', license: 'MIT License',
  },
  links: {
    repository, codingAgent: repositoryFile + '/docs/getting-started.md#coding-agent',
    fsqUi: repositoryFile + '/docs/getting-started.md#fsq-ui',
    gettingStarted: repositoryFile + '/docs/getting-started.md',
    chineseGuide: repositoryFile + '/docs/getting-started.zh-CN.md',
    prerequisites: repositoryFile + '/docs/platform-prerequisites.md',
    stability: repositoryFile + '/docs/support-and-stability.md',
    security: repositoryFile + '/SECURITY.md', contributing: repositoryFile + '/CONTRIBUTING.md',
    license: repositoryFile + '/LICENSE', feedback: repository + '/issues/new/choose',
  },
} as const;
