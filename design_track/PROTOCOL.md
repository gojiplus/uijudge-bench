# Design-quality track — annotation protocol (v1)

This protocol governs the pairwise, rubric-anchored, psychometrically-analyzed design track.
It is written to be **rater-pool agnostic**: the same instrument (rubric, pair set, app,
analysis) supports self + colleagues, a Prolific crowd, or an expert design panel. The rater
pool is chosen at execution time; this document describes what each choice additionally needs.

## 1. What raters do (instructions, verbatim)

Raters read `design_track/rubric_v1.md` in full, then the app shows the same instructions:

> You will compare pairs of web pages, one design dimension at a time, and choose which page
> is better on that dimension. This is a **forced choice** — pick a side. Use "genuinely
> cannot tell" only when the two pages are truly indistinguishable on the dimension in front
> of you; it is not a skip button. Judge **only the named dimension** — each dimension lists
> what it does not judge; ignore those aspects. The left/right placement is randomized every
> trial; judge the pages, not the sides.

One dimension is judged per trial, with that dimension's behavioral anchors on screen. Both
pages in a trial are shown at the **same viewport** (the frozen `page.html` rendered in a
sized iframe).

## 2. Pairs

`design_track/pairs_v1.jsonl` (built by `python -m uijudge.design_track.pairs --build`,
deterministic given the seed) holds two pair types:

- **Preference pairs (~60%)** — two different clean pages, same genre + same viewport (real)
  or same template family + same viewport (synthetic). No known answer; human judgment
  creates the label.
- **Validity pairs (~40%)** — a clean page vs. its mutated twin where the mutation degrades a
  *design* dimension (see the qualifying mutation classes in `uijudge/design_track/pairs.py`
  `DESIGN_DEGRADING`). The mutated member is worse **by construction** (mutation receipt).

Pilot scale is 120 pairs (48 validity / 72 preference).

## 3. Catch-trial policy

Validity pairs are injected, **unlabeled and indistinguishable from preference trials**,
among the preference trials (roughly one catch per several preference trials). A rater who
prefers the mutated member on the degraded dimension has failed that catch.

- A rater's **catch-trial pass rate** is the fraction of validity trials on which they chose
  the construction-better (clean) member (`cannot tell` counts as a miss).
- Default screening threshold: **0.8**. Raters below threshold are **flagged**, and their
  judgments are **excluded from promotion** (`analyze.py promote` excludes flagged raters
  before computing agreement). The threshold is configurable (`--catch-threshold`).

## 4. Judgment target and reliability

- **Target 10–20 judgments per pair per dimension** (`--n-min`, default 10). A dimension is
  admitted only if its **Krippendorff's α ≥ 0.667** (nominal, computed over preference-pair
  judgments coded A/B relative to the pair's canonical member order).
- **Run a pilot round first**, regardless of the final pool, to validate the instrument
  (do the anchors discriminate? are α values usable? is the `cannot tell` rate tolerable?).

## 5. Running the app for each rater-pool scenario

Build pairs once, then serve them:

```
python -m uijudge.design_track.pairs --build
python -m uijudge.design_track.app            # serves design_track/pairs_v1.jsonl
python -m uijudge.design_track.app --demo     # 3-pair smoke test of the flow
```

**Self + colleagues (local).** Each rater opens `http://<host>:8000/`, enters a rater id
(and optional role), and works through trials. Sessions are **resumable** — returning skips
already-judged pair+dimension trials. Judgments append to
`design_track/judgments/<rater_id>.jsonl`. This is fully supported today.

**Prolific crowd (NOT built here — what it would additionally need).** The app is a local
stdlib server; a crowd deployment would additionally require: (a) **hosting** the app and the
frozen corpus on a public URL with per-worker rater ids passed through the Prolific
completion URL; (b) a **consent screen** (placeholder below) reviewed by the study owner;
(c) a **payment calculation** — at an estimated ~10–15 s/trial and, say, ~60 trials/session,
that is ~10–15 min; pay at or above the platform's fair-wage floor (e.g. ~£9/hr → ~£1.50–2.25
per session); (d) attention/completion checks beyond the built-in catch trials if desired.
None of this is implemented — it is scoped here so the owner can decide.

**Expert design panel.** Same local app; record each expert's role/experience in the optional
demographics field. Fewer raters at higher expertise; the α gate and BT fit are unchanged.

## 6. Analysis workflow

```
python -m uijudge.design_track.analyze report   --judgments design_track/judgments
python -m uijudge.design_track.analyze --selftest    # BT+α+promotion on bundled synthetic data
```

The report gives, per dimension: α (and whether it clears the 0.667 gate), Bradley-Terry
latent scores + rank, and `cannot tell` rate; per rater: catch pass rate and flag status.

## 7. Promotion criteria (judgments → benchmark items)

```
python -m uijudge.design_track.analyze promote \
    --judgments design_track/judgments --labels labels/items.jsonl \
    --n-min 10 --alpha-min 0.667 --rater-pool "pilot: N colleagues"
```

- **Preference pairs** → `design_pair` items, `door=human`, one per (pair, dimension) that
  clears the gate: `n_judgments ≥ n_min` **and** dimension `α ≥ alpha_min` **and** a majority
  winner exists. Receipt: `{n_judgments, agreement, alpha_dimension, bt_margin,
  rubric_version, rater_pool_desc}`. Ground truth is the majority-preferred member (A/B).
- **Validity pairs** → `design_pair` items, `door=mutation`, from construction ground truth
  (the clean member); receipt carries the mutation receipt + rubric version. These need no
  human agreement and double as model-judge validity items.

**Until `promote` runs, nothing design enters `labels/items.jsonl`.** Under-judged or
under-agreed preference pairs are refused; they stay in `design_track/` until enough
agreeing judgments exist.

## 8. Honest limits

- **No IRB claim is made.** This protocol does not assert ethics-board approval. If the study
  moves to paid crowd work with human subjects, the owner is responsible for obtaining any
  required approval and for reviewing the consent language.
- **Consent language is a placeholder** for the owner to review, not final text:

  > *Placeholder — owner to review.* You are taking part in a research study comparing the
  > visual design of web pages. You will view pairs of pages and choose which is better on a
  > stated dimension. Participation is voluntary; you may stop at any time. No personal data
  > beyond an optional self-described role is collected. [Contact / withdrawal / data-use
  > terms to be completed by the study owner.]

- The synthetic pages share one template family, so synthetic preference pairs vary content
  and styling, not layout archetype — real-page preference pairs (within genre) carry the
  weight of cross-design variation. This is disclosed, not hidden.
