# KAI Persona — Source-of-Truth Documentation

This folder holds the **canonical persona documentation** for KAI — Kinetic Artificial Intelligence.

> **KAI ist kein Bild mehr. KAI ist ein sichtbares, zustandsgetriebenes, auditierbares UI-Wesen im System.**
> — Der zentrale Satz

## Files

| File | Purpose |
|---|---|
| `prompt_bibel_v1.md` | **Master identity bible**: name, motto, character, voice, visual DNA, mimics, state library, image-generation prompts (master + variants + GIFs), evolution plan Phase 1–6 |
| `creative_implementation_pack_v3_1.md` | Creative direction & integration patterns |
| `technical_ui_pack_v3_2.md` | TS-types, state machine spec, CSS tokens, component contracts, JSON-Schema |
| `asset_production_pack_v3_3.md` | Asset list (PNG/GIF/WebM/Voice) with production prompts |
| `final_execution_prompt_v3_4.md` | Master implementation directive — acceptance criteria + strict rules |
| `identitaets_ebenen.md` | 7 identity layers (personality, visual DNA, voice, behavior, avatar variants, dashboard/telegram presence, future talking figure) |
| `der_zentrale_satz.txt` | The central sentence |

## Master-Decision (2026-05-03)

**Mode:** Hybrid with image anchor.
**Visual anchor:** `web/public/assets/kai/master/kai_master_v1.png` (= `KAI-Persona/Me_KAI.png`).
**Variations:** Allowed and expected per state — face/identity is fixed by master image, hair-wildness/grin/eye-glow-color/posture vary per state.

## Implementation Layout

```text
config/
  kai_persona.yaml         # Single-source-of-truth runtime config (state machine, phrases, templates)
  kai_assets_manifest.json # Asset paths + status (available/placeholder) + fallback strategy

web/public/assets/kai/     # Static assets (master, states, motion, voice, talking_avatar)
web/src/kai/               # Frontend KAI engine (types, resolver, phrases, guards, mappers) — Phase A
web/src/components/kai/    # Frontend KAI UI components — Phase A
app/messaging/kai_*        # Backend KAI engine (Python pendant for Telegram/server) — Phase B
app/audit/kai_*            # Audit service — Phase B

docs/kai_persona/          # ← you are here. Read-only reference.
```

## Single Source of Truth Rule

When something contradicts:
1. `config/kai_persona.yaml` (runtime) wins for **runtime behavior** (states, phrases, colors, templates).
2. `prompt_bibel_v1.md` wins for **identity, voice, visual DNA** (when generating new assets).
3. The visual anchor `kai_master_v1.png` wins for **face / silhouette / outfit** (when generating new state variants).

If you find a discrepancy: open a doc-pull-request explaining which side should change.

## Restricted

- Never edit these files in-place to "fix" something for a single use-case. They are versioned canonical specs.
- Every asset added must be registered in `kai_assets_manifest.json` with explicit `status` (`available` / `placeholder`).
- Every claim about an asset must be backed by a real file. Do not lie about availability.

## Persona non grata

> Nicht eingeladen. Trotzdem im System.
> Nicht dekorativ. Sondern wach.
> Nicht bequem. Sondern notwendig.
