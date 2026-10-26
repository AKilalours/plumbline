# Model card: home-buyer propensity

| | |
| --- | --- |
| Predicts | the person's household buys a home within 180 days of the scoring date |
| Algorithm | logistic regression with median imputation and missing-value indicators (chosen over gradient boosting on validation: average precision 0.030 vs 0.026) |
| Unit | person; people in one household share the outcome |
| Training cutoffs | 2024-03-31, 2024-06-30, 2024-09-30, 2024-12-31 |
| Test cutoff | 2025-06-30, reported once |
| Labels | the synthetic answer key, standing in for a deed-records feed (in New York, ACRIS) |

## Features

Property (NYC PLUTO at the person's current address): residential units, building class family, year
built, small building, has a unit number. Census: log median household income of the ZIP. Store:
orders and revenue in the last 90 days, days since the last order, average order value. Life events:
moves detected with store or CRM evidence. Work: new work email seen.

**Not used, and why:** vendor income band, age and homeowner flag, and email engagement. They are
empty in every training snapshot: the vendor feed starts in November 2025 and email data in December.
A model that never saw them cannot use them, so they are left out instead of imputed.

## Results on 2025-06-30

AUC 0.659, average precision 0.038 (base rate 0.022), lift 2.3x in the top decile, Brier 0.0212.
Ceiling from the generating rule: AUC 0.806, lift 4.4x. Census ZIP income alone: AUC 0.627.
Without property and Census enrichment: AUC 0.610. With an on-time vendor feed (what-if): AUC 0.735.

Most useful features (drop in average precision when shuffled): ZIP income, small building, average
order value, building size.

## Use and limits

- **Rank, do not read as probabilities.** Purchases rose through 2025; in the top decile the model
  predicts 3.1% where 5.0% bought.
- **Only for people known from the brand's own systems.** Vendor-only people are outside the training
  population and are not scored.
- **Retrain once vendor attributes cover a full outcome window.** Until then the strongest signals
  (income, age, renting) are only seen through ZIP and building proxies.
- **Synthetic data.** The scores show the method works and where it stops; they say nothing about real
  people.
- Identity resolution runs on all records before snapshots are built, so clusters can use records from
  after a cutoff. The features themselves are point in time; the person boundaries are not. A stricter
  backtest would resolve identities per cutoff.

## Guardrails before publishing

Same inputs as training; at least 100 people; scores finite and within [0, 1] and varying; mean within
3x of the training base rate; missing-value rates within 0.25 of training; population stability index
at most 0.25 on every feature. A failed check publishes nothing and is logged in `ml.model_runs`.
