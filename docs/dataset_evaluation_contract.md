# Sprint 6 Dataset and Evaluation Contract

## Purpose

Sprint 6 prepares KAI for dataset construction, offline evaluation, and distillation readiness
without changing the runtime analysis contract.

All Sprint 6 artifacts reuse the existing analyzed-document boundary:

`CanonicalDocument -> export_training_data() row -> evaluation harness -> report`

No new provider architecture, no new analysis schema, and no training pipeline are introduced here.

---

## Dataset Roles

Sprint 6 defines three dataset roles. The role is determined only by `analysis_source`.

| Role | Required `analysis_source` | Purpose | Teacher-eligible |
|---|---|---|---|
| Teacher-only dataset | `external_llm` | Distillation / supervised teacher corpus | yes |
| Internal benchmark export | `internal` | Compare internal outputs against teacher or rule baseline | no |
| Rule baseline export | `rule` | Deterministic floor and regression baseline | no |

Rules:
- `EXTERNAL_LLM` is the only teacher-eligible tier.
- `INTERNAL` is benchmark-only.
- `RULE` is baseline-only.
- `provider`, `ensemble_chain`, and all other metadata are trace fields only.
- Teacher filtering must never branch on provider family or ensemble composition.

---

## Export Row Contract

Sprint 6 reuses the current JSONL row format produced by `app/research/datasets.py`.
No second export schema is introduced.

Each row must contain:

```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "{\"sentiment_label\":\"...\", ...}"}
  ],
  "metadata": {
    "document_id": "uuid",
    "provider": "openai",
    "analysis_source": "external_llm"
  }
}
```

Required metadata:
- `document_id`: stable join key for evaluation
- `provider`: technical provenance only
- `analysis_source`: canonical dataset-role signal

Required structured assistant targets for Sprint 6 comparison:
- `sentiment_label`
- `priority_score`
- `relevance_score`
- `impact_score`
- `tags`

Other existing structured targets may stay in the export, but Sprint 6 evaluation must not require
additional fields beyond this minimal set.

---

## Teacher-Only Export Rules

Teacher-only export is a dataset role, not a second schema.

Teacher-only rows must satisfy all of the following:
- analyzed document
- non-empty text payload
- `metadata.analysis_source == "external_llm"`

Teacher-only filtering rules:
- must filter exclusively on `analysis_source`
- must not use `provider`
- must not use `metadata["ensemble_chain"]`
- must not infer teacher eligibility from titles, source names, or URLs

Prohibited teacher sources:
- `analysis_source == "internal"`
- `analysis_source == "rule"`

Legacy note:
- compatibility fallbacks such as `effective_analysis_source` may still exist in runtime code
- Sprint 6 implementation must make the exported `metadata.analysis_source` the only downstream
  teacher gate

---

## Internal Benchmark Export

Internal benchmark export contains rows with:
- `metadata.analysis_source == "internal"`
- analyzed document
- non-empty text payload

Purpose:
- compare internal outputs to teacher outputs on overlapping `document_id`s
- measure whether internal quality closes the gap to Tier 3

Non-goals:
- not a teacher corpus
- not a promotion signal by itself
- not a fallback override

---

## Rule Baseline Export

Rule baseline export contains rows with:
- `metadata.analysis_source == "rule"`
- analyzed document
- non-empty text payload

Purpose:
- establish the deterministic floor
- detect regressions in fallback behavior
- quantify how much better internal outputs are than pure rules

Non-goals:
- not a teacher corpus
- not a substitute for external labels

---

## Evaluation Harness Contract

Sprint 6 evaluation is an offline comparison harness. It does not call external providers.

### Inputs

The harness compares exactly two datasets at a time:
- reference dataset
- candidate dataset

Allowed reference/candidate pairings:
- teacher-only vs internal benchmark
- teacher-only vs rule baseline

Comparison key:
- `metadata.document_id`

Comparison targets:
- `sentiment_label`
- `priority_score`
- `relevance_score`
- `impact_score`
- `tags`

Matching rules:
- compare only overlapping `document_id`s
- do not fuzzy-match by title, URL, or publish time
- rows missing required comparison targets are skipped and counted

### Outputs

The harness must produce a structured report with at least:
- `reference_role`
- `candidate_role`
- `compared_documents`
- `skipped_documents`
- `sentiment_agreement`
- `priority_mae`
- `relevance_mae`
- `impact_mae`
- `tag_overlap_mean`

`tag_overlap_mean` is the mean Jaccard overlap over normalized tag sets.
Missing tags are treated as empty sets, not as hard errors.

---

## Required Sprint 6 Metrics

The minimal required metric set for Sprint 6 is:

| Metric | Meaning |
|---|---|
| `sentiment_agreement` | exact label agreement rate |
| `priority_mae` | mean absolute deviation of priority score |
| `relevance_mae` | mean absolute deviation of relevance score |
| `impact_mae` | mean absolute deviation of impact score |
| `tag_overlap_mean` | mean normalized overlap of tag sets |

These metrics are mandatory for Sprint 6.
Additional metrics may be added later, but they must not replace this minimum set.

---

## Distillation Readiness Rules

Sprint 6 is considered distillation-ready when:
- teacher-only export can be constructed from `analysis_source=external_llm`
- internal benchmark export can be constructed from `analysis_source=internal`
- rule baseline export can be constructed from `analysis_source=rule`
- evaluation harness compares datasets by `document_id`
- the minimal metric set is reported
- no training path treats INTERNAL or RULE rows as teacher data

---

## Invariants

- Teacher eligibility is determined only by `analysis_source`.
- `external_llm` is teacher-eligible.
- `internal` is benchmark-only.
- `rule` is baseline-only.
- No training on `rule` as teacher.
- No training on `internal` as teacher.
- Evaluation compares overlapping `document_id`s only.
- Sprint 6 evaluation remains offline and provider-agnostic at the dataset boundary.
