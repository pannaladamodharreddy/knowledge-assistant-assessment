# Knowledge system notes

Stdlib only. No API keys, no network, no randomness. `requirements.txt` is empty on purpose.
Run: `python harness/run_eval.py --submission ../submission --eval eval/public_eval.jsonl` from the kit folder.

## Design in one paragraph
Each snapshot page is cleaned (images and footnote digits removed, links reduced to text) and cut into small chunks:
one chunk per table row (with the column headers attached, so a row reads "Certificate: 17 Month ...; APY: 4.10%"),
one per sentence or bullet (with its heading as context), and one per hyperlink (anchor text, section heading, target URL).
A chunk is stored once per (url, text) and remembers the set of snapshots in which it was live. That set is the
per-fact history, so `as_of` just means "only use chunks live in the chosen snapshot".
The index is a BM25 inverted index built once at the end of ingest, answers take milliseconds.

## How "current" is decided
1. Only chunks live in the target snapshot are used. As of a date, the target is the latest snapshot taken on or before that date
   (before the first snapshot the system declines).
2. Source authority by URL class: rate sheets (`/rates`, `/business-rates`) 1.0, product and about pages 0.95, other pages 0.65 to 0.7,
   landing and promo pages 0.7, PDFs 0.75, newsroom 0.5, blog and dated posts 0.35, calculators 0.3, expired offer pages and tag pages lowest.
   Authority multiplies the BM25 score.
3. A table row is dropped when another live page states the same labelled fact (same row label, same business or personal flag,
   year ranges collapsed) with a different value and a higher (authority, "rates are effective as of" date). That removes stale widgets and promo copies.
4. Press-release duplicates ("-2", "-3" slugs) get a small recency bonus.

## Qualifiers and tiers
Row labels carry term, jumbo, purchase or refinance, loan-amount cap, model-year range and balance tier, so tiers are different facts.
The question is parsed for dollar amounts (matched into balance ranges and "or less" caps) and years (matched into "2024-27" style ranges).
Business rows are penalised unless the question says business. Promo or relief rows are penalised unless the question asks for a special.
Rate, APY and APR live in separate columns of the same row. Rate questions only accept chunks that contain a rate figure next to a rate word.

## Conflicts
For every row, each numeric column (APY, rate, APR, fees) forms a key (label, business flag, column).
A key with two or more distinct numeric values is a conflict. Two kinds: "within" (different values live in the same snapshot, for
example a stale copy of a table) and "history" (value changed between snapshots). One conflict per labelled fact (APY, else rate, else APR).
The winner is the observation live in the newest snapshot with the highest authority, then newest effective date.
Prose counts (branches, counties) are compared across pages in the newest snapshot, majority wins.
Low authority pages (calculators) are ignored. The list is capped at 80.

## "I don't know"
The fixed refusal text has no digits and `confident=False`. It is used when the question names an entity (capitalised word) that no
passage contains, when the best passages cover too little of the question, when a rate question has no rate figure next to the matching
product words, or when an hours or routing question finds no time or 9-digit number.

## Determinism and speed
Sorted tie breaks everywhere, answers cached per (question, snapshot). Ingest about 25 s, answers about 0.1 s.

## Known weak spots
Free-text answers return the best passages rather than a composed sentence. List questions (wallets, eligibility) depend on the right
passage ranking. "Last year" style relative dates are not resolved. Hours differ by branch and are not tied to a named branch.

## Round 2 additions
Near-duplicate passages ("-2", "-3" copies) are collapsed and the newest differing copy is kept. Teaser snippets ("Keep reading") are down-weighted.
Rows are composed into short lines that show only the requested column (APY, APR or dividend rate, with "not the APY" respected).
Term and year qualifiers in the question (30-month, 17-month, 30-year) boost matching rows and demote other terms.
Count and routing questions use a voting extractor that returns one sentence with one number, so rewordings give identical answers.
Questions with an as_of date before the first snapshot use the first snapshot but only rows whose "effective as of" date is on or before it.
Table effective dates are searched up to 14 lines below a table.
Known gap: conflict winner for the branch count still prefers the figure used by most pages (23), not the figure the answer path returns (22).
