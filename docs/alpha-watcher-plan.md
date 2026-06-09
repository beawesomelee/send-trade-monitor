# Alpha Watcher Project Plan

## Problem

On send.trade, users can see token price movement, but the natural question is:

> why is this moving?

The current `send-trade-monitor` movement scanner helps answer that after the fact. It watches for sharp token moves, asks Grok to search X and the web, and posts a short explanation. That keeps the site aware of current news.

But if we only use the price-first model, send.trade will often be late. By the time price has already moved, the alpha may be partially gone.

The next system should be alpha-first: watch high-signal X accounts, classify new posts for token-relevant alpha, and add useful Lore before or during the move.

## Core Loop

```text
price movement happens
-> movement scanner asks Grok why
-> extract accounts, phrases, catalysts, links, and event types
-> store suggestions as pending watcher candidates
-> human/admin approves useful candidates
-> rules manager updates X stream rules
-> X watcher catches similar future signals earlier
-> deterministic matcher narrows candidate tokens
-> AI reviewer judges alpha/relevance from enriched context
-> accepted signals become Lore
-> later price movement confirms or rejects signal quality
-> account and phrase scores improve over time
```

The movement scanner becomes the teacher. The X watcher becomes the early-warning system.

## V1 Goal

Build an alpha-first watcher that learns from price-movement explanations, updates programmable X monitoring rules, captures relevant tweets from high-signal accounts, classifies them, and turns good hits into Send token Lore.

RAG is not required for V1. V1 should use structured storage, deterministic matching, model classification, review gates, and outcome tracking first.

## Design Principles

- Separate deterministic software pipeline work from AI judgment.
- Every step should produce a concrete artifact: table, file, API, job, queue item, or log.
- Model-suggested accounts and phrases must not directly mutate X rules.
- Human/admin review and deterministic validation happen before rule sync.
- Avoid account-by-token rule explosion.
- Fetch tweet context before classification.
- Track model, prompt, schema, and rule versions early.
- Keep V1 small enough to replay and test with stored fixtures.

## V1 Flow Checklist

### 1. Token Reference Store

Artifact: `TokenProfile` table/store.

Fields:

- `token_id`
- `symbol`
- `name`
- `chain`
- `chain_id`
- `address`
- `official_x_handle`
- `official_x_user_id`
- `aliases`
- `domains`
- `deny_terms`
- `confusing_terms`
- `updated_at`

Tasks:

- Pull verified tokens from the Send admin panel/API.
- Normalize Base addresses as lowercase.
- Preserve Solana address casing.
- Store aliases: symbol, `$SYMBOL`, name, address, official handle, domains.
- Store deny/confusing terms for ticker collisions.
- Refresh on a schedule.

Why: this is the entity layer every matcher, rule, and Lore item depends on.

### 2. Movement Event Store

Artifacts: `MovementEvent` and `GrokExplanation` tables/stores.

Tasks:

- Keep the existing pump/dump scanner.
- Store movement events in structured form.
- Store Grok explanations separately from movement data.
- Link every explanation to token and movement event.
- Track model, prompt, prompt version, and response schema version.
- Store source links or cited accounts when available.

### 3. Grok Explanation Extractor

Artifact: pending candidate records.

Extractor output:

- mentioned X accounts
- phrases
- event/catalyst types
- links/domains
- cited tweets/accounts if available
- linked token
- source movement event
- confidence score
- extraction model/prompt/version

Tasks:

- Parse each Grok explanation into structured candidates.
- Store extracted accounts as pending `SourceAccount` candidates.
- Store extracted phrases as pending `WatchPhrase` candidates.
- Link each candidate to its source movement event and explanation.

Important: model-suggested accounts and phrases are suggestions only. They do not directly modify X stream rules.

### 4. Source Account Registry

Artifact: `SourceAccount` table/store.

Fields:

- `handle`
- `x_user_id`
- `source_type`
- `trust_tier`
- `reason_added`
- `source_event_id`
- `score`
- `status`: `pending`, `approved`, `rejected`, `disabled`
- `last_seen_tweet_id`
- `created_at`
- `updated_at`

Tasks:

- Seed manually chosen high-signal accounts.
- Add Grok-suggested accounts as pending.
- Resolve handles to stable X user IDs when possible.
- Track why each account was added.
- Score accounts based on observed usefulness.
- Support manual approve, reject, disable, and boost actions.

### 5. Phrase / Token Rule Registry

Artifact: `WatchPhrase` table/store.

Fields:

- `phrase`
- `token_id`
- `source`
- `source_event_id`
- `score`
- `status`: `pending`, `approved`, `rejected`, `disabled`
- `deny_terms`
- `created_at`
- `updated_at`

Tasks:

- Seed from token aliases.
- Add learned phrases as pending.
- Track phrase source and token association.
- Store deny terms for broad or ambiguous phrases.
- Support manual approve, reject, disable, and boost actions.

### 6. Admin Review Queue

Artifact: review queue UI, sheet, JSON file, or admin endpoint.

Each review item should show:

- suggested account or phrase
- why it was suggested
- source movement event
- source Grok explanation
- linked token
- confidence/score
- current status

Actions:

- approve
- reject
- disable
- boost

Why: this is the safety valve before the model can influence stream rules.

### 7. X Rule Builder

Artifacts: desired rule set, rule diff, rule history.

Tasks:

- Generate rules only from approved accounts, phrases, and tokens.
- Validate every generated rule deterministically.
- Add dry-run mode.
- Add rule diffing.
- Add rule history.
- Add source/version tags.
- Enforce X rule limits before applying changes.
- Prevent account-by-token rule explosion.

Important X API detail: filtered-stream rules cannot be edited in place. To update a rule, delete the old rule ID and add the replacement rule.

Rule examples:

Top trusted account:

```text
from:ACCOUNT -is:retweet
```

Narrower source account:

```text
from:ACCOUNT ("TOKEN" OR "$TOKEN" OR "token name" OR officialhandle) -is:retweet
```

Token/project discovery:

```text
("$TOKEN" OR "token name" OR contract_address OR officialhandle) -is:retweet
```

### 8. X Rules Manager

Artifact: X stream rules synced from desired rule set.

Methods:

- `listRules()`
- `addRules(rules)`
- `deleteRules(ruleIds)`
- `replaceRules(desiredRules)`
- `dryRunSync(desiredRules)`

Tasks:

- Fetch current X rules.
- Compare current rules against desired rules.
- Delete stale rules by ID.
- Add missing rules.
- Store applied rule snapshots.
- Roll back or alert on sync failure.
- Never apply model output directly without registry approval and deterministic validation.

### 9. X Stream / Poll Watcher

Artifacts: raw tweet store and ingest logs.

Tasks:

- Connect to X filtered stream.
- Receive matching tweets in near real time.
- Store raw tweet payloads.
- Store matching rule tags.
- Deduplicate by tweet ID.
- Handle reconnects and backoff.
- Support polling fallback.
- Track ingest latency.

### 10. Context Enrichment

Artifact: enriched tweet packet.

Before classification, fetch:

- quoted tweet
- reply parent
- thread parent if relevant
- expanded URLs/domains
- mentioned users
- author metadata
- official-account relationships

Why: many alpha posts are context-dependent. A trusted account saying "big" is meaningless alone, but meaningful if it quote-tweets a token's official account or links to a launch announcement.

### 11. Fast Tweet-to-Token Matcher

Artifact: local candidate-token match result.

Match against:

- contract address
- cashtag
- symbol
- name
- aliases
- official handle
- domains
- quoted/replied-to official account

Tasks:

- Apply deny/confusing terms.
- Preserve Solana address casing.
- Return candidate tokens with local match reasons.
- Handle ambiguous ticker and name matches.
- Send only top candidate tokens to AI review.

This step should be deterministic and local before LLM review.

### 12. AI Tweet Reviewer

Artifact: strict JSON review result.

Input packet:

- tweet
- author
- enriched context
- candidate token
- local match reasons
- source account trust tier
- matched rule tags

Output schema:

- `is_relevant`
- `should_publish`
- `category`
- `direction`: `bullish`, `bearish`, `neutral`
- `relevance_score`
- `confidence_score`
- `short_summary`
- `reason`
- `risk_flags`

Tasks:

- Reject vague posts.
- Reject unsupported claims.
- Require evidence from tweet text, context, author, token mapping, or learned phrase.
- Track model, prompt, prompt version, and output schema version.
- Validate output against schema before using it.

### 13. Lore Item Generator

Artifact: `LoreItem` record for token pages.

Fields:

- token
- source tweet URL
- author
- timestamp
- category
- direction
- summary
- confidence
- reason
- source rule tags
- review model/prompt/version

Tasks:

- Convert accepted reviewed tweets into Lore.
- Keep output short and trader-useful.
- Avoid unsupported claims.
- Dedupe similar Lore items.
- Store rejected candidates for evaluation.

### 14. Outcome Tracker

Artifact: outcome records linked to tweet, token, account, phrase, and rule.

Tasks:

- Snapshot price and volume at detection time.
- Re-check after 1h, 6h, and 24h.
- Store outcome.
- Link outcome back to:
  - tweet
  - author
  - phrase
  - token
  - rule
  - classifier decision

### 15. Scoring Loop

Artifact: account and phrase score updates.

Tasks:

- Promote accounts and phrases that repeatedly precede useful movement.
- De-rank false-positive sources.
- Track score changes with reasons.
- Keep human override.
- Do not let the model directly mutate rules without validation and review.

### 16. Alerts / Ops

Tasks:

- Add concurrency guards to scheduled jobs.
- Add X API rate-limit handling.
- Add X quota/credit monitoring.
- Add structured logs.
- Add failure alerts.
- Add credential rotation process.
- Add rule sync failure rollback.
- Alert when stream disconnects or no tweets arrive for an unusual period.

### 17. Tests

Required tests:

- token matching
- rule generation
- rule diffing
- rule limit enforcement
- Grok-output extraction
- quote/reply context matching
- ticker collision handling
- deny-term handling
- duplicate tweet handling
- prompt schema validation
- AI reviewer fixture tests
- outcome scoring
- X rule sync dry-run integration
- replay tests with stored raw tweets and movement events

### 18. V1 Success Criteria

- System can ingest Send verified tokens.
- System can store movement events and Grok explanations.
- System can extract at least one pending account or phrase from a movement explanation.
- Admin/review flow can approve a suggested account or phrase.
- System can generate, dry-run, diff, and sync X stream rules from approved inputs.
- System can capture matching tweets.
- System can enrich quote/reply/URL context.
- System can deterministically match tweets to candidate tokens.
- System can classify tweets into alpha/noise with strict JSON output.
- System can create deduped Lore items from accepted tweets.
- System can track whether accepted signals preceded token movement.

## V2 Checklist

### 1. RAG Memory

- Embed past movement events.
- Embed Grok explanations.
- Embed accepted and rejected tweet signals.
- Retrieve similar historical cases for new tweets.
- Use retrieval to improve classification and confidence.

### 2. Historical Backtesting

- Use full-archive X search if available.
- Replay old account tweets against historical price movement.
- Score accounts and phrases by predictive value.

### 3. Advanced Source Discovery

- Discover accounts through retweets, replies, quote tweets, mentions, and communities.
- Build an account graph around high-performing signal sources.
- Identify second-order accounts before they become obvious.

### 4. Model-Generated Rule Proposals

- Let the model propose rule additions and removals from historical evidence.
- Require deterministic validation and human approval for risky changes.
- Auto-apply only low-risk changes.

### 5. Better Ranking

- Rank Lore by expected impact.
- Add confidence calibrated by past outcomes.
- Separate breaking news, official updates, KOL alpha, rumors, and noise.

### 6. Product Integration

- Push Lore directly into send.trade token pages.
- Add "why moving" and "early signal" labels.
- Show source tweet and confidence.
- Allow users/admins to dismiss bad Lore.

## First Milestone

V1 should prove the learning loop. V2 should make the system smarter.

The first concrete milestone should be:

```text
take one movement event
-> store Grok explanation
-> extract accounts and phrases as pending candidates
-> approve one candidate
-> generate watcher rules
-> dry-run and sync rules
-> capture future tweets
-> enrich tweet context
-> match candidate tokens locally
-> classify with AI reviewer
-> create Lore
-> store outcome
```
