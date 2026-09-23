# Dynamic operation + target

The input is a natural-language goal. Every page observation builds an indexed table of accessible elements and their current values. One node receives one index, even when it supports both clicking and typing.

One TypeSafe request asks which operation to perform and which target would be appropriate for each available operation. The executor consumes only the target head corresponding to the selected operation. This avoids serial operation-then-target calls and rejects targets incompatible with the operation. Dropdown targets include a code-owned option index.

Operation and target questions receive the same next-step rules. Target criteria include current values and checked/selected state. The questions run independently: a target cannot read the operation answer, so its premise explicitly names the operation it assumes.

TYPE_TEXT sends the goal, selected field, visible page context, and recent actions to a small LLM. Its JSON must contain exactly one valid `text` value. The code does not extract quoted literals. A value can be reused after a stale decision only while the entire helper input is identical, and is discarded after a successful mutation.

## System 2 review

Jev is System 1: one fast request per step. `Agent(..., reasoning=True)` adds System 2, a reasoning LLM in [reasoner.py](../jev_ultrafast/reasoner.py). Code does not decide when to think. With reasoning enabled, the Jev request carries one more head, a `noul` question named `system2`: should a slower model decide this step? It runs in the same round trip as the operation and target heads. Its rules name the hard cases (finishing or giving up without unambiguous evidence, repeated or ineffective actions, comparison or inference, several plausible targets). When Jev's probability reaches `REVIEW_THRESHOLD` (0.5), System 2 decides the step. Without reasoning, the head is not asked and requests are unchanged.

System 2 receives the goal, visible page text, the indexed element table, the offered operations, the full action history, and System 1's proposal with its distribution. It also gets Jev's probability for asking. It returns JSON: `analysis`, `operation`, `target`, `note`. Code validates the operation against the offered set and the target against that operation's head, exactly as it validates Jev. Anything else — selectors, invented indices, a TYPE_TEXT target that is a button — is rejected before execution. The validated choice replaces System 1's decision and executes through the same freshness guards.

The `note` is the only free text, and it never executes. It is attached to the executed action, so later System 1 requests see it in `recent_actions`, and it is passed to the text helper as `advice` when System 2 selects TYPE_TEXT. The text helper still writes every typed value. A stale retry reuses a review only while the page fingerprint and the entire review input are identical. Reviews are capped at 12 per run and have a 10-second wall-clock deadline (`REVIEW_TIMEOUT`); a late, failed, or invalid review is logged and System 1's validated decision stands. Results are in [system2.md](system2.md). 

Jev can be reached directly (`TYPESAFE_API_KEY`) or through OpenRouter's decisions API (`OPENROUTER_API_KEY`, model `~typesafe/jev-latest`). The same OpenRouter key is reused for the text helper and reviewer only when their base URL is `openrouter.ai`.

## Budgets and repeat boundaries

Every run has hard limits, checked before the call or input they would allow:

| Limit | Default | Stops |
| --- | --- | --- |
| Model calls per run (Jev + text helper + System 2) | 40 (`JEV_MAX_CALLS`) | the next model call |
| Same action + same argument executed | 3 | the 4th execution |
| Same action chosen without any action succeeding | 3 | the 4th attempt, even on a page that keeps changing |
| Identical Jev request (same page, history, questions) | 3 | the 4th request |
| System 2 reviews per run | 12 | further reviews |
| Browser actions per run | 60 | the 61st action |

`scripts/compare_systems.py --max-calls N` adds a session cap across runs; it stops starting runs once reached.

An attempt that did not execute (target covered or changed, or the text helper had no value) is sent to Jev once as `last_failed_attempt`, until an action succeeds. A stop needs a confident Jev or System 2's agreement: when Jev asks for System 2 about DONE or BLOCKED and System 2 is late or invalid, Jev is asked again; the limits above bound this.

### Repeat boundary

The executor refuses to run the same action (kind and observed label) with the same argument (typed text, or the selected option in the label) a fourth time in one run, whichever system chose it. The refusal happens before browser input and stops the run as blocked. This replaces the older stop after three consecutive unchanged actions. WAIT and scrolling count too: a fourth identical WAIT is refused, so loading that needs more than three explicit waits ends the run.

## Runtime

One browser-side DOM snapshot supplies common HTML/ARIA roles, names, values, visible text, and executable targets. A WeakMap gives each actual node a code-owned identity; a Map keeps the live references used for execution. Replaced elements receive new identities, disconnected references are pruned, and navigation starts a new cache. These IDs are not CDP backend node IDs. Geometry is always read again immediately before input.

The model sees visible text. Background focus emulation keeps animation frames running in the owned tab. Screenshots are optional and disabled in library calls by default; `screenshots=True` or `record_dir=...` enables them. The inspector enables them explicitly. A continuous screencast can record a run separately.

Freshness compares semantic state instead of counting DOM mutations. Before a click/select, guards compare the document, full URL, viewport, safe form values/states, selected target, and nearby form/dialog/row context. Text generation, typing, scrolling, waiting, and completion use a full semantic comparison. The executor rechecks target visibility, enabled state, geometry, and click occlusion. Scoped guards intentionally permit unrelated visible content to change; this is a practical heuristic, not proof that arbitrary page changes are irrelevant to the goal.

Browser mutations are not retried by transport recovery. Completed execution is logged before the next observation, including when that observation encounters a navigation. An interrupted native-select evaluation stops because its change event may already have fired. Typing uses a browser select-all command followed by CDP text insertion, so existing input contents are replaced.

The next observation waits for up to two animation frames or 50 ms after an interaction. Editable ARIA comboboxes instead wait for visible options, capped at 200 ms. This avoids paying for a prediction before autocomplete suggestions arrive. An explicit WAIT remains 100 ms; network loading is never fast-forwarded in the recording.

## What changed after the first demo

The initial prototype used five manually prepared steps and copied quoted strings. That proved finite-choice browser execution but did not demonstrate task decomposition or text generation. The current policy removes that shortcut and uses the original goal throughout. Operation/target distributions replace the old flat-choice/lookahead/Noul arrangement.

The audit also found that treating every INPUT as editable misclassified checkboxes. Editable roles now control TYPE_TEXT availability. Tests cover checkbox/radio/button distinction, invalid operation/target outputs, stale decisions, text-cache invalidation, missing credentials, waits, and final-route verification.

## Boundaries

Up to 250 action candidates are retained; truncated candidates cannot be selected. Controls whose center is covered by another element are observed but not offered (the executor's own hit test); coverage is excluded from freshness, so movement alone does not invalidate a decision. Dropdowns offer at most 20 options each and 60 per page, with short option labels; one page's native selects otherwise produced an 81k-character request that exceeded Jev's input limit. `PRESS_ENTER` presses Enter in an observed editable field; typing uses the same target-scoped guard as clicks. The service stays loopback-only, serializes inspector actions, and checks Host, Origin, and a local request token. Credentials remain server-side. Tabs share the existing Chrome profile.

The policy is generic, but two websites do not establish broad reliability. Name resolution covers common labels, ARIA references, and text; it is not the browser's full accessibility algorithm. Shadow roots, frames, canvas, uploads, nested scrolling, pop-ups, and complex keyboard interactions can block progress. A valid action can still be wrong. Independent checks, rather than the model's DONE choice, determine whether the demonstrated task succeeded.
