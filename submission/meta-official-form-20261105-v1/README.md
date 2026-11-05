# Mollet submission

This package follows the authenticated Meta Global AI Developer Hackathon submission form: 21 questions, including 10 required questions. The project was submitted through the official form. The server accepted the submission with HTTP 200, the event page displayed "Project Submitted" and "View Submission", and the saved submission was reopened to verify all six text fields. See the [submission receipt](execution/receipt.json) and [current status](submission-status.json).

- Public source: <https://github.com/mmporong/mollet-meta>
- Public English 180-second demo: <https://cdn.jsdelivr.net/gh/mmporong/mollet-meta@1b71e2fb2e30b3bad1f84b5e9d9e1eadf9c924f9/submission/meta-english-demo-20261105-v1/mollet-meta-demo-180s-en.mp4>
- Public workbench: <https://mollet-meta.vercel.app>
- Official form: <https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit>

## Contents

| File | Purpose |
| --- | --- |
| [answers.json](answers.json) | The three technical answers and their character counts |
| [ANSWER-REVIEW.md](ANSWER-REVIEW.md) | English factual review of the technical answers |
| [coverage.md](coverage.md) | Important requirements mapped to each answer and evidence level |
| [form-fields.json](form-fields.json) | Observed question labels, options and visible limits |
| [form-verification.json](form-verification.json) | Redacted readback of the prepared form |
| [video-verification.json](video-verification.json) | Anonymous browser playback, media headers and SHA-256 |

The technical answers contain 2,938, 2,875 and 2,844 characters respectively, each below the observed 3,000-character limit. They distinguish the two completed historical Meta calls from saved-response replay and from unexecuted video-analysis, reflection and training extensions. The same submitted project now includes the English-voice video and public workbench URL. The site accepted the update with HTTP 200; both changed fields were reopened and matched. See the [English-media update receipt](execution/english-update-receipt.json).

The team and project classification fields were prepared from the project context. The user supplied the two required survey answers, which were entered in the form; those personal answers are omitted from this public package. The lower survey fields and marketing-consent checkbox were completed before submission. Individual survey answers remain private.

The browser's input values were read back and compared with the prepared draft. All 10 required fields were populated, no `aria-invalid` fields were observed, and the Submit button was enabled. This file preserves the preparation checks; the separate execution receipt records server acceptance and saved-field verification.

## Evidence scope

The demo uses one completed Muse Spark 1.3 response and one completed SAM 3.1 response, with recorded H1 and Go2 clips and source-bound state traces. SAM's 624/625 figure is response coverage, not measured accuracy. The new raw-video inference, reflection, correction and policy-training paths have not produced new model or recovery results for this submission.

Earlier dated submission manifests in this repository preserve earlier preparation states. This directory contains the current official-form mapping and public links. Authentication profiles, browser screenshots, login codes, API keys and private survey values are kept outside the repository.
