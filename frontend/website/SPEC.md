# Frontend Entry: website

## Purpose

Own the public English FSQ introduction website at `https://fsq-lab.github.io/FSQ/`. The single-page site helps developers, QA engineers, open-source evaluators, and potential contributors understand FSQ's evidence-first alpha workflow and choose either a Coding Agent or the FSQ UI as an equal way to begin.

The Website does not execute FSQ, collect credentials or visitor input, replace project documentation, serve the Control Plane, or own product runtime behavior.

## Dependencies

- The parent frontend workspace owns React, React DOM, TypeScript, Vite, Vitest, Testing Library, the root npm manifest, and the lock file.
- Repository-owned FSQ logo, approved public media, media provenance, and social-preview assets provide local presentation assets.
- Canonical public product statements and setup destinations come from root `SPEC.md`, `README.md`, `docs/getting-started.md`, `docs/platform-prerequisites.md`, `docs/support-and-stability.md`, `SECURITY.md`, and `CONTRIBUTING.md`.
- Browser built-ins provide semantic navigation and the optional Clipboard API interaction.
- The Website has no Control Plane API, Python runtime, remote font, analytics, third-party widget, or automatic video-player dependency.
- The Website must not import Control Plane source or another frontend entry's private implementation.

## Public Interface

- `index.html` is the English GitHub Pages entry below the `/FSQ/` project-site base path.
- The canonical URL is `https://fsq-lab.github.io/FSQ/`.
- The page presents FSQ as open-source alpha software for goal-driven real-UI testing that records inspectable evidence. It states that a run may produce a Case, that users review results and any returned Case, and that deterministic replay is a separate user choice.
- The page contains a concise hero, an approved replaceable demonstration-media area, a three-part workflow explanation, supported-platform context, two equal setup paths, alpha/privacy guidance, and project/documentation links.
- The two setup paths are titled `Coding Agent` and `FSQ UI`. They use the same semantic heading level, typography, visual weight, container treatment, and action prominence. They are alternatives, carry no sequential numbering or progression language, and remain visibly equal when stacked at narrow widths.
- The Coding Agent path links to the authoritative approval-aware Coding Agent setup and states that Provider credentials must not be pasted into Coding Agent chat.
- The FSQ UI path links to the authoritative UI setup, may offer the documented installation and launch commands for copying, and states that Provider and model configuration occurs in the local UI. Copy failure leaves selectable command text and exposes an explicit nearby failure message.
- Web, Android, Windows, and macOS are presented as supported target platforms subject to platform-specific prerequisites.
- Media playback or an external video destination begins only after an explicit user action. The page does not preload a third-party player.
- Primary project links use `FSQ-lab/FSQ`; `microsoft/FSQ` is not presented as the canonical project.

## Data And State Flow

- Product copy, navigation destinations, media references, captions, alternative text, and provenance labels are static, typed Website content.
- Media references remain centralized so an approved replacement can change the source and metadata without redesigning the media container or surrounding copy.
- The only transient browser state is command-copy success or failure feedback owned by the FSQ UI path.
- The Website stores no visitor state, sets no project-owned cookie, uses no local or session storage, submits no visitor data, and performs no behavioral analytics or tracking.
- Essential fonts, styles, scripts, logos, and rendered media load from the Pages origin. External destinations are contacted only after explicit navigation.

## Internal Structure

- `index.html`: English document metadata and browser entry.
- `src/main.tsx`: React root creation and global style entry.
- `src/WebsiteApp.tsx`: Semantic single-page composition and user-visible workflows.
- `src/content.ts`: Typed copy, canonical destinations, and replaceable media metadata.
- `src/styles.css`: Light visual system, responsive layout, focus, reduced-motion, and print-safe rules.
- `src/WebsiteApp.test.tsx`: Focused content, hierarchy, navigation, copy-feedback, and no-tracking contract tests.

## Frontend Architecture

- Architecture level: Level 1 Static Or Minimal Interactive Page.
- Runtime boundary: static browser assets hosted by GitHub Pages; no backend calls or service worker.
- State boundary: copy feedback remains local to the control that owns it; all other content is static.
- Integration boundary: ordinary links lead to canonical GitHub documentation and user-selected media destinations.
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
- JavaScript-disabled and media-unavailable checks preserve the value proposition, alpha/privacy warning, both setup paths, and setup links.
- Browser network and storage inspection confirms no project-added tracking, remote font, automatic video-player load, cookie, visitor storage, or visitor-data submission.
- Link verification covers the canonical repository, both setup paths, getting started, prerequisites, support/stability, security, contribution, media destination, and license.
- Published media follows `docs/media/media-acceptance.md`, has reviewed provenance and privacy, and remains understandable without audio.

## Current Invariants

- The page is an English-first static introduction site, not an online FSQ execution surface or documentation portal.
- Public claims preserve alpha status, conditional Case generation, separate replay choice, platform prerequisites, local-evidence privacy risk, and configured-Provider input boundaries.
- Coding Agent and FSQ UI remain equal alternative entry paths with consistent headings and actions; order never implies a required sequence.
- No credential field, contact form, account workflow, analytics, personalization, testimonial, benchmark, pricing, or automatic third-party embed appears.
- Generated Website output remains untracked, deployment-only, and isolated from Control Plane and Python package output.
- Replacing demonstration media requires an approved asset and synchronized source, alternative text, caption, destination, and provenance metadata.
