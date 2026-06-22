# Watcher Data Cleanup Strategy

## Problem

The watcher pipeline is collecting useful movement-derived accounts and terms, but some suggested terms are too generic for X stream rules.

The cleanup goal is not to decide whether a term "looks good." The goal is to estimate whether a rule produces measurable value.

The watcher should move from:

```text
did the model generate a plausible account or term?
```

to:

```text
did this account or term produce useful, early, cost-effective signal?
```

## Objective Function

The first-principles objective is:

```text
detect actionable project information before the market fully prices it in
```

A watcher rule is valuable when it:

1. Fires early.
2. Fires accurately.
3. Produces unique information.
4. Produces enough value to justify monitoring cost.

The simplest mature metric is:

```text
Rule Value =
    Useful Signals Produced
    -----------------------
    Monitoring Cost
```

Everything else is a proxy until there is enough outcome data.

## Target Pipeline

The pipeline should become:

```text
movement signal
-> candidate accounts and terms
-> cleanup / scoring / review
-> active watcher rules
-> X stream ingest
-> outcome tracking
-> rule/account/term score updates
```

## 1. Heuristics Are Priors

Rule-based cleanup is still useful, but it should be treated as bootstrap priors, not permanent truth.

Examples:

```text
official account      -> higher prior
unknown account       -> lower prior
cashtag               -> higher prior
generic hype phrase   -> lower prior
```

These priors help before enough data exists. They should eventually be outweighed by observed performance.

## 2. Rule-Based Cleanup

Start with deterministic cleanup before using models. This reduces obvious noise and protects X credits while the system has little history.

Drop generic terms such as:

```text
moonshot
kol post
community meme
team update
app launch
narrative
narrative rotation
insane breakout
#1 on gainers
article timing
vote activity
pump
ath
alpha
```

Keep token/project-specific terms such as:

```text
$VELVET
velvet x app
pre ipo trading
opengradient
cbventures
zkml infrastructure
$JOTCHUA
jotchuans
```

## 3. Term Specificity Scoring

Give each candidate term an initial score. This is a prior estimate of whether the term will produce useful signal.

Example scoring:

```text
contract address           +5
cashtag, e.g. $VELVET      +5
official project handle    +4
unique project phrase      +3
token symbol               +3
generic trading word       -3
common hype phrase         -4
```

Example policy:

```text
score >= 3  -> keep
score 1-2   -> pending / review
score <= 0  -> drop
```

## 4. Account Quality Scoring

Score accounts separately from terms. This is also a prior until there is enough history.

Useful account signals:

- account was cited in a source reference
- account is official or team-adjacent
- account posted before or during the movement
- account mentioned the token directly
- account has prior useful watcher hits
- account has low false-positive history

Possible statuses:

```text
pending   -> stored, not synced to X
active    -> synced to X
rejected  -> ignored unless manually restored
disabled  -> previously active, now paused
```

Only `active` accounts should produce X rules.

## 5. Pending / Active / Rejected Workflow

New model-suggested accounts should default to:

```json
{
  "status": "pending"
}
```

This prevents noisy accounts from becoming live X rules automatically.

Human or scripted review can later promote selected accounts:

```json
{
  "status": "active"
}
```

## 6. Outcome Tracking

Outcome tracking is the core data science layer. For every captured watcher tweet, track:

```text
tweet id
tweet time
token
matched account
matched terms
matched rule id / tag
price at detection
price after 1h
price after 6h
price after 24h
lead time versus movement event
published lore? yes/no
useful? yes/no
review label: useful / maybe / noise
estimated API cost
estimated review cost
```

This enables performance metrics:

```text
precision = useful hits / total hits
false positive rate
average return after 1h / 6h / 24h
average lead time
cost per useful signal
```

Outcome tracking is what lets the watcher improve over time.

## 7. Signal-To-Noise Ratio

Measure rules by signal-to-noise:

```text
SNR =
  useful_matches
  --------------
  noisy_matches
```

Example:

```text
Rule A: 40 useful, 10 noisy -> SNR 4.0
Rule B: 100 useful, 200 noisy -> SNR 0.5
```

Rule A is better despite fewer total useful matches because it wastes less attention and cost.

## 8. Precision, Recall, And Cost

Precision:

```text
useful_matches / all_matches
```

Recall:

```text
useful_matches_found / all_useful_matches
```

Early versions should optimize precision first because noisy rules burn X credits and analyst attention. Later, measure recall with replay datasets and missed-signal analysis.

Cost matters:

```text
efficiency =
  useful_matches / monitoring_cost
```

Monitoring cost includes:

- X API usage
- storage
- model review
- human review
- downstream alert fatigue

## 9. Information Gain

A rule is more valuable when it reduces uncertainty.

Low information gain:

```text
project name
already pumped
chart update
generic hype
```

High information gain:

```text
new partnership
new listing
new product launch
pre-IPO market narrative
token utility launch
team or investor connection
```

Track an `information_gain_score` during review. It can start as human/model-labeled and later be learned from outcomes.

## 10. Bayesian Updating

Each account, term, and rule can be modeled as having a probability of producing useful signal.

Start with priors:

```text
official account    -> higher prior
unknown account     -> lower prior
generic term        -> lower prior
cashtag             -> higher prior
```

Then update with observations:

```text
useful hit -> increase belief
noise hit  -> decrease belief
```

Example stored summary:

```json
{
  "account": "@VegaSnipes",
  "prior": 0.5,
  "observations": 40,
  "successful": 28,
  "posterior": 0.69
}
```

This is stronger than arbitrary scores because the score has statistical meaning.

## 11. Mature Rule Score

The long-term formula should move toward:

```text
rule_score =
  P(useful_signal)
  * expected_information_gain
  * expected_timeliness
  / monitoring_cost
```

Where:

```text
P(useful_signal)              comes from observed history
expected_information_gain     comes from novelty/usefulness labels
expected_timeliness           comes from lead-time history
monitoring_cost               comes from match volume, API cost, and review cost
```

## 12. Clustering / Similarity

Cluster similar terms and tweets to reduce duplicate rules.

Examples:

```text
pre ipo trading
pre-IPO narrative
private-market narrative
SpaceX / OpenAI private market
```

These may be treated as one theme instead of separate loose terms.

## 13. Grok Cleanup

Use Grok as a reviewer, not as the direct rule writer.

Input:

```text
movement event
token symbol and address
referenced tweets
candidate accounts
candidate terms
```

Output should use strict JSON:

```json
{
  "account": "@example",
  "recommended_status": "pending",
  "keep_terms": ["$token", "project phrase"],
  "drop_terms": ["generic hype phrase"],
  "reason": "short explanation",
  "confidence": 0.82,
  "risk_flags": ["generic_terms"]
}
```

After Grok responds, still run deterministic validation:

- no denied generic terms
- valid account handles only
- max terms per account
- require token-specific or project-specific terms
- do not directly mutate live X rules

## 14. RAG Later

RAG becomes useful after there is enough watcher history.

For a new account, term, or tweet, retrieve similar past examples:

```text
same account history
similar terms
similar tweets
past outcomes
accepted / rejected lore decisions
```

Then ask Grok to judge with that context.

RAG is useful for the learning loop, but it should come after deterministic cleanup and outcome tracking.

## Recommended Implementation Order

1. Define the outcome labels: `useful`, `maybe`, `noise`.
2. Keep new accounts `pending` by default.
3. Add a generic-term denylist to prevent obvious noisy rules.
4. Add term and account prior scoring.
5. Add verified watcher-hit processing before Discord alerts.
6. Add outcome tracking from captured watcher tweets.
8. Compute precision, SNR, cost per useful signal, and lead time.
9. Add Bayesian account/term/rule updates after enough observations.
10. Add Grok reviewer for ambiguous candidates.
11. Add RAG once enough historical watcher data exists.

## First Concrete Artifact

Build:

```text
lib/watcher_verify.py
```

It should read:

```text
X stream payload
latest token snapshots
movement_events.json
```

And output a verification decision:

```json
{
  "verified": true,
  "reason": "verified_price_movement",
  "direction": "pump",
  "token": {
    "symbol": "OPG",
    "address": "0x..."
  },
  "market": {
    "price_change_h1_pct": 25.4,
    "price_change_h6_pct": 42.0
  }
}
```

The first version should store every raw stream hit, but Discord should only receive verified movement hits.
