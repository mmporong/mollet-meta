# Mollet English 180-second demo

## 00:00:00,000–00:00:15,000 · Turn desired robot behavior into a testable condition

A reward for climbing stairs does not explain when posture failed. Mollet turns the behavior we want to inspect into a condition with time and numeric thresholds.

## 00:00:15,000–00:00:30,000 · Put visual evidence and state verdicts on one timeline

Object locations are aligned to video frames, while posture conditions are computed from synchronized robot state. Model output and local verdicts keep separate provenance.

## 00:00:30,000–00:00:45,000 · Restore saved results in Chrome

The restore button works without an API key. It restores the H1 source, Spark condition, and SAM boxes while discarding the old approval and verdict.

## 00:00:45,000–00:01:00,000 · SAM boxes cover 624 of 625 frames

The historical record contains one Spark call and one SAM call. Saved SAM boxes are aligned to the H1 timeline; 624 of 625 is response coverage, not tracking accuracy.

## 00:01:00,000–00:01:15,000 · The original Spark condition passes for 0.5 seconds

The restored condition requires up-axis alignment of at least 0.8, an 8-second deadline, and a 0.5-second hold. Recalculation on the current source yields 0.0 to 0.5 seconds.

## 00:01:15,000–00:01:30,000 · A reviewer changes the hold to 8 seconds

Changing the hold time to 8 seconds resets review state. After review, the H1 ascent also passes the full 8-second posture condition.

## 00:01:30,000–00:01:45,000 · Reopen the edited condition without stale approval

The 8-second condition is exported and loaded again. A user-authored condition gains no model authority, and approval returns to unreviewed.

## 00:01:45,000–00:02:00,000 · Apply the same evaluator to a Go2 recording

A manual full-duration 12.48-second posture condition is applied to the Go2 stair descent. It reads recorded state and is not presented as a new model diagnosis.

## 00:02:00,000–00:02:15,000 · Compute the Go2 posture failure from recorded state

Synchronized state shows up-axis alignment falling below the threshold. The verdict comes from robot state, not a new Spark or SAM inference.

## 00:02:15,000–00:02:30,000 · The full-duration Go2 condition fails

The requirement to remain at or above 0.8 for 12.48 seconds fails. This is a manual comparison on saved data, not policy recovery or measured improvement.

## 00:02:30,000–00:02:45,000 · Keep every claim inside the verified scope

This demo verifies saved-response recovery and local reevaluation in the current browser. New video inference, reflection, and training are all zero, and performance improvement is unmeasured.

## 00:02:45,000–00:03:00,000 · Preserve source and verdict in one evidence lineage

Video hash, provider-response hashes, condition, state values, and object boxes remain in one lineage. Mollet reopens saved evidence so the next check can continue.
