# Mollet public evidence demo

This Vercel package serves the saved-response Mollet workbench without credentials or provider calls. It exposes the six recorded simulation clips, source-bound observations, saved Spark and SAM evidence, contract validation, local review, and deterministic evaluation.

The public package rejects model, account, hypothesis-analysis, correction, training, and recovery endpoints. Video routes redirect to immutable commit-pinned CDN assets whose SHA-256 values are recorded in `data/media.json`.

Build from the repository root:

```powershell
python tools/build_meta_public_demo.py
python deployment/meta-public-demo/api/index.py 8795
```

Deploy with the Vercel project root set to `deployment/meta-public-demo`.
