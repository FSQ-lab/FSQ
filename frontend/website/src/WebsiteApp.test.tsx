import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { WebsiteApp } from './WebsiteApp';
import { websiteContent } from './content';
import indexHtml from '../index.html?raw';

describe('WebsiteApp', () => {
  it('presents the page truth with a logical heading hierarchy', () => {
    render(<WebsiteApp />);

    expect(
      screen.getByRole('heading', { level: 1, name: 'Describe a goal. Test the real app.' }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByText(/open-source alpha software/i)).toBeInTheDocument();
    expect(screen.getByText(/screenshots, interface state, and events/i)).toBeInTheDocument();
    expect(screen.getByText(/may produce a Case/i)).toBeInTheDocument();
    expect(screen.getByText(/deterministic replay is a separate user choice/i)).toBeInTheDocument();

    const main = screen.getByRole('main');
    expect(within(main).getByRole('heading', { level: 2, name: 'From goal to evidence.' })).toBeInTheDocument();
    expect(within(main).getByRole('heading', { level: 2, name: 'Choose how to begin.' })).toBeInTheDocument();
  });

  it('renders Coding Agent and FSQ UI as exactly two unnumbered equal alternatives', () => {
    render(<WebsiteApp />);

    const choices = screen.getByTestId('setup-choices');
    const cards = within(choices).getAllByRole('article');
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByRole('heading', { level: 3, name: 'Coding Agent' })).toBeInTheDocument();
    expect(within(cards[1]).getByRole('heading', { level: 3, name: 'FSQ UI' })).toBeInTheDocument();
    expect(cards[0]).toHaveClass('setup-card');
    expect(cards[1]).toHaveClass('setup-card');
    expect(choices).not.toHaveTextContent(/\b0?[12][.)/]\s*(Coding Agent|FSQ UI)/i);
    expect(within(cards[0]).getByRole('link', { name: /Coding Agent setup/i })).toHaveAttribute(
      'href',
      websiteContent.links.codingAgent,
    );
    expect(within(cards[1]).getByRole('link', { name: /FSQ UI setup/i })).toHaveAttribute(
      'href',
      websiteContent.links.fsqUi,
    );
  });

  it('uses canonical links and centralized media metadata', () => {
    render(<WebsiteApp />);

    expect(screen.getByRole('link', { name: /GitHub repository/i })).toHaveAttribute(
      'href',
      websiteContent.links.repository,
    );
    expect(screen.getByRole('link', { name: /platform.*prerequisites/i })).toHaveAttribute(
      'href',
      websiteContent.links.prerequisites,
    );
    expect(screen.getByRole('link', { name: /Watch the full demonstration/i })).toHaveAttribute(
      'href',
      websiteContent.media.destination,
    );

    const media = screen.getByRole('img', { name: websiteContent.media.alt });
    expect(media).toHaveAttribute('src', expect.stringContaining('fsq-v0.1.0-demo-thumbnail'));
    expect(media).toHaveAttribute('width', String(websiteContent.media.width));
    expect(media).toHaveAttribute('height', String(websiteContent.media.height));
    expect(screen.getByRole('link', { name: /Play the recorded demonstration/i })).toHaveAttribute(
      'href',
      expect.stringContaining('fsq-control-plane-demo'),
    );
    expect(screen.getByText(websiteContent.media.provenanceLabel)).toBeInTheDocument();
    expect(screen.getByText(/recorded from FSQ 0.1.0/i)).toBeInTheDocument();
    expect(screen.queryByTitle(/youtube/i)).not.toBeInTheDocument();
  });

  it('states provider, privacy, alpha, and platform boundaries', () => {
    render(<WebsiteApp />);

    expect(screen.getByText(/never paste Provider credentials into Coding Agent chat/i)).toBeInTheDocument();
    expect(screen.getByText(/configure your Provider and model in the local FSQ UI/i)).toBeInTheDocument();
    expect(screen.getAllByText(/configured Provider/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Evidence is stored locally and may contain private app data/i)).toBeInTheDocument();
    expect(
      screen.getByText((_, element) => element?.classList.contains('platform-line') ?? false),
    ).toHaveTextContent(/Web, Android, Windows, macOS/i);
    expect(screen.getAllByText(/platform-specific prerequisites/i).length).toBeGreaterThan(0);
  });

  it('reports clipboard success only after commands are written', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    render(<WebsiteApp />);

    await userEvent.click(screen.getByRole('button', { name: 'Copy commands' }));

    expect(writeText).toHaveBeenCalledWith(websiteContent.setupCommands.join('\n'));
    expect(screen.getByRole('status')).toHaveTextContent('Commands copied.');
  });

  it('leaves selectable commands and explicit guidance when clipboard copying fails', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    render(<WebsiteApp />);

    await userEvent.click(screen.getByRole('button', { name: 'Copy commands' }));

    expect(screen.getByRole('status')).toHaveTextContent(
      'Could not copy automatically. Select and copy the commands above.',
    );
    const commands = screen.getByLabelText('Commands to install FSQ and open the local UI');
    expect(commands).toHaveTextContent('python -m pip install --upgrade fsq-agent');
    expect(commands).toHaveTextContent('fsq ui');
    fireEvent.select(commands);
  });

  it('keeps essential product and setup guidance in the JavaScript-disabled HTML boundary', () => {
    expect(indexHtml).toContain('Describe a goal. Test the real app.');
    expect(indexHtml).toContain('Coding Agent');
    expect(indexHtml).toContain('FSQ UI');
    expect(indexHtml).toContain('#1-coding-agent');
    expect(indexHtml).toContain('#2-fsq-ui');
    expect(indexHtml).toMatch(/alpha/i);
    expect(indexHtml).toMatch(/credentials.*Coding Agent chat/i);
    expect(indexHtml).toMatch(/Evidence is stored locally/i);
  });
});
