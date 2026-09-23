# System 1 + System 2 on real tasks

Measured on 2026-09-23 with `scripts/compare_systems.py`. System 1 is TypeSafe Jev (`typesafe/jev-1.13-20260917`) reached through OpenRouter's decisions API. System 2 is `deepseek/deepseek-v4.1-flash` with medium reasoning. The text helper is `inception/mercury-2.5` with reasoning disabled. Browser: an isolated headless Chrome 153 profile, 1120×780. Every pass is an independent check of the final browser state. A DONE choice alone counts as nothing.

| Task | Page | Independent check |
| --- | --- | --- |
| `filters` | Local fixture | Casa Flora open with Design, Free cancellation, and Destination Lisbon applied |
| `cheapest` | Local fixture | Free cancellation applied and the cheapest remaining stay (Casa Flora, €145 < €210) open |
| `paraphrase` | Local fixture | The article whose title shares no keywords with the goal is open (`#uncertainty`) |
| `wikipedia` | en.wikipedia.org | `/wiki/Kurt_Gödel` open from "the logician who proved the incompleteness theorems" |
| `flights` | Google Flights | Search page, London, Tue Oct 20, and visible flights on October 20 |

## Results

All attempts are included. Rounds 1–3 used code triggers (DONE/BLOCKED, stuck, loop, low confidence). From round 4, Jev decides with its own `system2` head, and the repeat boundary replaces the three-unchanged-actions stop.

| Round | Who engages System 2 | Change | System 1 | System 1 + 2 | Median, S1 → S1+2 |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | Code | First reviewer prompt | 4/5 | 4/5 | 2.9 s → 12.6 s |
| 2 | Code | Reviewer shares System 1's rules; loop trigger | 8/10 | 9/10 | 2.5 s → 7.1 s |
| 3 | Code | 10-second review deadline | — | 9/10 | — → 11.4 s |
| 4 | **Jev** | `system2` head; repeat boundary | 8/10 | 7/10 | 5.6 s → 5.9 s |
| 5 | **Jev** | Reviewer sees recently observed page text (`cheapest` only) | — | 4/4 | — → 2.5 s |
| 6 | **Jev** | Final code | 9/10 | **10/10** | 2.7 s → 2.7 s |

**Jev asks at the right moments.** Across round 4, its `system2` probability was 0.10–0.49 on routine steps in every task. It crossed 0.5 on the premature BLOCKED in `cheapest` (0.54, 0.56) and during a list/detail oscillation (0.67, 0.73). Code triggers had also reviewed every DONE, which tripled time on tasks System 1 already solved. With Jev deciding, the four easy tasks ran at System 1 speed with zero reviews.

**System 1 failed `cheapest` in 6 of 7 attempts.** It turned on the filter and opened Casa Flora, then chose BLOCKED on the correct page. With reviews, 10 of 13 attempts passed across all rounds, and 6 of 6 since round 5.

**The reviewer needs memory of what it asked to see.** In round 4, System 2 was consulted only on the detail page. It said "go back and compare prices", but it never saw the list: its history held only action labels. System 1 reopened Casa Flora, and System 2 again sent it back. The repeat boundary stopped the run before a fourth "View Casa Flora". System 2 now receives the visible text after the last four actions (`recently_observed`). With that, `cheapest` passed 6/6 in 1.9–4.3 s, each with one review.

**Round 1's reviewer made `filters` worse.** It overrode an uncertain step and skipped submitting the typed destination. It then accepted DONE while the page read "Destination anywhere". Since round 2, the reviewer prompt includes System 1's rules ("a populated field alone is not an applied search") and treats contradicting visible text as unmet.

**The repeat boundary fired once live** (round 4, above). It is also covered offline for identical WAITs and for typed text, where a different argument doesn't count. Two runs (rounds 2 and 4) failed before any review because the text helper returned `null` for the first field.

## Cost of thinking

Without a deadline, two reviews took 35 s and 69 s (1,907 and 5,124 reasoning tokens). One provider stall took 30 s for 136 output tokens. Reviews now have a 10-second wall-clock deadline (`REVIEW_TIMEOUT`); a late or invalid review is logged and System 1's decision stands. In round 6, the two reviews took 1.3 s and 1.9 s.

System 2 cost for rounds 1–3: $0.0185. Rounds 4–6, with Jev deciding: $0.0037 for 10 reviews in total. The extra `system2` head adds no round trip.

## Limits

Five tasks with a few attempts each is a smoke comparison, not a benchmark. The fixture tasks were written to exercise System 2. Round 5 reran only `cheapest`. Google Flights and Wikipedia are live and can change. Headless Chrome used a fresh profile, not the logged-in profile behind [performance.md](performance.md), so these timings are not comparable to the 7.1 s recording. Raw traces are in ignored `artifacts/systems/`.
