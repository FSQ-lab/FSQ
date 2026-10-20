# Android Quick Test media provenance

## Destinations and source identity

- [GitHub video](https://github.com/user-attachments/assets/3acfecf4-bf48-4c76-8245-2eeef0dedb1f)
- [YouTube](https://youtu.be/f1sobvVHNg0)
- The selected files came from the supplied local `FSQ_video/edit_v2/selected_stills/` asset set. No original video was modified in this repository change.
- The externally stored edited master and upload copy are both H.264/AAC, 1920×1080, 130.708333 seconds as observed with ffprobe. “2:11” is a rounded duration label, not a performance claim.

| Local edit basename (outside Git) | Bytes | SHA-256 |
|---|---:|---|
| FSQ_Android_Quick_Test_EdgeTTS_v2.mp4 | 11250266 | 0584d48fdd24c47ad46f8d99bd32d925b7a8afc280cb083762f515837dd1943b |
| FSQ_Android_Quick_Test_EdgeTTS_v2_GitHub.mp4 | 8018382 | 114f7774c330ce0b0069818ec50e0eb8bea969a00d067b955c8cadb4d88b2792 |

The hashes identify local files; they do not independently prove byte identity of a remote attachment or YouTube transcode. Recording time, export time, source commit, source application revision, browser version, Android version, and capture-host OS are not established. File modification times and the current repository HEAD are not recording provenance.

## Matching recorded Run

The selected report capture's visible Run suffix `7f837a` and exact summary match a local Android Quick Test record. Only the following safe fields are recorded here; full Run files and private paths are not copied into Git:

- Recorded Run start: `2026-10-20T09:24:13.077261Z` (UTC); completion: `2026-10-20T09:26:00.078623Z` (UTC). These are execution timestamps, not capture/export dates.
- Producer: FSQ `0.2.1`, Python `3.14.7`, backend `uiautomator2` `3.5.2`.
- Run outcome: `success`; whole-goal verification: `success`.
- Report summary: “The supplied repository and landing-page UI records support that the About-linked landing page contains both required phrases.”
- Source application and repository revisions: `null`, with reason `not_reported`.
- Local `run.json` SHA-256: `f0b543c07f60bd7b760efc5a1ea2880918d1d696e471976d9efcdee27a187242`.
- Local `execution-result.json` SHA-256: `0e4a61ab7c110bfd297761592b40c6fb41bbec6047618a5081089b0f4d92bebf`.

This matching record supports the recorded run, not current-package availability, cross-platform equivalence, or a successful new run.

## Committed derivatives

| File | Dimensions or cues | Bytes | SHA-256 |
|---|---|---:|---|
| quick-test-task.png | 1800×1040 | 210400 | 0f0921bc31529034ae3cc8b9bd6f408283576328cd9f973e7b8a0cbabc594d12 |
| quick-test-execution.png | 1593×705 | 337216 | fea487cb2eff842c2dc0bd63a2fff9d7c05f8b2e21d60483f0614297f77cab88 |
| quick-test-result.png | 1800×1040 | 391821 | e0efa713da99b63d8d1ec616ee40c182e5d55587bec39534a44b1221d3af0a72 |
| quick-test-poster.png | 1920×1080 | 445322 | 25328dcea127bf2475ad06f02f8404c135220487725555077eacef4cbe968fad |
| quick-test.en.srt | 25 cues | 2197 | f6696ac2a1859de703139a17e5de8654e6408c3edc314512d048115496635a82 |
| quick-test.en.vtt | 25 cues | 2205 | e31616ad9e341420f250bdd8f2258f4e86a3f87ba19a967c7d9842300b8de005 |

- `quick-test-task.png` is a byte-for-byte copy of `revised-01-task.png`: a real goal-entry crop within editorial task framing.
- `quick-test-execution.png` is a byte-for-byte copy of `selected-02-execution.png`: a crop of recorded execution. The exact extraction timestamp and source-crop coordinates are not established.
- `quick-test-result.png` is a byte-for-byte copy of `revised-03-result.png`: actual destination-page and report crops within editorial framing.
- `quick-test-poster.png` is a byte-for-byte copy of `selected-video-poster.png`, a full-frame execution view. The exact frame timestamp is not established.
- The supplied context-card script uses crops `(86,246,1836,914)` from `01-describe-goal.png`, `(133,235,756,907)` from `07-verify-content.png`, and `(91,314,1838,557)` from `03-inspect-report.png`, then resizes within the cards. The editorial text is not product UI or independent test evidence.
- The English SRT is copied unchanged from the supplied v2 edit. WebVTT changes only the header and timestamp decimal separators; all 25 cue texts/times are preserved. Old English/Chinese SRT files remain historical and are not assigned to the new player.
- Existing local edit records identify Edge TTS `7.3.1`, voice `en-US-AndrewMultilingualNeural`, and opening/closing covers with 0.75-second fades. These are edit-record observations, not a newly performed narration or source-recording audit.

## Share image

`docs/assets/social-preview.png` is a 1200×630 editorial brand graphic with white/green styling and the retained headline. It is not product evidence. `frontend/website/public/social-preview.png` is byte-identical and publishes at `https://fsq-lab.github.io/FSQ/social-preview.png` without a runtime network dependency.

SHA-256 for both copies: `46d980ce499f168d14f6cffb5faa2456c8f24d5322254fbb85883d1954c7872b`.

## Review limits

The selected images have been visually inspected for content/layout and are approved as the promotional direction. A complete frame-by-frame video privacy/security review, independent muted-comprehension/caption review, clean public installation, first-use run, and deployed-page verification are not established by these facts. See [acceptance status](media-acceptance.md). No checkbox implies those checks passed.
