import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WebsiteApp } from './WebsiteApp';
import caseProvenance from '../public/benchmarks/cases.json';

const repository = 'https://github.com/FSQ-lab/benchmarks';
const report = repository + '/blob/main/androidworld/';
const cases = [
  {
    id: '013', name: 'ExpenseAddMultipleFromGallery', writes: 44, maximum: 60,
    goal: 'Add the expenses from expenses.jpg in Simple Gallery Pro to pro expense.',
  },
  {
    id: '046', name: 'RecipeAddMultipleRecipesFromImage', writes: 56, maximum: 60,
    goal: 'Add the recipes from recipes.jpg in Simple Gallery Pro to the Broccoli recipe app.',
  },
  {
    id: '060', name: 'RetroPlaylistDuration', writes: 30, maximum: 30,
    goal: 'Create a playlist in Retro Music titled "Reggae Rhythms 51" with a duration between 45 and 50 minutes using the provided songs.',
  },
  {
    id: '027', name: 'MarkorCreateNoteAndSms', writes: 18, maximum: 18,
    goal: 'Create a new note in Markor named fair_ant_XOdi.txt with the following text: Actions speak louder than words.. Share the entire content of the note with the phone number +13192815309 via SMS using Simple SMS Messenger',
  },
  {
    id: '091', name: 'SportsTrackerTotalDistanceForCategoryOverInterval', writes: 19, maximum: 20,
    goal: 'What was the total distance covered for kayaking activities in the OpenTracks app from October 09 2023 to October 10 2023? Express your answer as a single number in meters rounded to the nearest integer.',
  },
  {
    id: '002', name: 'BrowserDraw', writes: 19, maximum: 20,
    goal: 'Open the file task.html in Downloads in the file manager; when prompted open it with Chrome. Then create a drawing using the three colors shown at the top and hit submit.',
  },
] as const;

function benchmark() {
  return screen.getByRole('region', { name: 'AndroidWorld benchmark' });
}

describe('AndroidWorld benchmark presentation', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); });

  it('offers benchmark navigation between the workflow and the unchanged trial journey', () => {
    render(<WebsiteApp />);
    expect(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('link', { name: 'Benchmarks' })).toHaveAttribute('href', '#benchmarks');
    const section = benchmark();
    expect(section).toHaveAttribute('id', 'benchmarks');
    expect(document.getElementById('workflow')!.nextElementSibling).toBe(section);
    expect(section.nextElementSibling).toBe(document.getElementById('setup'));
    expect(screen.getByRole('heading', { name: 'Coding Agent' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'FSQ UI' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Alpha software, local evidence.' })).toBeInTheDocument();
  });

  it('states the exact frozen result and keeps material grading and isolation limits outside disclosures', () => {
    render(<WebsiteApp />);
    const section = benchmark();
    const content = within(section);
    expect(content.getByText('95.69%')).toBeVisible();
    expect(content.getByText('111 / 116 tasks, pass@1')).toBeVisible();
    expect(content.getByText('0 invalid · 0 task retries')).toBeVisible();
    for (const version of ['33113e4', '9d197f1', '668267e', 'seed=30', 'gpt-6.1-sol']) {
      expect(content.getByText(version)).toBeVisible();
    }
    for (const text of [/113 unchanged upstream tasks/, /three task-contract corrections: 001, 014, 076/, /not an unmodified-upstream or stock-strict leaderboard score/i, /share the emulator and host identity/]) {
      const disclosure = content.getByText(text);
      expect(disclosure).toBeVisible();
      expect(disclosure.closest('details')).toBeNull();
    }
    expect(content.getByText(/mode B outer-single-settlement/)).toBeVisible();
    expect(content.getByText(/one whole-task attempt; no whole-task retries/)).toBeVisible();
  });

  it('explains all four evidence-linked distinctions without overstating model, skills, or budgets', async () => {
    render(<WebsiteApp />);
    const section = within(benchmark());
    for (const title of [
      'Single-Model Architecture (Not Multi-Model)',
      'Zero App-Specific Skills',
      'Strict Step Budget & True pass@1',
      'Transparent Upstream Grading',
    ]) {
      const article = section.getByRole('heading', { name: title }).closest('article')!;
      expect(within(article).getByRole('link', { name: /Read methodology/ })).toHaveAttribute('href', report + 'METHOD.md');
    }
    expect(section.getByText(/separate inference calls, not one request/)).toBeVisible();
    expect(section.getByText(/inspect_with_ai/)).toHaveTextContent(/no dedicated grounding or verification model/);
    expect(section.getByText(/Two generic skills/)).toBeVisible();
    expect(section.getByText(/int\(10 × complexity\)/)).toHaveTextContent(/admitted Harness write-action dispatches/);
    const details = section.getByText('Budget accounting and audit limits').closest('details')!;
    expect(details).not.toHaveAttribute('open');
    await userEvent.click(within(details).getByText('Budget accounting and audit limits'));
    expect(details).toHaveAttribute('open');
    const accounting = within(details);
    expect(accounting.getByText(/Admitted writes remain charged even when they fail/)).toBeVisible();
    expect(accounting.getByText(/Denied dispatches consume no admission/)).toBeVisible();
    expect(accounting.getByText(/Reads and assertions are excluded/)).toBeVisible();
    expect(accounting.getByText(/Composite text entry is one dispatch/)).toBeVisible();
    expect(accounting.getByText(/Client-selected model identity only/)).toHaveTextContent(/not provider-attested/);
    expect(accounting.getByText(/Historical service birth-anchor audit is incomplete/)).toBeVisible();
    expect(accounting.getByText(/does not claim the absence of benchmark-informed generic guidance/)).toBeVisible();
  });

  it('shows all six original goals and actual admitted write counts with accessible observation links', async () => {
    render(<WebsiteApp />);
    const list = within(benchmark()).getByRole('list', { name: 'Featured full-reward tasks' });
    const cards = within(list).getAllByRole('article');
    expect(cards).toHaveLength(6);
    for (const [index, item] of cases.entries()) {
      const card = within(cards[index]);
      expect(card.getByRole('heading', { level: 4 })).toHaveTextContent(item.id);
      expect(card.getByText(item.goal)).toBeVisible();
      expect(card.getByText(`${item.writes} / ${item.maximum} admitted writes`)).toBeVisible();
      expect(card.getByText('Full reward · 1.0')).toBeVisible();
      const image = card.getByRole('img');
      const imagePath = import.meta.env.BASE_URL + `benchmarks/${item.id}-${item.name}.webp`;
      expect(image).toHaveAttribute('src', imagePath);
      expect(image).toHaveAttribute('width', '1080');
      expect(image).toHaveAttribute('height', '2400');
      expect(image).toHaveAttribute('alt', expect.stringMatching(/observation/i));
      expect(card.getByRole('link', { name: `View full-size observation for task ${item.id}` })).toHaveAttribute('href', imagePath);
      expect(card.getByText(/Representative recorded observation; not necessarily the final frame/)).toBeVisible();
    }
    expect(within(cards[2]).getByText(/46:29/)).toBeVisible();
    expect(within(cards[4]).getByText(/6,510 m/)).toHaveTextContent(/one contributing record/);
    expect(within(cards[3]).getByText(/sent does not mean delivered/)).toBeVisible();
    expect(within(cards[1]).getByText(/not food-photo recognition/)).toBeVisible();
    const links = within(list).getAllByRole('link');
    links[0].focus();
    expect(links[0]).toHaveFocus();
    await userEvent.tab();
    expect(links[1]).toHaveFocus();
    expect(within(benchmark()).getByText(/Screenshots alone do not prove a task’s grade/)).toBeVisible();
  });

  it('resolves full-size evidence below the GitHub Pages project base without remote image loading', () => {
    vi.stubEnv('BASE_URL', '/FSQ/');
    render(<WebsiteApp />);
    for (const image of within(benchmark()).getAllByRole('img')) {
      expect(image.getAttribute('src')).toMatch(/^\/FSQ\/benchmarks\/.+\.webp$/);
      expect(image.closest('a')).toHaveAttribute('href', image.getAttribute('src'));
    }
    expect(within(benchmark()).getByRole('link', { name: 'Case provenance (JSON)' })).toHaveAttribute('href', '/FSQ/benchmarks/cases.json');
  });

  it('provides the evidence repository, both reports, methodology, data, release, and five non-passing analyses', () => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    const { container } = render(<WebsiteApp />);
    const section = within(benchmark());
    for (const [label, destination] of [
      ['Explore the evidence repository', repository],
      ['English screenshot report', report + 'README.md'],
      ['中文截图报告', report + 'README.zh-CN.md'],
      ['Methodology & limitations', report + 'METHOD.md'],
      ['All 116 results (CSV)', report + 'results.csv'],
      ['Frozen campaign release', repository + '/releases/tag/androidworld-2026-11-19'],
      ['Five non-passing case analyses', report + 'README.md'],
    ]) {
      expect(section.getByRole('link', { name: label })).toHaveAttribute('href', destination);
    }
    expect(section.getByText(/all 116 tasks, including the five non-passing cases/)).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
    expect(container.querySelector('iframe, form, input')).toBeNull();
    fetch.mockRestore();
  });

  it('keeps the displayed task fixtures aligned with unchanged provenance and six local observations', () => {
    expect(caseProvenance.cases.map(item => ({
      id: String(item.index).padStart(3, '0'), name: item.name,
      writes: item.writes, maximum: item.maximum, goal: item.goal,
    }))).toEqual(cases);
    const observations = import.meta.glob('../public/benchmarks/*.webp', { eager: true, query: '?url', import: 'default' });
    expect(Object.keys(observations).sort()).toEqual(cases.map(item => `../public/benchmarks/${item.id}-${item.name}.webp`).sort());
    expect(caseProvenance.cases.every(item => item.reward === 1)).toBe(true);
  });
});
