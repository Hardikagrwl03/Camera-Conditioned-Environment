# Plans

Design documents, one per feature, written before the work and kept afterwards as the record of
what was decided and why. Each was implemented by referencing it directly, so they are written to
be followed rather than skimmed.

They are listed in the order they were written; later plans assume the ones above them.

| Plan | What it covers |
|---|---|
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | The original build: the manual Camera2 sweep engine, the session directory layout, and the manifest / CSV records. |
| [UI_FIX_PLAN.md](UI_FIX_PLAN.md) | Correcting the squeezed preview aspect ratio, matching the device theme, and recovering the invisible capture button. |
| [CONFIG_UX_PLAN.md](CONFIG_UX_PLAN.md) | Moving configuration onto the main screen, one editable attribute at a time. |
| [UI_POLISH_PLAN.md](UI_POLISH_PLAN.md) | The attribute tab grid and the partial-screen editing popups. |
| [FOCUS_SAMPLING_PLAN.md](FOCUS_SAMPLING_PLAN.md) | Focus as List or Uniform, sampled across distance bands with the boundaries always captured. |
| [WHITE_BALANCE_PLAN.md](WHITE_BALANCE_PLAN.md) | White balance as a fourth sweep axis, AUTO by default so existing captures are unchanged. |

Where a plan was not followed exactly, the deviation and its reason are recorded in the plan
itself rather than left to the commit history — see the two notes in `WHITE_BALANCE_PLAN.md`, which
record why the colour-gain derivation and the AUTO filenames departed from the original design.

The titles inside the older four still say "Frame Sampler", the app's name before it became
Lattice. They are left as written: they are a record of a decision made at a point in time, and
rewriting them would misrepresent when it was taken.
