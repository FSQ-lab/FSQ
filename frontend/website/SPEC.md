# Frontend Entry: website

## Purpose

Own the public English FSQ introduction website at `https://fsq-lab.github.io/FSQ/`. The single-page site helps developers, QA engineers, open-source evaluators, and potential contributors understand FSQ's evidence-first alpha workflow and choose either a Coding Agent or the FSQ UI as an equal way to begin.

The Website does not execute FSQ, collect credentials or visitor input, replace project documentation, serve the Control Plane, or own product runtime behavior.

## Dependencies

- The parent frontend workspace owns React, React DOM, TypeScript, Vite, Vitest, Testing Library, the root npm manifest, and the lock file.
- Repository-owned FSQ logo, approved public media, media provenance, and social-preview assets provide local presentation assets.
- Canonical public product statements and setup destinations come from root `SPEC.md`, `README.md`, `docs/getting-started.md`, `docs/platform-prerequisites.md`, `docs/support-and-stability.md`, `SECURITY.md`, and `CONTRIBUTING.md`.
- Browser built-ins provide semantic navigation, native disclosure elements, the optional Clipboard API interaction, and user-initiated HTML video playback with native controls and local caption tracks.
- The Website has no Control Plane API, Python runtime, remote font, analytics, third-party widget, or automatic video-player dependency.
- The Website must not import Control Plane source or another frontend entry's private implementation.

## Public Interface

- `index.html` is the English GitHub Pages entry below the `/FSQ/` project-site base path.
- The canonical URL is `https://fsq-lab.github.io/FSQ/`.
- The page presents FSQ as open-source alpha software for goal-driven real-UI testing that records inspectable evidence. It states that a run may produce a Case, that users review results and any returned Case, and that deterministic replay is a separate user choice.
- The page retains its light white-and-green visual system and the hero headline `Describe a goal. Test the real app.`. Its concise description leads with natural-language goals, real-UI execution and verification, and inspectable screenshots, actions, and results.
- The page contains an approved replaceable demonstration-media area, a three-part workflow explanation with contextualized real screenshots, supported-platform context, two equal setup paths, a shared complete first-test example, progressive capability descriptions, alpha/privacy guidance, and project/documentation links.
- The two setup paths are titled `Coding Agent` and `FSQ UI`. They use the same semantic heading level, typography, visual weight, container treatment, and action prominence. They are alternatives, carry no sequential numbering or progression language, and remain visibly equal when stacked at narrow widths.
- The Coding Agent path links to the authoritative approval-aware Coding Agent setup and states that Provider credentials must not be pasted into Coding Agent chat.
- The FSQ UI path links to the authoritative UI setup, may offer the documented installation and launch commands for copying, and states that Provider and model configuration occurs in the local UI. Copy failure leaves selectable command text and exposes an explicit nearby failure message.
- Both setup paths lead to one Quick Test and result/evidence review. Short entry summaries precede expandable setup details; Python 3.11+, a supported configured Provider and selected model, and platform prerequisites remain visible. Quick Test needs no manual project Workspace setup and does not save a formal Case or perform Strict Replay.
- A shared copyable first-test example searches for `FSQ-lab/FSQ`, follows the repository About website link, and checks both homepage headlines. It distinguishes the Web first-use option from the recorded Android run. Copy feedback is local to the owning control and preserves selectable text on failure.
- Case management and Strict Replay are described as separately chosen advanced workflows. Star and experience-feedback links appear after the trial guidance and navigate to the canonical GitHub repository without authentication widgets, submission, tracking, or automatic starring. A visible link leads to the Chinese getting-started guide.
- Web, Android, Windows, and macOS are presented as supported target platforms subject to platform-specific prerequisites.
- The demonstration initially shows a repository-owned 16:9 real-device/execution poster, an accessible play button, and a duration label. Activating the button replaces the poster in place with native HTML video controls for playback, pause, seeking, and fullscreen where supported. The player uses the approved external GitHub video attachment and same-origin captions; no external video URL is assigned to a loading element before activation, and there is no page-load autoplay or third-party iframe/player library.
- Explicit GitHub-video and YouTube links remain available independently of playback. Loading and playback errors expose nearby status or fallback guidance without hiding those links or the rest of the page. Playback start is claimed only after the browser confirms playback; rejected playback leaves native/manual controls usable.
- Primary project links use `FSQ-lab/FSQ`; `microsoft/FSQ` is not presented as the canonical project.
- Navigation includes `Benchmarks` linking to `#benchmarks`. The AndroidWorld benchmark section follows `How it works` and precedes `Try FSQ`, retaining the warm-white, pale-green, deep-green visual system and the existing Quick Test journey.
- The benchmark result is `95.69% (111 / 116 tasks, pass@1)` with `0 invalid · 0 task retries`. Its frozen experiment identifies FSQ `33113e4`, appium-connect-agent `9d197f1`, AndroidWorld `668267e`, `seed=30`, and client-selected `gpt-6.1-sol`.
- Four evidence-linked explanations cover Single-Model Architecture (Not Multi-Model), Zero App-Specific Skills, Strict Step Budget & True `pass@1`, and Transparent Upstream Grading. Planning, execution, internal verification, and `inspect_with_ai` use the same configured model without a dedicated grounding or verification model. Two generic skills, not app-specific cards or scripts, are loaded. Each task has the `int(10 × complexity)` admitted Harness write-action cap; admitted writes remain charged on failure, denied dispatches consume no admission, reads/assertions are excluded, and composite text entry is one dispatch. Separate task Workspaces load no cross-task knowledge; they share the emulator and host identity. These statements do not claim no benchmark-informed generic guidance or one inference call.
- Grading disclosures identify 113 unchanged upstream tasks and three task-contract corrections (`001`, `014`, `076`), mode B outer-single-settlement, one whole-task attempt, and no whole-task retries. The result is not described as an unmodified-upstream or stock-strict leaderboard score. Method links expose client-only model identity and incomplete historical service birth-anchor audit rather than implying those limitations are resolved.
- Six featured full-reward cases (`013`, `046`, `060`, `027`, `091`, `002`) use the reviewed handoff's WebP observations, original task goals, and admitted/max write counts. A responsive semantic card grid links each screenshot to its full-size same-origin image; captions identify representative observations rather than final-state or standalone grading proof. The playlist duration `46:29` and sports-distance answer `6,510 m` remain evidence-backed case details.
- The evidence repository card links to `https://github.com/FSQ-lab/benchmarks`, English `androidworld/README.md`, Chinese `androidworld/README.zh-CN.md`, `androidworld/METHOD.md`, `androidworld/results.csv`, and Release tag `androidworld-2026-11-19`. It directs readers to all 116 tasks and five non-passing case analyses. Links are normal user-initiated navigation, not runtime API calls; public publication requires the evidence destinations to be available.

## Data And State Flow

- Product copy, navigation destinations, media references, captions, alternative text, and provenance labels are static, typed Website content.
- Media references remain centralized so an approved replacement can change the source and metadata without redesigning the media container or surrounding copy.
- Transient browser state is local to command/example copying and demonstration activation, loading, playback, or failure. Disclosure expansion is native browser state. No state persists between visits.
- The Website stores no visitor state, sets no project-owned cookie, uses no local or session storage, submits no visitor data, and performs no behavioral analytics or tracking.
- Essential fonts, styles, scripts, logos, posters, screenshots, and caption tracks load from the Pages origin. External media is requested only after explicit playback activation; other external destinations are contacted only after explicit navigation. No preconnect, preload, or automatically embedded external player is used.

## Internal Structure

- `index.html`: English document metadata and browser entry.
- `src/main.tsx`: React root creation and global style entry.
- `src/WebsiteApp.tsx`: Semantic single-page composition and user-visible workflows.
- `src/content.ts`: Typed copy, canonical destinations, and replaceable media metadata.
- `src/styles.css`: Light visual system, responsive layout, focus, reduced-motion, and print-safe rules.
- `src/WebsiteApp.test.tsx`: Focused content, hierarchy, navigation, copy-feedback, and no-tracking contract tests.
- `public/benchmarks/`: Reviewed WebP case observations and their `cases.json` provenance, copied from the approved evaluation handoff; assets resolve under the configured `/FSQ/` base path.

## Frontend Architecture

- Architecture level: Level 1 Static Or Minimal Interactive Page.
- Runtime boundary: static browser assets hosted by GitHub Pages; no backend calls or service worker.
- State boundary: copy feedback and video lifecycle state remain local to their owning controls; page content and destinations are static.
- Integration boundary: ordinary links lead to canonical GitHub documentation and user-selected media destinations; the browser video element requests the approved external attachment only after activation.
- Dependency direction: Website source may consume parent-workspace dependencies and repository-owned public assets; it does not import sibling application source or backend implementation.
- Current framework and language: React with TypeScript/TSX and authored CSS, compiled by Vite.

## Error Handling

- Missing or rejected required local assets fail the production build rather than publishing broken references.
- Optional media that cannot render does not hide the value proposition, setup paths, or documentation links; the approved caption and explicit external demonstration link remain usable.
- Clipboard success is reported only after the browser confirms the write. Clipboard unavailability or rejection produces visible manual-copy guidance without hiding selectable commands.
- External destination failure does not become a success-shaped website state or trigger automatic retry.
- GitHub Pages deployment failure is distinct from build success, and publication is not complete until the canonical HTTPS URL serves the intended page and required assets.

## Verification Scope

- Type checking, unit/component tests, and the Website production build run in repository CI on the supported Node.js lines.
- The production artifact resolves HTML, scripts, styles, images, metadata, and canonical links below `/FSQ/`.
- Browser verification covers 320, 768, 1024, and 1440 CSS-pixel widths, 200% zoom/reflow, no page-level horizontal overflow, media containment, and equal presentation of both setup paths.
- Accessibility verification covers English page language, one page-level heading, logical heading order, semantic landmarks, keyboard navigation, visible focus, 44-by-44 CSS-pixel touch targets where applicable, text and control contrast, alternative text, and reduced-motion behavior.
- JavaScript-disabled and media-unavailable checks preserve the value proposition, alpha/privacy warning, both setup paths, setup links, a Chinese guide destination, and an explicit current demonstration link.
- Browser network and storage inspection confirms no project-added tracking, remote font, external video request before activation, third-party player, project-owned cookie, visitor storage, or visitor-data submission. Player checks cover activation, native controls, loading, captions, playback rejection/error fallback, and external viewing links.
- Link verification covers the canonical repository, both setup paths, English and Chinese getting started, prerequisites, support/stability, security, contribution, Star/feedback destinations, both media destinations, and license.
- Published media follows `docs/media/media-acceptance.md`, has reviewed provenance and privacy, and remains understandable without audio.
- Benchmark checks cover exact campaign metrics and versions, disclosure visibility, evidence destinations, six matching case goals/write counts/images, keyboard-accessible full-size image links, narrow-screen reflow, and complete production assets below `/FSQ/`. English and Chinese README benchmark summaries retain equivalent result scope and evidence destinations.

## Current Invariants

- The page is an English-first static introduction site, not an online FSQ execution surface or documentation portal.
- Public claims preserve alpha status, conditional Case generation, separate replay choice, platform prerequisites, local-evidence privacy risk, and configured-Provider input boundaries.
- Coding Agent and FSQ UI remain equal alternative entry paths with consistent headings and actions; order never implies a required sequence.
- No credential field, contact form, account workflow, analytics, personalization, testimonial, pricing, or automatic third-party embed appears. Official benchmarks are permitted only with verified public evidence, frozen experiment identity, material limitations, and complete methodology/report links; benchmark presentation adds no tracking or visitor-data submission.
- Generated Website output remains untracked, deployment-only, and isolated from Control Plane and Python package output.
- Replacing demonstration media requires an approved asset and synchronized source, alternative text, caption, destination, and provenance metadata.
