# Mollet submission answer review

The saved official form contains a 2,938-character project description, a 2,875-character Meta-products answer, and a 2,844-character model-rationale answer. Each remains within its 3,000-character limit. The description links the public repository and workbench.

## Verified workflow

Mollet converts a behavior requirement into an editable contract, aligns it with recorded video and state, and reports PASS, FAIL, or UNKNOWN with a supporting interval. Editing resets review. Export and import preserve values while requiring fresh review.

Six Isaac Lab stair recordings cover H1 biped ascent/descent and Go2 quadruped ascent/descent at two command speeds. Four have synchronized time series; two faster Go2 recordings are summary-only. The app does not invent missing state.

The saved H1 provider condition uses upright alignment of at least 0.8, an eight-second start deadline, and a 0.5-second hold. It passes over 0.0–0.5 seconds. A user-edited eight-second hold passes over 0.0–8.0 seconds. A manual full-duration 12.48-second Go2 descent condition fails. These are recorded simulation checks, not physical-robot trials or new policy results.

## Meta evidence

One historical authenticated Muse Spark 1.3 call generated a contract from natural language and state JSON; this call did not inspect raw video. One authenticated SAM 3.1 call returned boxes for 624 of 625 H1 samples. This is response coverage, not tracking accuracy.

Saved replay preserves provenance without another provider call. The public app uses repository evidence verification; private raw provider files are not deployed. The corrected video has English UI, narration, and captions.

## Extension and scope

The code supports an original MP4 with synchronized state, one to four source-frame-cited hypotheses, and at most three untested conditions checked on the same observation. Reflection receives verdict, reason, interval, trigger, hashes, and uncertainty. These are proxies on stored data, not new experiments or causal confirmation.

Reflection cannot confirm a probe. An independently confirmed triage probe must match its case and registered correction before a proposal enters `pending_human_approval`, with GPU budget zero. The fixed recovery oracle or triplet comparison grants no execution approval.

The extension has code and synthetic-response tests. New video inference, real reflection, reward changes, retraining, GPU work, and measured recovery improvement remain zero. Existing RL diagnosis and stair assets are reused work; Meta authoring, review, evaluation, replay, provenance, and extension interfaces are the hackathon contribution.

## Validation

- [English browser capture](../../evals/results/meta_english_browser_20261105_v1/verification.json): 11 checks, three viewport widths, no visible Korean text or horizontal overflow.
- [Production flow](../../evals/results/meta_public_deployment_20261105_v1/full-flow-verification.json): replay, edited hold, review reset, export/import, and Go2 FAIL.
- [English video manifest](../meta-english-demo-20261105-v1/manifest.json): 180 seconds, twelve voiced scenes, H.264/AAC, full decode success.
- [Update receipt](execution/english-update-receipt.json): same submission ID, HTTP 200, two changed fields matched.

The related suite passed 142 tests after the English UI changes. No model-superiority, generalization, tracking-accuracy, time-saving, purchasing, or recovery-improvement claim is made. Credentials, personal survey answers, and authenticated screenshots remain private.
