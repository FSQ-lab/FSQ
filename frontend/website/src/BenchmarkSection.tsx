import { benchmarkAssetUrl, benchmarkContent as content } from './benchmarkContent';

const externalLinkProps = { target: '_blank', rel: 'noreferrer' } as const;

function Arrow() { return <span aria-hidden="true">↗</span>; }

function LeaderboardComparison() {
  const leaderboard = content.leaderboard;
  return (
    <section className="benchmark-leaderboard" aria-labelledby="leaderboard-title">
      <p className="section-label">Public-code comparison · 13 entries</p>
      <h3 id="leaderboard-title">{leaderboard.title}</h3>
      <p>{leaderboard.scope}</p>
      <p className="leaderboard-protocols">{leaderboard.protocols}</p>
      <div className="leaderboard-plot">
        <div className="leaderboard-axis leaderboard-axis--top" aria-hidden="true"><span>0%</span><span className="leaderboard-axis-reference">80% Human reference</span><span>100%</span></div>
        <ul className="leaderboard-rows" aria-label="Reported results, not official ranks">
          {leaderboard.rows.map(row => (
            <li key={row.name} className={'leaderboard-row' + (row.name === 'FSQ' ? ' leaderboard-row--ours' : row.name === 'Human Performance' ? ' leaderboard-row--reference' : '')}>
              <div className="leaderboard-project"><strong>{row.name}</strong><small>{row.label}</small></div>
              <span className="leaderboard-track" aria-hidden="true"><span style={{ width: row.rate + '%' }} /></span>
              <span className="leaderboard-rate">{row.display}</span>
            </li>
          ))}
        </ul>
        <span className="leaderboard-reference" aria-hidden="true" />
        <div className="leaderboard-axis leaderboard-axis--bottom" aria-hidden="true"><span>0%</span><span className="leaderboard-axis-reference">80% Human reference</span><span>100%</span></div>
      </div>
      <div className="leaderboard-disclosures">
        <p>{leaderboard.releases}</p>
        <p>{leaderboard.metrics}</p>
        <p>{leaderboard.selection}</p>
      </div>
      <nav className="leaderboard-links" aria-label="Comparison evidence">
        <a href={benchmarkAssetUrl('androidworld-leaderboard.png')} {...externalLinkProps}>{leaderboard.imageAction} <Arrow /></a>
        <a href={benchmarkAssetUrl('leaderboard.json')} {...externalLinkProps}>{leaderboard.provenanceAction} <Arrow /></a>
        <a href={content.links.method} {...externalLinkProps}>{leaderboard.methodAction} <Arrow /></a>
      </nav>
    </section>
  );
}

export function BenchmarkSection() {
  return (
    <section className="section shell benchmarks" id="benchmarks" aria-labelledby="benchmarks-title">
      <p className="section-label">{content.label}</p>
      <h2 id="benchmarks-title">{content.title}</h2>
      <p className="section-intro">{content.introduction}</p>
      <div className="benchmark-result">
        <div className="benchmark-result__score">
          <p className="benchmark-result__percent">{content.score}</p>
          <p className="benchmark-result__tasks">{content.result}</p>
          <p className="benchmark-result__validity">{content.validity}</p>
        </div>
        <div className="benchmark-result__identity">
          <p className="section-label">{content.frozenLabel}</p>
          <dl className="benchmark-versions">
            {content.versions.map(version => (
              <div key={version.label}><dt>{version.label}</dt><dd><code>{version.value}</code></dd></div>
            ))}
          </dl>
        </div>
      </div>
      <p className="benchmark-scope">{content.scope}</p>
      <LeaderboardComparison />
      <div className="benchmark-features">
        {content.features.map(feature => (
          <article className="benchmark-feature" key={feature.title}>
            <h3>{feature.title}</h3>
            <p>{feature.body}</p>
            <a href={content.links.method} {...externalLinkProps}>{content.methodAction} <Arrow /></a>
          </article>
        ))}
      </div>
      <p className="benchmark-isolation">{content.isolation}</p>
      <details className="benchmark-details">
        <summary>{content.detailsTitle}</summary>
        <ul>{content.accounting.map(item => <li key={item}>{item}</li>)}</ul>
        {content.limitations.map(item => <p key={item}>{item}</p>)}
      </details>
      <aside className="benchmark-evidence" aria-labelledby="benchmark-evidence-title">
        <div>
          <p className="section-label">{content.evidence.label}</p>
          <h3 id="benchmark-evidence-title">{content.evidence.title}</h3>
          <p>{content.evidence.body}</p>
          <a className="button button--primary" href={content.links.repository} {...externalLinkProps}>{content.evidence.action} <Arrow /></a>
        </div>
        <ul className="benchmark-evidence__links">
          {content.evidence.links.map(link => <li key={link.label}><a href={link.url} {...externalLinkProps}>{link.label} <Arrow /></a></li>)}
          <li><a href={benchmarkAssetUrl('cases.json')} {...externalLinkProps}>{content.evidence.provenanceAction} <Arrow /></a></li>
        </ul>
      </aside>
      <div className="benchmark-cases" aria-labelledby="benchmark-cases-title">
        <h3 id="benchmark-cases-title">{content.casesTitle}</h3>
        <p className="section-intro">{content.casesIntroduction}</p>
        <p className="benchmark-observation-note">{content.observationWarning}</p>
        <ul className="benchmark-grid" aria-label={content.casesTitle}>
          {content.cases.map(item => (
            <li key={item.id}>
              <article className="benchmark-case" aria-labelledby={'benchmark-case-' + item.id}>
                <div className="benchmark-case__copy">
                  <p className="benchmark-case__reward">{content.reward}</p>
                  <h4 id={'benchmark-case-' + item.id}>{item.id} · {item.title}</h4>
                  <p className="benchmark-case__name"><code>{item.name}</code></p>
                  <p className="benchmark-case__highlight">{item.highlight}</p>
                  <p className="benchmark-case__budget">{item.writes} / {item.maximum} {content.writeCountLabel}</p>
                  <p className="benchmark-case__goal-label">{content.originalGoalLabel}</p>
                  <blockquote>{item.goal}</blockquote>
                </div>
                <figure>
                  <a className="benchmark-case__image" href={benchmarkAssetUrl(item.image)} {...externalLinkProps}
                    aria-label={content.imageAccessibleLabel + ' ' + item.id}>
                    <img src={benchmarkAssetUrl(item.image)} alt={item.alt} loading="lazy" decoding="async"
                      width={content.imageWidth} height={content.imageHeight} />
                    <span>{content.imageAction} <Arrow /></span>
                  </a>
                  <figcaption>{content.observationCaption}</figcaption>
                </figure>
              </article>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
