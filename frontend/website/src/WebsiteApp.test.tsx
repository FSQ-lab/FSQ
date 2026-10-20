import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WebsiteApp } from './WebsiteApp';
import { websiteContent } from './content';
import indexHtml from '../index.html?raw';

const videoSource = 'https://github.com/user-attachments/assets/3acfecf4-bf48-4c76-8245-2eeef0dedb1f';
const youtube = 'https://youtu.be/f1sobvVHNg0';
function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  return writeText;
}

describe('WebsiteApp', () => {
  afterEach(() => vi.restoreAllMocks());
  it('keeps the headline and gives equal unnumbered Quick Test entries', () => {
    render(<WebsiteApp />);
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Describe a goal. Test the real app.');
    const cards = within(screen.getByTestId('setup-choices')).getAllByRole('article');
    expect(cards).toHaveLength(2);
    for (const [index, title] of ['Coding Agent', 'FSQ UI'].entries()) {
      expect(within(cards[index]).getByRole('heading', { level: 3, name: title })).toBeInTheDocument();
      expect(cards[index]).toHaveClass('setup-card');
      expect(cards[index].querySelector('details')).not.toHaveAttribute('open');
      expect(within(cards[index]).getByRole('link', { name: 'Open ' + title + ' setup' })).toHaveClass('button--secondary');
    }
    expect(screen.getByTestId('setup-choices')).not.toHaveTextContent(/\b[12][.)]\s*(Coding Agent|FSQ UI)/);
    expect(screen.getByText(/Python 3.11\+/)).toHaveTextContent(/configured Provider and selected model/);
    expect(screen.getByText(/no manual project Workspace/i)).toBeInTheDocument();
    expect(screen.getByText(/Never paste Provider credentials into Coding Agent chat/)).toBeInTheDocument();
  });
  it('does not load external media before activation and exposes both viewing links', () => {
    const { container } = render(<WebsiteApp />);
    expect(container.querySelector('video, iframe, source, track')).toBeNull();
    expect(screen.getByRole('button', { name: /Play Android Quick Test demonstration/ })).toHaveTextContent('2:11');
    expect(screen.getByRole('link', { name: /Open GitHub video/ })).toHaveAttribute('href', videoSource);
    expect(screen.getByRole('link', { name: /YouTube/ })).toHaveAttribute('href', youtube);
    expect(screen.getByRole('img', { name: websiteContent.media.alt })).toHaveAttribute('src', expect.stringContaining('quick-test-poster'));
  });
  it('activates native inline playback with captions and waits for the playing event', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
    const { container } = render(<WebsiteApp />);
    await userEvent.click(screen.getByRole('button', { name: /Play Android Quick Test demonstration/ }));
    const video = container.querySelector('video')!;
    expect(video).toHaveAttribute('src', videoSource);
    expect(video).toHaveAttribute('controls');
    expect(video).toHaveAttribute('tabindex', '0');
    expect(video).toHaveFocus();
    expect(video).toHaveAttribute('playsinline');
    expect(video).not.toHaveAttribute('autoplay');
    expect(video.querySelector('track')).toHaveAttribute('kind', 'captions');
    expect(video.querySelector('track')).toHaveAttribute('srclang', 'en');
    expect(video.querySelector('track')).toHaveAttribute('src', expect.stringContaining('quick-test.en.vtt'));
    expect(screen.getByTestId('media-status')).toHaveTextContent(/Loading/);
    fireEvent.playing(video);
    expect(screen.getByTestId('media-status')).toHaveTextContent('Playing');
    fireEvent.waiting(video);
    expect(screen.getByTestId('media-status')).toHaveTextContent(/Loading/);
    fireEvent.pause(video);
    expect(screen.getByTestId('media-status')).toHaveTextContent('Paused');
  });
  it('retains controls and links after play rejection or media error', async () => {
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('blocked'));
    const { container } = render(<WebsiteApp />);
    await userEvent.click(screen.getByRole('button', { name: /Play Android Quick Test demonstration/ }));
    await waitFor(() => expect(screen.getByTestId('media-status')).toHaveTextContent(/Use the video controls/));
    const video = container.querySelector('video')!;
    expect(video).toHaveAttribute('controls');
    fireEvent.error(video);
    expect(screen.getByTestId('media-status')).toHaveTextContent(/Could not load/);
    expect(screen.getByRole('link', { name: /Open GitHub video/ })).toHaveAttribute('href', videoSource);
    expect(screen.getByRole('link', { name: /YouTube/ })).toHaveAttribute('href', youtube);
    expect(screen.getByRole('heading', { name: 'Choose how to begin.' })).toBeInTheDocument();
  });
  it('offers the full scenario with local copy feedback and progressive capabilities', async () => {
    const writeText = clipboard();
    render(<WebsiteApp />);
    const scenario = screen.getByLabelText('First Quick Test goal');
    for (const text of ['FSQ-lab/FSQ', 'About', 'Describe a goal.', 'Test the real app.', 'Do not sign in']) expect(scenario).toHaveTextContent(text);
    await userEvent.click(screen.getByRole('button', { name: 'Copy test goal' }));
    expect(writeText).toHaveBeenCalledWith(scenario.textContent);
    expect(screen.getByText('Test goal copied.')).toBeInTheDocument();
    expect(screen.queryByText('Commands copied.')).toBeNull();
    expect(screen.getByText(/recorded demonstration uses Android/)).toBeInTheDocument();
    expect(screen.getByText(/does not save a formal Case or perform Strict Replay/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Star FSQ/ })).toHaveAttribute('href', 'https://github.com/FSQ-lab/FSQ');
    expect(screen.getByRole('link', { name: /Share your experience/ })).toHaveAttribute('href', 'https://github.com/FSQ-lab/FSQ/issues/new/choose');
    expect(screen.getByRole('link', { name: /中文快速开始/ })).toBeInTheDocument();
  });
  it('preserves selectable text and explicit feedback when copying fails', async () => {
    clipboard();
    render(<WebsiteApp />);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    await userEvent.click(screen.getByRole('button', { name: 'Copy commands' }));
    expect(screen.getByText('Could not copy automatically. Select and copy the commands above.')).toBeInTheDocument();
    expect(screen.getByLabelText('Commands to install FSQ and open the local UI')).toHaveTextContent('fsq ui');
    clipboard(vi.fn().mockRejectedValue(new Error('denied')));
    await userEvent.click(screen.getByRole('button', { name: 'Copy test goal' }));
    expect(screen.getByText('Could not copy automatically. Select and copy the test goal above.')).toBeInTheDocument();
    expect(screen.getByLabelText('First Quick Test goal')).toHaveTextContent('FSQ-lab/FSQ');
  });
  it('keeps essential guidance and current demo without JavaScript or automatic embeds', () => {
    for (const text of ['Describe a goal. Test the real app.', 'Coding Agent', 'FSQ UI', 'Python 3.11+', 'Quick Test', 'Alpha and privacy', '中文快速开始', youtube]) expect(indexHtml).toContain(text);
    expect(indexHtml).not.toMatch(/<iframe|autoplay|preconnect|localStorage|sessionStorage|google-analytics/i);
    const { container } = render(<WebsiteApp />);
    expect(container.querySelector('iframe, form, input')).toBeNull();
    for (const img of container.querySelectorAll('img')) expect(img).toHaveAttribute('alt');
  });
});
