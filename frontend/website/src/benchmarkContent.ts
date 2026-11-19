const repository = 'https://github.com/FSQ-lab/benchmarks';
const report = repository + '/blob/main/androidworld/';

export type BenchmarkCase = Readonly<{
  id: string;
  name: string;
  title: string;
  goal: string;
  writes: number;
  maximum: number;
  highlight: string;
  image: string;
  alt: string;
}>;

type BenchmarkFeature = Readonly<{ title: string; body: string }>;

export const benchmarkContent = {
  label: 'Benchmarks',
  title: 'AndroidWorld benchmark',
  introduction: 'Real Android tasks, a frozen experiment, and evidence you can inspect. Explore what worked, what did not, and the limits of this result.',
  score: '95.69%',
  result: '111 / 116 tasks, pass@1',
  validity: '0 invalid · 0 task retries',
  scope: 'This is not an unmodified-upstream or stock-strict leaderboard score. The three task-contract corrections and grading protocol are disclosed below.',
  leaderboard: {
    title: 'AndroidWorld Open-Source & Source-Available Leaderboard',
    scope: 'Representative projects with publicly released code, plus a human reference baseline — not the complete archived leaderboard, which also includes closed-source submissions. Sources combine the Google Research leaderboard archived in November 2026 with recent public project reports.',
    protocols: 'Evaluation protocols, step limits and exact score-to-code commit bindings differ. Display order is not an official same-protocol ranking or a controlled head-to-head comparison. License labels describe released code, not certification of a complete evaluated stack; using a commercial model API alone does not make Agent code closed source.',
    releases: 'Phone Use has public source but its license is unverified. UI-Venus and UI-TARS release open weights and inference code; their complete score-producing AndroidWorld Harness is not verified as public.',
    metrics: 'Minitap’s 94.0% is pass³, not pass@1. FSQ’s 95.69% is 111/116 at pass@1: 113 unchanged upstream tasks plus three disclosed task-contract corrections, using mode B outer-single-settlement. The image rounds FSQ to 95.7%; its 111/116* footnote refers to these disclosed conditions.',
    selection: 'This selected view excludes Lusor (the evaluated Harness was not released), gbox.ai (closed-service dependencies), and Gemini 2.5 Computer Use (the reported AndroidWorld Harness is not verified as public). It does not imply that every included project exposes its entire evaluation stack.',
    imageAction: 'View high-resolution comparison',
    provenanceAction: 'Comparison sources and provenance (JSON)',
    methodAction: 'Source notes and evaluation protocols',
    rows: [
      { name: 'Project ARTEMIS', label: 'Apache-2.0', rate: 99.1, display: '99.1%' },
      { name: 'Phone Use', label: '113/116 · source-available, license unverified', rate: 97.4, display: '97.4%' },
      { name: 'FinalRun', label: 'Apache-2.0', rate: 97.4, display: '97.4%' },
      { name: 'FSQ', label: 'OURS · MIT · Single Model · Strict Budget · 0 App Skills', rate: 95.69, display: '95.69%' },
      { name: 'AutoDevice', label: 'Apache-2.0', rate: 94.8, display: '94.8%' },
      { name: 'Minitap mobile-use', label: 'Apache-2.0 · 109/116 pass³', rate: 94, display: '94.0%' },
      { name: 'Midscene.js', label: 'MIT', rate: 93.1, display: '93.1%' },
      { name: 'DroidRun (Mobilerun)', label: 'MIT', rate: 91.4, display: '91.4%' },
      { name: 'AutoGLM-Mobile', label: 'Apache-2.0', rate: 80.2, display: '80.2%' },
      { name: 'Human Performance', label: 'reference baseline', rate: 80, display: '80.0%' },
      { name: 'Mobile-Agent-v3', label: 'MIT', rate: 73.3, display: '73.3%' },
      { name: 'UI-Venus-72B', label: 'open weights & inference', rate: 65.9, display: '65.9%' },
      { name: 'UI-TARS', label: 'Apache-2.0 · open weights & inference', rate: 46.6, display: '46.6%' },
    ],
  },
  frozenLabel: 'Frozen experiment',
  versions: [
    { label: 'FSQ', value: '33113e4' },
    { label: 'appium-connect-agent', value: '9d197f1' },
    { label: 'AndroidWorld', value: '668267e' },
    { label: 'Task seed', value: 'seed=30' },
    { label: 'Client-selected model', value: 'gpt-6.1-sol' },
  ],
  features: [
    {
      title: 'Single-Model Architecture (Not Multi-Model)',
      body: 'Planning, execution, internal verification, and inspect_with_ai use the same configured model, with no dedicated grounding or verification model. These are separate inference calls, not one request.',
    },
    {
      title: 'Zero App-Specific Skills',
      body: 'Two generic skills are loaded. No test-app-specific skill cards or scripts are loaded; the agent works through the real UI rather than a per-app automation recipe.',
    },
    {
      title: 'Strict Step Budget & True pass@1',
      body: 'Each task has an int(10 × complexity) cap on admitted Harness write-action dispatches. The campaign uses one whole-task attempt; no whole-task retries.',
    },
    {
      title: 'Transparent Upstream Grading',
      body: '113 unchanged upstream tasks and three task-contract corrections: 001, 014, 076. Grading uses mode B outer-single-settlement, not repeated evaluator-driven attempts.',
    },
  ] satisfies readonly BenchmarkFeature[],
  methodAction: 'Read methodology',
  isolation: 'Each task uses a separate Workspace with no loaded cross-task knowledge. Tasks share the emulator and host identity; this is not independent-device isolation.',
  detailsTitle: 'Budget accounting and audit limits',
  accounting: [
    'Admitted writes remain charged even when they fail, including app launches.',
    'Denied dispatches consume no admission.',
    'Reads and assertions are excluded from the write budget.',
    'Composite text entry is one dispatch, not one dispatch per underlying low-level operation.',
  ],
  limitations: [
    'Client-selected model identity only: the model name is not provider-attested.',
    'Historical service birth-anchor audit is incomplete; the methodology records this unresolved audit limit.',
    'Generic-only skills does not claim the absence of benchmark-informed generic guidance.',
  ],
  evidence: {
    label: 'Open evidence',
    title: 'Inspect the full campaign.',
    body: 'The evidence repository covers all 116 tasks, including the five non-passing cases. Read the screenshot reports, compare the machine-readable results, and inspect the method and frozen release—not just the selected successes below.',
    action: 'Explore the evidence repository',
    links: [
      { label: 'English screenshot report', url: report + 'README.md' },
      { label: '中文截图报告', url: report + 'README.zh-CN.md' },
      { label: 'Methodology & limitations', url: report + 'METHOD.md' },
      { label: 'All 116 results (CSV)', url: report + 'results.csv' },
      { label: 'Frozen campaign release', url: repository + '/releases/tag/androidworld-2026-11-19' },
      { label: 'Five non-passing case analyses', url: report + 'README.md' },
    ],
    provenanceAction: 'Case provenance (JSON)',
  },
  casesTitle: 'Featured full-reward tasks',
  casesIntroduction: 'Six selected tasks, each graded with full reward. Goals are quoted from the original tasks; write counts show admitted dispatches against each task’s maximum.',
  reward: 'Full reward · 1.0',
  originalGoalLabel: 'Original task goal',
  observationCaption: 'Representative recorded observation; not necessarily the final frame.',
  observationWarning: 'Screenshots alone do not prove a task’s grade. Follow the reports and results for grading evidence and the five non-passing cases.',
  writeCountLabel: 'admitted writes',
  imageAccessibleLabel: 'View full-size observation for task',
  imageAction: 'Open full-size observation',
  imageWidth: 1080,
  imageHeight: 2400,
  cases: [
    {
      id: '013', name: 'ExpenseAddMultipleFromGallery', title: 'Expenses across apps',
      goal: 'Add the expenses from expenses.jpg in Simple Gallery Pro to pro expense.',
      writes: 44, maximum: 60,
      highlight: 'Read three expenses from an image, enter them across apps, and check saved records.',
      image: '013-ExpenseAddMultipleFromGallery.webp',
      alt: 'Task 013 recorded observation from the expense-entry journey across Simple Gallery Pro and Pro Expense.',
    },
    {
      id: '046', name: 'RecipeAddMultipleRecipesFromImage', title: 'Recipes from image text',
      goal: 'Add the recipes from recipes.jpg in Simple Gallery Pro to the Broccoli recipe app.',
      writes: 56, maximum: 60,
      highlight: 'Turn text in a recipe image into three structured recipes; this is not food-photo recognition.',
      image: '046-RecipeAddMultipleRecipesFromImage.webp',
      alt: 'Task 046 recorded observation from converting recipe-image text into entries in Broccoli.',
    },
    {
      id: '060', name: 'RetroPlaylistDuration', title: 'A playlist within the limit',
      goal: 'Create a playlist in Retro Music titled "Reggae Rhythms 51" with a duration between 45 and 50 minutes using the provided songs.',
      writes: 30, maximum: 30,
      highlight: 'Save a seven-song playlist lasting 46:29 within the requested 45–50 minute interval.',
      image: '060-RetroPlaylistDuration.webp',
      alt: 'Task 060 recorded observation of the playlist in Retro Music.',
    },
    {
      id: '027', name: 'MarkorCreateNoteAndSms', title: 'From note to SMS',
      goal: 'Create a new note in Markor named fair_ant_XOdi.txt with the following text: Actions speak louder than words.. Share the entire content of the note with the phone number +13192815309 via SMS using Simple SMS Messenger',
      writes: 18, maximum: 18,
      highlight: 'Save a note and share its exact text by SMS; sent does not mean delivered to a recipient.',
      image: '027-MarkorCreateNoteAndSms.webp',
      alt: 'Task 027 recorded observation from sharing a Markor note through Simple SMS Messenger.',
    },
    {
      id: '091', name: 'SportsTrackerTotalDistanceForCategoryOverInterval', title: 'Sum the right activities',
      goal: 'What was the total distance covered for kayaking activities in the OpenTracks app from October 09 2023 to October 10 2023? Express your answer as a single number in meters rounded to the nearest integer.',
      writes: 19, maximum: 20,
      highlight: 'Filter kayaking activities by date and sum two distances to 6,510 m; the image shows one contributing record.',
      image: '091-SportsTrackerTotalDistanceForCategoryOverInterval.webp',
      alt: 'Task 091 recorded observation of one OpenTracks record contributing to the total kayaking distance.',
    },
    {
      id: '002', name: 'BrowserDraw', title: 'Draw and submit in Chrome',
      goal: 'Open the file task.html in Downloads in the file manager; when prompted open it with Chrome. Then create a drawing using the three colors shown at the top and hit submit.',
      writes: 19, maximum: 20,
      highlight: 'Use three requested colors on a local browser canvas and submit to receive Success; the drawing is simple lines.',
      image: '002-BrowserDraw.webp',
      alt: 'Task 002 recorded observation from the local three-color browser drawing task.',
    },
  ] satisfies readonly BenchmarkCase[],
  links: { repository, method: report + 'METHOD.md' },
} as const;

export function benchmarkAssetUrl(filename: string): string {
  return import.meta.env.BASE_URL + 'benchmarks/' + filename;
}
