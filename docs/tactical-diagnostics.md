# Tactical diagnostics v1

A legal principal variation provides a witness, not a proof of forced causality. The detector compares the best and played lines, distinguishes opportunities missed, allowed and exploited, and exposes structural candidates separately. Confidence is categorical (line_witness / candidate); neither category is a calibrated probability. Material values are pawn units. Phase is a material/move-count heuristic. The labels follow conventional chess terminology and are implemented independently. No third-party detector code is incorporated.

Validation: synthetic positive/negative/ambiguous fixtures cover legal lines, mate, and contrast. The detector deliberately abstains on uncertain causal labels. Precision/recall from an independently annotated representative corpus is not yet available. Candidate-only themes must not be included in competence denominators.

The entire supplied PV is checked for legality; tactical witnesses are limited to the first eight plies. Inconsistent scores with identical root moves do not establish a missed opportunity. Confirmed and candidate-only labels are reported separately.

Run `python -m chessbuilder.tactical_benchmark` to obtain per-theme confusion counts, precision, recall and annotated coverage for structural candidates and witnessed outcomes. The synthetic corpus contains targeted positive, negative and ambiguous cases; missing positive witness support is reported as null recall. It is a regression corpus rather than an independent estimate of real-world accuracy.