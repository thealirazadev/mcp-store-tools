# Agent evaluation

Unit and live tests call the tools directly. This evaluation checks what users actually get: a
real model, reading only the tool descriptions and the skill, answering shopper and merchant
questions through the server. Each case passes when the model calls the expected tools; answers
are printed for review.

```bash
uv run python evals/agent_eval.py          # needs the claude CLI, a few cents per case
```

Set `SHOPIFY_STORE_DOMAIN` and `SHOPIFY_STOREFRONT_TOKEN` to include the real-store cases
(deals, stock counts, catalog audit). `EVAL_MODEL` picks the model (default `sonnet`).

## Results, 2026-11-09 (Claude Sonnet, v0.7.0 then 0.7.1)

10 cases on Shopify's sample store and a development store with test data: 9/10 on the first
run, 10/10 after two fixes the run exposed:

- Asked "how many Ice snowboards are left?", the model searched with `options` and answered
  "unknown": option matches did not carry stock counts. Fixed: they do now (the store had 10).
- The model writes sizes as "S" or "M" while stores write "Small" or "Medium", so option
  searches missed and the model needed 3 extra calls. Fixed: common size abbreviations match.
  The "buy two small green shirts" case went from 5 tool calls to 2.

A third run of this harness on the 7 sample-store cases passed 7/7 ($0.48) once the cart case
accepted variant ids taken from `matching_variants` (no `get_product` call needed).

Total cost of all runs: $1.32.
