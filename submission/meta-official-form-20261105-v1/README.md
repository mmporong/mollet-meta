# Mollet submission preparation

This package follows the authenticated Meta Global AI Developer Hackathon submission form: 21 questions, including 10 required questions. The form was inspected in Playwright and prepared for user review. The final Submit button has not been clicked by the assistant.

- Public source: <https://github.com/mmporong/mollet-meta>
- Public 180-second demo: <https://cdn.jsdelivr.net/gh/mmporong/mollet-meta@44c460d5d82033116642e1a77ed38b738d6aea14/submission/meta-recovery-demo-20261105-v2/mollet-meta-recovery-demo-180s-en-captioned.mp4>
- Official form: <https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit>

## Contents

| File | Purpose |
| --- | --- |
| [answers.json](answers.json) | The three technical answers and their character counts |
| [ANSWERS-ko.md](ANSWERS-ko.md) | Korean factual review of the technical answers |
| [form-fields.json](form-fields.json) | Observed question labels, options and visible limits |
| [form-verification.json](form-verification.json) | Redacted readback of the prepared form |
| [video-verification.json](video-verification.json) | Anonymous browser playback, media headers and SHA-256 |

The technical answers contain 1,629, 1,683 and 1,477 characters respectively, each below the observed 3,000-character limit. They distinguish the two completed historical Meta calls from saved-response replay and from unexecuted video-analysis, reflection and training extensions.

The team and project classification fields were prepared from the project context. The user supplied the two required survey answers, which were entered in the form; those personal answers are omitted from this public package. Optional survey fields and the marketing-consent checkbox were left unselected.

The browser's input values were read back and compared with the prepared draft. All 10 required fields were populated, no `aria-invalid` fields were observed, and the Submit button was enabled. These checks establish readiness for user review; they are not a server acceptance receipt.

## Evidence scope

The demo uses one completed Muse Spark 1.3 response and one completed SAM 3.1 response, with recorded H1 and Go2 clips and source-bound state traces. SAM's 624/625 figure is response coverage, not measured accuracy. The new raw-video inference, reflection, correction and policy-training paths have not produced new model or recovery results for this submission.

Earlier dated submission manifests in this repository preserve earlier preparation states. This directory contains the current official-form mapping and public links. Authentication profiles, browser screenshots, login codes, API keys and private survey values are kept outside the repository.
