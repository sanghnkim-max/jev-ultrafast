# Wildlife and real-life benchmark

Measured on 2026-09-23 with `uv run python scripts/compare_systems.py --suite all --modes system1+2`. Setup: Jev through OpenRouter, System 2 `deepseek/deepseek-v4.1-flash`, text helper `inception/mercury-2.5`, and an isolated headless Chrome 153 profile. Each task is one natural-language goal. A run passes only if its check against the final page passes; DONE alone is not evidence. Task definitions and checks are in [tasks.py](../jev_ultrafast/tasks.py).

| Suite | Tasks | Examples |
| --- | --- | --- |
| core | 5 | fixture filters, cheapest stay, paraphrased article, Wikipedia, Google Flights |
| wildlife | 10 | local [Wildwatch](../jev_ultrafast/static/wildlife.html) field guide (search, scientific name, filter + compare, sort + pick, background knowledge, sighting form), iNaturalist, Audubon, two Wikipedia species questions |
| reallife | 6 | WebVoyager-style tasks on Cambridge Dictionary, arXiv, GitHub, Hugging Face, Apple, Coursera |

Before any paid run, each Wildwatch check was shown to pass on a scripted ideal path and fail on a wrong answer. Each real-site check was also shown to pass on its expected final URL. WWF and Allrecipes were dropped at that stage: headless Chrome received a bot challenge or an empty page before any agent action.

## Result

| | core | wildlife | reallife | Total |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 5/5 | 7/10 | 1/6 | **13/21** |
| After generic fixes | 5/5 | 9/10 | 3/6 | **17/21** |

One attempt per task. The baseline core figure comes from the preceding round with identical core tasks. Of the four remaining failures, two are bot walls: iNaturalist's Explore page and Cambridge's search results both showed a Cloudflare challenge. arXiv found the search results, but System 2 re-sorted and re-filtered instead of opening the paper, and the repeat boundary stopped it. Coursera failed here after passing the previous targeted rerun: Jev kept clicking the filled field instead of pressing Enter.

## What the failures taught, and the generic fixes

No fix names a site, field, or value.

| Failure seen | Cause | Fix |
| --- | --- | --- |
| GitHub, Coursera, arXiv typed a query and then clicked the field repeatedly | No way to press Enter | `PRESS_ENTER` operation on observed editable fields |
| Cambridge typed into a field under a cookie banner; arXiv clicked buttons under its search overlay | Covered controls were offered, then refused by the executor | Controls whose center is covered are observed but not offered |
| Audubon: 18 text-helper calls, nothing typed | Typing required the whole page to be unchanged; a rotating element never settles | Typing uses the same target-scoped guard as clicks |
| Jev repeated a refused attempt | It never learned the attempt did not execute | `last_failed_attempt` in Jev's next request |
| arXiv run crashed when the text helper returned null | Helper failure was fatal | Recorded as a failed attempt; Jev chooses again |
| Apple: `max_tokens_exceeded` | Three native dropdowns produced an 81k-character request | Options capped (20 per dropdown, 60 per page) with short labels: 29k |
| arXiv: run crashed, then a 51 s observation | Browser-daemon timeout after Enter | Read-only observation retries within an 8 s total deadline |
| Wildwatch report: an unsure BLOCKED ended the run | System 2 was asked and hit its deadline | A stop needs a confident Jev or System 2's agreement; otherwise Jev asks again |

## Safeguards seen live

The limits in [design.md](design.md#budgets-and-repeat-boundaries) stopped real loops: the call budget (Audubon before the typing fix), the repeat boundary (GitHub, Coursera, arXiv), the identical-request stop (Cambridge), and the session cap in the script. The whole benchmark (51 runs, 405 model calls) had a provider-reported cost of **$0.098**.

## Limits

One attempt per task, on one machine and one fresh headless profile. Real sites change, and bot protection depends on the browser profile. Coursera passed in one attempt and failed in another, so single-attempt rates are noisy. Wildwatch was written for this benchmark; its difficulty is controlled, not representative. Raw traces are in ignored `artifacts/systems/`.
