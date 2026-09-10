# Evaluation V1 Human Audit

Stratified examples: 40

## eval-000001 — SUPPORTED

- Claim: Brightpath Brewhouse SE costs $530.49.
- Reference: prod-brightpath-brewhouse-se
- Claim type: price
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: 530.49
- Mutation: none
- Ground truth: Reference price value 530.49 satisfies the claim.

## eval-000002 — CONTRADICTED

- Claim: offer CLEA549 ends on July 17, 2026.
- Reference: offer-clearance-event-clea549
- Claim type: promotion_dates
- Difficulty: HARD
- As of: 2026-06-09
- Relevant source value: {'offer_start': datetime.date(2026, 6, 10), 'offer_end': datetime.date(2026, 7, 10)}
- Mutation: {"type":"PROMOTION_END_MISMATCH","source_value":"2026-07-10","mutated_value":"2026-07-17","magnitude":"moderate"}
- Ground truth: Injected PROMOTION_END_MISMATCH: reference value 2026-07-10; claim value 2026-07-17.

## eval-000003 — CONTRADICTED

- Claim: offer SEAS219 gives up to 9% off.
- Reference: offer-seasonal-sale-seas219
- Claim type: discount
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: 10.0
- Mutation: {"type":"DISCOUNT_MISMATCH","source_value":10.0,"mutated_value":9.0,"magnitude":"easy"}
- Ground truth: Injected DISCOUNT_MISMATCH: reference value 10.0; claim value 9.0.

## eval-000004 — SUPPORTED

- Claim: The promotion for Lumen Clack Plus runs until September 9.
- Reference: prod-lumen-clack-plus
- Claim type: promotion_dates
- Difficulty: HARD
- As of: 2026-09-09
- Relevant source value: {'offer_start': datetime.date(2026, 9, 2), 'offer_end': datetime.date(2026, 9, 9)}
- Mutation: none
- Ground truth: Reference promotion_dates value 2026-09-09 satisfies the claim.

## eval-000005 — SUPPORTED

- Claim: Meridian Analytics Starter plan costs $18.95 per month.
- Reference: plan-meridian-analytics-starter
- Claim type: subscription_terms
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: 18.95
- Mutation: none
- Ground truth: Reference subscription_terms value 18.95 satisfies the claim.

## eval-000006 — CONTRADICTED

- Claim: The listed price for Kestrel Frame is $2135.45.
- Reference: prod-kestrel-frame
- Claim type: price
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: 2134.95
- Mutation: {"type":"PRICE_MISMATCH","source_value":"2134.95","mutated_value":"2135.45","magnitude":"subtle"}
- Ground truth: Injected PRICE_MISMATCH: reference value 2134.95; claim value 2135.45.

## eval-000007 — CONTRADICTED

- Claim: Trellis Docs Pro plan costs $914.90 per month.
- Reference: plan-trellis-docs-pro
- Claim type: subscription_terms
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: 814.90
- Mutation: {"type":"SUBSCRIPTION_PRICE_MISMATCH","source_value":"814.90","mutated_value":"914.90","magnitude":"easy"}
- Ground truth: Injected SUBSCRIPTION_PRICE_MISMATCH: reference value 814.90; claim value 914.90.

## eval-000010 — CONTRADICTED

- Claim: Customers get wall mount kit with Northwind Hearth SE.
- Reference: prod-northwind-hearth-se
- Claim type: feature_inclusion
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: ('battery_powered', 'multi_room_audio', 'room_calibration', 'stereo_pairing', 'voice_assistant')
- Mutation: {"type":"FEATURE_INCLUSION_MISMATCH","source_value":"stereo_pairing","mutated_value":"wall_mount_kit","magnitude":"subtle"}
- Ground truth: Injected FEATURE_INCLUSION_MISMATCH: reference value stereo_pairing; claim value wall_mount_kit.

## eval-000011 — INVALID_CLAIM

- Claim: Buy Volterra Payments Business plan today!
- Reference: plan-volterra-payments-business
- Claim type: unknown
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: field absent from catalog schema
- Mutation: none
- Ground truth: Marketing language contains no testable commercial proposition.

## eval-000013 — SUPPORTED

- Claim: The minimum order for offer LOYA778 is $150.00.
- Reference: offer-loyalty-offer-loya778
- Claim type: minimum_purchase
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: 150.00
- Mutation: none
- Ground truth: Reference minimum_purchase value 150.00 satisfies the claim.

## eval-000017 — SUPPORTED

- Claim: Northwind Vector Lite applies to the FR region.
- Reference: prod-northwind-vector-lite
- Claim type: geo_eligibility
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: ('GB', 'DE', 'FR')
- Mutation: none
- Ground truth: Reference geo_eligibility value FR satisfies the claim.

## eval-000018 — SUPPORTED

- Claim: offer BACK701 gives 20% off.
- Reference: offer-back-to-school-back701
- Claim type: discount
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: 20.0
- Mutation: none
- Ground truth: Reference discount value 20.0 satisfies the claim.

## eval-000019 — CONTRADICTED

- Claim: Zephyr Tempo Air is preorder.
- Reference: prod-zephyr-tempo-air
- Claim type: availability
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: out_of_stock
- Mutation: {"type":"AVAILABILITY_MISMATCH","source_value":"out_of_stock","mutated_value":"preorder","magnitude":"easy"}
- Ground truth: Injected AVAILABILITY_MISMATCH: reference value out_of_stock; claim value preorder.

## eval-000020 — SUPPORTED

- Claim: No shipping charge applies to Cobalt Orbit Ultra.
- Reference: prod-cobalt-orbit-ultra
- Claim type: shipping
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: True
- Mutation: none
- Ground truth: Reference shipping value True satisfies the claim.

## eval-000021 — INSUFFICIENT_EVIDENCE

- Claim: Northwind Sweep Pro 2 includes a 3-year warranty.
- Reference: prod-northwind-sweep-pro-2
- Claim type: unknown
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: field absent from catalog schema
- Mutation: none
- Ground truth: The catalog schema and known record provide no warranty evidence.

## eval-000022 — SUPPORTED

- Claim: Cobalt Observability Enterprise plan includes a 30-day free trial.
- Reference: plan-cobalt-observability-enterprise
- Claim type: trial_duration
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: 30
- Mutation: none
- Ground truth: Reference trial_duration value 30 satisfies the claim.

## eval-000024 — CONTRADICTED

- Claim: You must spend at least $50.50 to use Northwind Hearth SE.
- Reference: prod-northwind-hearth-se
- Claim type: minimum_purchase
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: 50.00
- Mutation: {"type":"MINIMUM_PURCHASE_MISMATCH","source_value":"50.00","mutated_value":"50.50","magnitude":"subtle"}
- Ground truth: Injected MINIMUM_PURCHASE_MISMATCH: reference value 50.00; claim value 50.50.

## eval-000026 — CONTRADICTED

- Claim: Customers in Germany are eligible for Aurex Vortex SE.
- Reference: prod-aurex-vortex-se
- Claim type: geo_eligibility
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: ('US', 'CA', 'GB')
- Mutation: {"type":"REGION_MISMATCH","source_value":"CA","mutated_value":"DE","magnitude":"moderate"}
- Ground truth: Injected REGION_MISMATCH: reference value CA; claim value DE.

## eval-000027 — INSUFFICIENT_EVIDENCE

- Claim: Customers get installation service with Kestrel Keystone Air.
- Reference: prod-kestrel-keystone-air
- Claim type: feature_inclusion
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: ('hot_swappable_switches', 'programmable_macros', 'rgb_backlight', 'wireless_bluetooth')
- Mutation: none
- Ground truth: Feature installation service is neither explicitly included nor excluded.

## eval-000032 — CONTRADICTED

- Claim: You can try Kestrel Workflow Standard plan free for 37 days.
- Reference: plan-kestrel-workflow-standard
- Claim type: trial_duration
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: 30
- Mutation: {"type":"TRIAL_DURATION_MISMATCH","source_value":30,"mutated_value":37,"magnitude":"moderate"}
- Ground truth: Injected TRIAL_DURATION_MISMATCH: reference value 30; claim value 37.

## eval-000033 — SUPPORTED

- Claim: Halcyon Clack 2 is in stock.
- Reference: prod-halcyon-clack-2
- Claim type: availability
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: in_stock
- Mutation: none
- Ground truth: Reference availability value in_stock satisfies the claim.

## eval-000035 — INSUFFICIENT_EVIDENCE

- Claim: The minimum order for Fernwood Tactile Pro is $32.00.
- Reference: prod-fernwood-tactile-pro
- Claim type: minimum_purchase
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: None
- Mutation: none
- Ground truth: The known reference record has no minimum-purchase field.

## eval-000037 — CONTRADICTED

- Claim: Kestrel Workflow Premium plan does not include advanced analytics.
- Reference: plan-kestrel-workflow-premium
- Claim type: feature_exclusion
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: ('custom_contracts', 'on_premise_option')
- Mutation: {"type":"FEATURE_EXCLUSION_MISMATCH","source_value":"custom_contracts","mutated_value":"advanced_analytics","magnitude":"easy"}
- Ground truth: Injected FEATURE_EXCLUSION_MISMATCH: reference value custom_contracts; claim value advanced_analytics.

## eval-000048 — SUPPORTED

- Claim: offer LAUN194 does not include 128 gb storage.
- Reference: offer-launch-discount-laun194
- Claim type: feature_exclusion
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: ('128_gb_storage',)
- Mutation: none
- Ground truth: Reference feature_exclusion value 128_gb_storage satisfies the claim.

## eval-000065 — CONTRADICTED

- Claim: A shipping charge applies to Brightpath Keystone Pro.
- Reference: prod-brightpath-keystone-pro
- Claim type: shipping
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: True
- Mutation: {"type":"SHIPPING_MISMATCH","source_value":true,"mutated_value":false,"magnitude":"subtle"}
- Ground truth: Injected SHIPPING_MISMATCH: reference value True; claim value False.

## eval-000078 — SUPPORTED

- Claim: Customers get usb c charging with Zephyr Echo 2.
- Reference: prod-zephyr-echo-2
- Claim type: feature_inclusion
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: ('active_noise_cancellation', 'transparency_mode', 'usb_c_charging')
- Mutation: none
- Ground truth: Reference feature_inclusion value usb_c_charging satisfies the claim.

## eval-000008 — CONTRADICTED

- Claim: Meridian Tidy SE ends on August 16, 2026.
- Reference: prod-meridian-tidy-se
- Claim type: promotion_dates
- Difficulty: HARD
- As of: 2026-06-25
- Relevant source value: {'offer_start': datetime.date(2026, 6, 25), 'offer_end': datetime.date(2026, 8, 9)}
- Mutation: {"type":"PROMOTION_END_MISMATCH","source_value":"2026-08-09","mutated_value":"2026-08-16","magnitude":"moderate"}
- Ground truth: Injected PROMOTION_END_MISMATCH: reference value 2026-08-09; claim value 2026-08-16.

## eval-000009 — SUPPORTED

- Claim: Northwind Canvas Lite starts at $471.00.
- Reference: prod-northwind-canvas-lite
- Claim type: price
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: 481.00
- Mutation: none
- Ground truth: Reference price value 481.00 satisfies the claim.

## eval-000012 — CONTRADICTED

- Claim: Kestrel Slab Max includes headphone jack.
- Reference: prod-kestrel-slab-max
- Claim type: feature_inclusion
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: ('128_gb_storage', '256_gb_storage', 'cellular_option', 'laminated_display')
- Mutation: {"type":"FEATURE_INCLUSION_MISMATCH","source_value":"laminated_display","mutated_value":"headphone_jack","magnitude":"easy"}
- Ground truth: Injected FEATURE_INCLUSION_MISMATCH: reference value laminated_display; claim value headphone_jack.

## eval-000014 — CONTRADICTED

- Claim: Solstice Support Premium plan is billed monthly at $283.49.
- Reference: plan-solstice-support-premium
- Claim type: subscription_terms
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: 282.99
- Mutation: {"type":"SUBSCRIPTION_PRICE_MISMATCH","source_value":"282.99","mutated_value":"283.49","magnitude":"subtle"}
- Ground truth: Injected SUBSCRIPTION_PRICE_MISMATCH: reference value 282.99; claim value 283.49.

## eval-000015 — CONTRADICTED

- Claim: Aurex Level Plus is priced at $1498.00.
- Reference: prod-aurex-level-plus
- Claim type: price
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: 1478.00
- Mutation: {"type":"PRICE_MISMATCH","source_value":"1478.00","mutated_value":"1498.00","magnitude":"moderate"}
- Ground truth: Injected PRICE_MISMATCH: reference value 1478.00; claim value 1498.00.

## eval-000016 — CONTRADICTED

- Claim: The promotion for offer SEAS104 runs until September 27.
- Reference: offer-seasonal-sale-seas104
- Claim type: promotion_dates
- Difficulty: EASY
- As of: 2026-09-27
- Relevant source value: {'offer_start': datetime.date(2026, 8, 3), 'offer_end': datetime.date(2026, 9, 26)}
- Mutation: {"type":"PROMOTION_END_MISMATCH","source_value":"2026-09-26","mutated_value":"2026-09-27","magnitude":"subtle"}
- Ground truth: Injected PROMOTION_END_MISMATCH: reference value 2026-09-26; claim value 2026-09-27.

## eval-000023 — CONTRADICTED

- Claim: Volterra Horizon Pro gives at least 35% off.
- Reference: prod-volterra-horizon-pro
- Claim type: discount
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: 15.0
- Mutation: {"type":"DISCOUNT_MISMATCH","source_value":15.0,"mutated_value":35.0,"magnitude":"easy"}
- Ground truth: Injected DISCOUNT_MISMATCH: reference value 15.0; claim value 35.0.

## eval-000025 — INVALID_CLAIM

- Claim: Premium quality with Meridian Atlas Pro.
- Reference: prod-meridian-atlas-pro
- Claim type: unknown
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: field absent from catalog schema
- Mutation: none
- Ground truth: Marketing language contains no testable commercial proposition.

## eval-000028 — SUPPORTED

- Claim: The free trial for Northwind CRM Starter plan lasts 14 days.
- Reference: plan-northwind-crm-starter
- Claim type: trial_duration
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: 14
- Mutation: none
- Ground truth: Reference trial_duration value 14 satisfies the claim.

## eval-000029 — SUPPORTED

- Claim: A 15 percent discount applies to offer SEAS918.
- Reference: offer-seasonal-sale-seas918
- Claim type: discount
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: 15.0
- Mutation: none
- Ground truth: Reference discount value 15.0 satisfies the claim.

## eval-000030 — SUPPORTED

- Claim: offer FLAS585 does not include free delivery.
- Reference: offer-flash-deal-flas585
- Claim type: shipping
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: False
- Mutation: none
- Ground truth: Reference shipping value False satisfies the claim.

## eval-000031 — CONTRADICTED

- Claim: A 61 percent discount applies to offer LAUN819.
- Reference: offer-launch-discount-laun819
- Claim type: discount
- Difficulty: EASY
- As of: 2026-09-15
- Relevant source value: 60.0
- Mutation: {"type":"DISCOUNT_MISMATCH","source_value":60.0,"mutated_value":61.0,"magnitude":"subtle"}
- Ground truth: Injected DISCOUNT_MISMATCH: reference value 60.0; claim value 61.0.

## eval-000034 — CONTRADICTED

- Claim: The current availability for Brightpath Tidy 2 is out of stock.
- Reference: prod-brightpath-tidy-2
- Claim type: availability
- Difficulty: HARD
- As of: 2026-09-15
- Relevant source value: limited_stock
- Mutation: {"type":"AVAILABILITY_MISMATCH","source_value":"limited_stock","mutated_value":"out_of_stock","magnitude":"moderate"}
- Ground truth: Injected AVAILABILITY_MISMATCH: reference value limited_stock; claim value out_of_stock.

## eval-000036 — SUPPORTED

- Claim: Customers in the United States are eligible for Volterra Slab Pro.
- Reference: prod-volterra-slab-pro
- Claim type: geo_eligibility
- Difficulty: MODERATE
- As of: 2026-09-15
- Relevant source value: ('US', 'CA')
- Mutation: none
- Ground truth: Reference geo_eligibility value US satisfies the claim.
