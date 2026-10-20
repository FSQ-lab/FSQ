# Current public media acceptance

This record concerns the Android Quick Test edit linked from the current README and Website source. It replaces the older blanket checked assertions. Approval of a proposed asset is distinct from a complete privacy review or publication readiness.

| Area | Status | Evidence or remaining work |
|---|---|---|
| Approved source selection | Established | User supplied the new YouTube destination and accepted the three-image direction; the assigned asset set and destinations are recorded in provenance. |
| Asset identity | Established | SHA-256, dimensions, transforms, and caption identity are recorded in [provenance](quick-test-demo-provenance.md). Large videos remain outside Git. |
| Recorded Run identity | Established for the matching local record | A visible Run suffix and exact report summary match a local Run with FSQ 0.2.1; source revision and recording/export time remain unknown. |
| Explanatory imagery | Established in source | Task/result cards identify their editorial text; real UI captures remain distinct. README and Website supply equivalent task/result prose and alt text. |
| Captions | Prepared; playback verification pending | New-edit English SRT has 25 cues; VTT preserves text and timing. Browser track loading, synchronization, readability, and muted comprehension require final visual verification. No new Chinese subtitle file is available. |
| No automatic external player | Component-tested; real browser pending | Website creates native video only after activation; explicit viewing links survive failure. Standalone demo follows the same pattern without an iframe. |
| Full video privacy review | Unverified | No claim of frame-by-frame inspection, metadata scan, or complete absence of credentials, account/avatar data, paths, device identifiers, private URLs, notifications, or unrelated content is established by this change. Review the entire exported video and linked destinations before broad promotion. |
| Screenshot privacy | Partially inspected, not certified | Selected images have been visually inspected for layout/content; a complete independent privacy/security checklist remains pending. Do not infer clearance from an asset hash or editorial crop. |
| Source version and runtime | Partial | Producer version/backend and Run timestamps are known; source commit, app/browser/OS versions, capture/export time, and complete capture conditions remain unknown. |
| Package availability | Unverified for the promoted workflow | Source metadata, an installed local version, a package-index response, and a GitHub Release are different facts. No clean public installation of the required workflow has been demonstrated by this change. |
| Real first use | Unverified | No clean-environment end-to-end run or three-person new-user comprehension test has been performed for these public materials. |
| Deployment | Pending | A local production build does not deploy the Website. The intended canonical HTTPS page, assets, link destinations, and current package must be verified after authorized publication. |

## Publication checks still required

The release owner must review privacy and accessibility for the entire export, confirm the obtainable package supports the instructions, and exercise the public goal in a clean environment. Observe failures as failures; do not substitute a local source checkout or preconfigured machine for first-use evidence.

Ask at least three people who did not build FSQ to use only the materials. Record whether they can explain FSQ, choose either entry path, satisfy prerequisites, run a Quick Test, inspect evidence, and find feedback/privacy guidance. Record actual times and blockers instead of promising installation or testing within a fixed duration. A repeated comprehension or setup failure needs a documented repair before promotion.

Tech Lead and the user own publication decisions. Outstanding media privacy, installation, first-use, deployment, or canonical-page evidence remains explicit; unit tests and a build do not satisfy those checks.
