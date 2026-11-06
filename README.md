# Knowledge system for a website that changes over time

A small question-answering system for the interface.ai knowledge assessment. It reads dated snapshots of a
credit-union website, answers questions about it with sources, says which answer is current, lists facts that
conflict across pages or over time, and says "I don't know" when the pages do not support an answer.

No network calls, no API keys, no randomness, no third-party packages. Python standard library only.

## What is in this repo

| File | What it is |
|---|---|
| `solution.py` | The whole system: ingest, index, answer, conflicts. About 1,000 lines. |
| `NOTES.md` | Design notes: how "current" is decided, conflicts, refusals, known weak spots. |
| `requirements.txt` | Empty on purpose. Nothing to install. |

## The problem

The website changes between snapshots. Rates move, offers expire, old copies of a table linger on other pages.
A good system has to answer the question as of a date, prefer the page that should win, point to its sources,
surface real conflicts, and refuse when it has no evidence.

## How the solution works

1. **Clean and chunk.** Each page is cleaned, then cut into small chunks: one per table row (with its column
   headers attached, so a row reads "Certificate: 17 Month ...; APY: 4.10%"), one per sentence or bullet (with its
   heading as context), and one per hyperlink.
2. **Remember history.** A chunk is stored once and remembers the set of snapshots where it was live. That set is the
   history of the fact, so "as of a date" simply means "only use chunks live in the right snapshot".
3. **Search.** A BM25 inverted index is built once after ingest, so an answer takes about 0.1 seconds. Ingest takes
   about 25 seconds.
4. **Decide what is current.** Pages get an authority weight by type (rate sheets highest, product pages next,
   blog posts and calculators low). A table row is dropped when another live page states the same labelled fact with
   a different value and a higher authority or a newer "effective as of" date.
5. **Understand the question.** It reads dollar amounts, years, terms (for example 17-month or 30-year), and whether
   the question is about business or personal products, and boosts the matching rows.
6. **Report conflicts.** For every table row, each numeric column (APY, rate, APR, fees) is a key. Two different
   values for one key is a conflict, either inside one snapshot (a stale copy) or across snapshots (a real change).
   The winner is the newest, highest-authority observation.
7. **Refuse safely.** If the question names something no page mentions, or the best passages cover too little of the
   question, the system returns a fixed "I don't know" answer with `confident=False`.

Answers are deterministic: ties break in sorted order and results are cached per question and snapshot.

## Running it

The system is meant to run inside the assessment harness, which supplies the snapshots and the evaluation questions:

```
python harness/run_eval.py --submission ../submission --eval eval/public_eval.jsonl
```

Run that from the assessment kit folder. The assessment data is not included in this repo.

## Results

On the public evaluation set it scored 0.787 in my last run. The weak spots are listed honestly in `NOTES.md`.

## Known limits

- Free-text answers return the best passages rather than a freshly written sentence.
- Relative dates such as "last year" are not resolved.
- Branch hours are not tied to a named branch.
- For the branch count, the conflict winner prefers the figure most pages use, not the figure the answer path returns.
