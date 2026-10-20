import { useRef, useState } from 'react';
import { websiteContent as content } from './content';

const externalLinkProps = { target: '_blank', rel: 'noreferrer' } as const;
function Arrow() { return <span aria-hidden="true">↗</span>; }

type CopyTextProps = {
  text: string; label: string; accessibleName: string; copyAction: string;
  copiedAction: string; success: string; failure: string; wrap?: boolean;
};
function CopyText(props: CopyTextProps) {
  const [status, setStatus] = useState<'idle' | 'success' | 'failure'>('idle');
  async function copy() {
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(props.text);
      setStatus('success');
    } catch { setStatus('failure'); }
  }
  return (
    <div className="command-panel">
      <div className="command-panel__header">
        <span>{props.label}</span>
        <button type="button" className="copy-button" onClick={copy}>
          {status === 'success' ? props.copiedAction : props.copyAction}
        </button>
      </div>
      <pre className={'commands' + (props.wrap ? ' commands--wrap' : '')} tabIndex={0} aria-label={props.accessibleName}>
        <code>{props.text}</code>
      </pre>
      <p className={'copy-status copy-status--' + status} role="status" aria-live="polite">
        {status === 'success' && props.success}{status === 'failure' && props.failure}
      </p>
    </div>
  );
}

function Demonstration() {
  const [activated, setActivated] = useState(false);
  const [status, setStatus] = useState('');
  const requestedPlayback = useRef(false);
  const failed = useRef(false);
  function attachVideo(video: HTMLVideoElement | null) {
    if (!video || requestedPlayback.current) return;
    requestedPlayback.current = true;
    video.focus();
    void video.play().catch(() => {
      if (!failed.current) setStatus('Playback did not start. Use the video controls or open a viewing link below.');
    });
  }
  return (
    <figure className="media-frame" id="demonstration">
      <div className="media-frame__chrome" aria-hidden="true"><span>{content.mediaChrome.title}</span><span>{content.mediaChrome.context}</span></div>
      <div className="media-player">
        {activated ? (
          <video ref={attachVideo} src={content.media.source} poster={content.media.poster} controls playsInline preload="metadata" tabIndex={0}
            aria-label="Android Quick Test recorded demonstration"
            onPlaying={() => setStatus('Playing')}
            onWaiting={() => { if (!failed.current) setStatus('Loading video…'); }}
            onPause={() => { if (!failed.current) setStatus('Paused'); }}
            onEnded={() => setStatus('Demonstration ended. Review the evidence and try the goal below.')}
            onError={() => { failed.current = true; setStatus('Could not load the video. Open GitHub video or YouTube below.'); }}>
            <track kind="captions" src={content.media.captions} srcLang="en" label="English" default />
          </video>
        ) : (
          <button className="media-play" type="button" aria-label={content.mediaActions.play}
            onClick={() => { setStatus('Loading video…'); setActivated(true); }}>
            <img src={content.media.poster} alt={content.media.alt} width={content.media.width} height={content.media.height} />
            <span className="media-play__label"><span aria-hidden="true">▶</span> Play demo <span>{content.media.duration}</span></span>
          </button>
        )}
      </div>
      <figcaption>
        <p>{content.media.caption}</p>
        <p className="media-status" role="status" aria-live="polite" data-testid="media-status">{status}</p>
        <div className="media-frame__links">
          <a href={content.media.source} {...externalLinkProps}>{content.mediaActions.github} <Arrow /></a>
          <a href={content.media.destination} {...externalLinkProps}>{content.mediaActions.youtube} <Arrow /></a>
          <a href={content.media.provenanceUrl} {...externalLinkProps}>{content.media.provenanceLabel} <Arrow /></a>
        </div>
      </figcaption>
    </figure>
  );
}

export function WebsiteApp() {
  return (
    <>
      <a className="skip-link" href="#main">{content.skipLink}</a>
      <header className="site-header shell">
        <a className="brand" href={content.canonicalUrl} aria-label={content.navigation.home}><img src={content.logo.source} alt={content.logo.alt} width="400" height="120" /></a>
        <nav aria-label={content.navigation.label}>
          <a href="#workflow">{content.navigation.workflow}</a><a href="#setup">{content.navigation.setup}</a>
          <a href={content.links.repository} {...externalLinkProps}>{content.navigation.repository} <Arrow /></a>
        </nav>
      </header>
      <main id="main">
        <section className="hero shell" aria-labelledby="hero-title">
          <p className="eyebrow">{content.hero.eyebrow}</p>
          <h1 id="hero-title" aria-label={content.hero.title}><span>Describe a goal. </span><span>Test the real app.</span></h1>
          <p className="hero__description">{content.hero.description}</p>
          <div className="hero__actions"><a className="button button--primary" href="#setup">{content.hero.setupAction}</a><a className="button button--secondary" href="#demonstration">{content.hero.mediaAction}</a></div>
          <Demonstration />
          <p className="platform-line"><strong>{content.platformLine.label}</strong> {content.platforms.join(', ')} — {content.platformLine.qualifier}{' '}<a href={content.links.prerequisites} {...externalLinkProps}>{content.platformLine.action}</a>.</p>
        </section>
        <section className="section shell" id="workflow" aria-labelledby="workflow-title">
          <p className="section-label">{content.navigation.workflow}</p><h2 id="workflow-title">{content.workflow.title}</h2>
          <p className="section-intro">{content.workflow.introduction}</p>
          <div className="workflow-stories">
            {content.workflow.steps.map((step, index) => (
              <article className="workflow-story" key={step.title}>
                <div className="workflow-story__copy"><span className="section-label" aria-hidden="true">0{index + 1}</span><h3>{step.title}</h3><p>{step.body}</p></div>
                <figure><a href={step.image} {...externalLinkProps} aria-label={'View full-size image: ' + step.title}><img src={step.image} alt={step.alt} loading="lazy" width={step.width} height={step.height} /></a><figcaption>{step.caption} <a href={step.image} {...externalLinkProps}>View full size <Arrow /></a></figcaption></figure>
              </article>
            ))}
          </div>
        </section>
        <section className="section shell setup" id="setup" aria-labelledby="setup-title">
          <p className="section-label">{content.setup.label}</p><h2 id="setup-title">{content.setup.title}</h2>
          <p className="section-intro">{content.setup.introduction} <a href={content.links.prerequisites} {...externalLinkProps}>Check prerequisites <Arrow /></a></p>
          <p className="section-intro">{content.setup.quickTest}</p>
          <div className="setup-grid" data-testid="setup-choices">
            {[content.setup.codingAgent, content.setup.fsqUi].map((path, index) => (
              <article className="setup-card" key={path.title}>
                <div><h3>{path.title}</h3><p>{path.body}</p><p className="setup-card__warning">{path.warning}</p></div>
                <details><summary>{path.details}</summary><p>{path.detail}</p>
                  {index === 1 && <CopyText {...content.commandPanel} text={content.setupCommands.join('\n')} />}
                </details>
                <a className="button button--secondary" href={index === 0 ? content.links.codingAgent : content.links.fsqUi} {...externalLinkProps}>{path.action} <Arrow /></a>
              </article>
            ))}
          </div>
          <div className="first-test" aria-labelledby="example-title">
            <h3 id="example-title">{content.example.title}</h3><p>{content.example.introduction}</p>
            <CopyText {...content.example} text={content.example.goal} accessibleName={content.example.label} wrap />
            <p>After the run, inspect the outcome and page evidence. A failed or inconclusive run is a result to investigate, not a reason to assume the checks passed.</p>
          </div>
        </section>
        <section className="section shell" aria-labelledby="capabilities-title">
          <p className="section-label">Beyond the first test</p><h2 id="capabilities-title">{content.capabilities.title}</h2><p className="section-intro">{content.capabilities.introduction}</p>
          <div className="workflow-grid">{content.capabilities.items.map(item => <article className="workflow-step" key={item.title}><h3>{item.title}</h3><p>{item.body}</p></article>)}</div>
        </section>
        <section className="section shell community" aria-labelledby="community-title">
          <h2 id="community-title">{content.community.title}</h2><p className="section-intro">{content.community.body}</p>
          <div className="hero__actions"><a className="button button--secondary" href={content.links.repository} {...externalLinkProps}>Star FSQ on GitHub <Arrow /></a><a className="button button--secondary" href={content.links.feedback} {...externalLinkProps}>Share your experience <Arrow /></a></div>
          <p className="section-intro">{content.community.feedback}</p>
        </section>
        <aside className="boundary shell" aria-labelledby="boundary-title"><h2 id="boundary-title">{content.boundary.title}</h2><p>{content.boundary.body}</p><a href={content.links.stability} {...externalLinkProps}>{content.boundary.action} <Arrow /></a></aside>
      </main>
      <footer className="site-footer shell"><p>{content.footer.credit}</p><nav aria-label={content.footer.navigationLabel}>
        <a href={content.links.gettingStarted} {...externalLinkProps}>{content.footer.gettingStarted}</a><a href={content.links.chineseGuide} {...externalLinkProps}>中文快速开始 <Arrow /></a><a href={content.links.security} {...externalLinkProps}>{content.footer.security}</a><a href={content.links.contributing} {...externalLinkProps}>{content.footer.contributing}</a><a href={content.links.license} {...externalLinkProps}>{content.footer.license}</a>
      </nav></footer>
    </>
  );
}
