# Evaluation Dataset V1

This directory is the frozen V1 benchmark. Ground truth is generated from structured
catalog data and controlled mutations; no LLM labels are used.

- Samples: 500
- Unique catalog records: 420 / 420 (100.00%)
- Dataset SHA-256: `0868111bf882b702738fc82720c2ccf51bbe2776645740a7fc20a93f724af9c7`
- Catalog fingerprint: `dab11bd68087b4771b85ac301aeea601fd539c456cc371140f60d7247c197def`

## Verdicts

- `CONTRADICTED         ` 220
- `INSUFFICIENT_EVIDENCE` 50
- `INVALID_CLAIM        ` 30
- `SUPPORTED            ` 200

## Claim types

- `availability      ` 38
- `discount          ` 38
- `feature_exclusion ` 38
- `feature_inclusion ` 59
- `geo_eligibility   ` 38
- `minimum_purchase  ` 53
- `price             ` 39
- `promotion_dates   ` 38
- `shipping          ` 38
- `subscription_terms` 38
- `trial_duration    ` 38
- `unknown           ` 45

## Difficulty

- `EASY    ` 134
- `HARD    ` 198
- `MODERATE` 168

## Injected mismatches

- `AVAILABILITY_MISMATCH      ` 20
- `DISCOUNT_MISMATCH          ` 20
- `FEATURE_EXCLUSION_MISMATCH ` 20
- `FEATURE_INCLUSION_MISMATCH ` 20
- `MINIMUM_PURCHASE_MISMATCH  ` 20
- `PRICE_MISMATCH             ` 20
- `PROMOTION_END_MISMATCH     ` 20
- `REGION_MISMATCH            ` 20
- `SHIPPING_MISMATCH          ` 20
- `SUBSCRIPTION_PRICE_MISMATCH` 20
- `TRIAL_DURATION_MISMATCH    ` 20

## Freeze policy

Do not rewrite V1 after inspecting system predictions. Proven label, generator, or
corruption fixes require a documented v1.1 or v2 dataset.
