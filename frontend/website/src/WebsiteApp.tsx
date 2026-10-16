import { useState } from 'react';

import { websiteContent as content } from './content';

const externalLinkProps = {
  target: '_blank',
  rel: 'noreferrer',
} as const;

function Arrow() {
  return <span aria-hidden="true">↗</span>;
}

function CopyCommands() {
  const [status, setStatus] = useState<'idle' | 'success' | 'failure'>('idle');
  const commands = content.setupCommands.join('\n');

  async function copyCommands() {
    try {
      if (!navigator.clipboard) {
        throw new Error('Clipboard API unavailable');
      }
      await navigator.clipboard.writeText(commands);
      setStatus('success');
    } catch {
      setStatus('failure');
    }
  }

  return (
    <div className="command-panel">
      <div className="command-panel__header">
        <span>{content.commandPanel.label}</span>
        <button type="button" className="copy-button" onClick={copyCommands}>
          {status === 'success'
            ? content.commandPanel.copiedAction
            : content.commandPanel.copyAction}
        </button>
      </div>
      <pre
        className="commands"
        tabIndex={0}
        aria-label={content.commandPanel.accessibleName}
      >
        <code>{commands}</code>
      </pre>
      <p className={`copy-status copy-status--${status}`} role="status" aria-live="polite">
        {status === 'success' && content.commandPanel.success}
        {status === 'failure' && content.commandPanel.failure}
      </p>
    </div>
  );
}

export function WebsiteApp() {
  return (
    <>
      <a className="skip-link" href="#main">
        {content.skipLink}
      </a>
      <header className="site-header shell">
        <a className="brand" href={content.canonicalUrl} aria-label={content.navigation.home}>
          <img src={content.logo.source} alt={content.logo.alt} width="400" height="120" />
        </a>
        <nav aria-label={content.navigation.label}>
          <a href="#workflow">{content.navigation.workflow}</a>
          <a href="#setup">{content.navigation.setup}</a>
          <a href={content.links.repository} {...externalLinkProps}>
            {content.navigation.repository} <Arrow />
          </a>
        </nav>
      </header>

      <main id="main">
        <section className="hero shell" aria-labelledby="hero-title">
          <p className="eyebrow">{content.hero.eyebrow}</p>
          <h1 id="hero-title" aria-label={content.hero.title}>
            <span>Describe a goal. </span>
            <span>Test the real app.</span>
          </h1>
          <p className="hero__description">{content.hero.description}</p>
          <div className="hero__actions">
            <a className="button button--primary" href="#setup">
              {content.hero.setupAction}
            </a>
            <a className="button button--secondary" href="#demonstration">
              {content.hero.mediaAction}
            </a>
          </div>

          <figure className="media-frame" id="demonstration">
            <div className="media-frame__chrome" aria-hidden="true">
              <span>{content.mediaChrome.title}</span>
              <span>{content.mediaChrome.context}</span>
            </div>
            <img
              src={content.media.poster}
              alt={content.media.alt}
              width={content.media.width}
              height={content.media.height}
            />
            <figcaption>
              <p>{content.media.caption}</p>
              <div className="media-frame__links">
                <a href={content.media.source} {...externalLinkProps}>
                  {content.mediaActions.play} <Arrow />
                </a>
                <a href={content.media.destination} {...externalLinkProps}>
                  {content.mediaActions.youtube} <Arrow />
                </a>
                <a href={content.media.provenanceUrl} {...externalLinkProps}>
                  {content.media.provenanceLabel} <Arrow />
                </a>
              </div>
            </figcaption>
          </figure>

          <p className="platform-line">
            <strong>{content.platformLine.label}</strong> {content.platforms.join(', ')} —{' '}
            {content.platformLine.qualifier}{' '}
            <a href={content.links.prerequisites} {...externalLinkProps}>
              {content.platformLine.action}
            </a>
            .
          </p>
        </section>

        <section className="section shell" id="workflow" aria-labelledby="workflow-title">
          <p className="section-label">{content.navigation.workflow}</p>
          <h2 id="workflow-title">{content.workflow.title}</h2>
          <p className="section-intro">{content.workflow.introduction}</p>
          <div className="workflow-grid">
            {content.workflow.steps.map((step, index) => (
              <article className="workflow-step" key={step.title}>
                <span aria-hidden="true">0{index + 1}</span>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="section shell setup" id="setup" aria-labelledby="setup-title">
          <p className="section-label">{content.setup.label}</p>
          <h2 id="setup-title">{content.setup.title}</h2>
          <p className="section-intro">{content.setup.introduction}</p>
          <div className="setup-grid" data-testid="setup-choices">
            <article className="setup-card">
              <div>
                <h3>{content.setup.codingAgent.title}</h3>
                <p>{content.setup.codingAgent.body}</p>
                <p className="setup-card__warning">{content.setup.codingAgent.warning}</p>
              </div>
              <a
                className="button button--secondary"
                href={content.links.codingAgent}
                {...externalLinkProps}
              >
                {content.setup.codingAgent.action} <Arrow />
              </a>
            </article>

            <article className="setup-card">
              <div>
                <h3>{content.setup.fsqUi.title}</h3>
                <p>{content.setup.fsqUi.body}</p>
                <p className="setup-card__warning">{content.setup.fsqUi.warning}</p>
              </div>
              <CopyCommands />
              <a
                className="button button--secondary"
                href={content.links.fsqUi}
                {...externalLinkProps}
              >
                {content.setup.fsqUi.action} <Arrow />
              </a>
            </article>
          </div>
        </section>

        <aside className="boundary shell" aria-labelledby="boundary-title">
          <h2 id="boundary-title">{content.boundary.title}</h2>
          <p>{content.boundary.body}</p>
          <a href={content.links.stability} {...externalLinkProps}>
            {content.boundary.action} <Arrow />
          </a>
        </aside>
      </main>

      <footer className="site-footer shell">
        <p>
          {content.footer.credit}
        </p>
        <nav aria-label={content.footer.navigationLabel}>
          <a href={content.links.gettingStarted} {...externalLinkProps}>
            {content.footer.gettingStarted}
          </a>
          <a href={content.links.security} {...externalLinkProps}>
            {content.footer.security}
          </a>
          <a href={content.links.contributing} {...externalLinkProps}>
            {content.footer.contributing}
          </a>
          <a href={content.links.license} {...externalLinkProps}>
            {content.footer.license}
          </a>
        </nav>
      </footer>
    </>
  );
}
