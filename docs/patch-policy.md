# Patch policy

How TES3X decides which engine fixes to port, what they default to, and what evidence moves a fix
from idea to default. The fixes themselves are listed in [patches.md](patches.md) (implemented)
and [candidates.md](candidates.md) (not implemented).

## Categories and defaults

Every fix has one category, and the category sets its default:

| category | what it covers | default |
|---|---|---|
| `core` | crashes, hangs, save corruption, data loss, out-of-bounds and lifetime errors | In `standard` once verified. |
| `correctness` | clear formula, persistence, scripting or display defects that do not redesign the game | In `standard` once verified, and still selectable one by one. |
| `compat` | support a particular mod or scripting pattern needs | Only in profiles that use that mod. |
| `performance` | less CPU, disk or memory work | Opt in until an original Xbox shows a win and no regression. |
| `instrumentation` | measurement, tracing and crash reports | Opt in, for testing builds. |
| `qol` | interface, controls and conveniences that do not retune the game | Opt in: these are preferences, even when another project calls one a bug fix. |
| `balance` | deliberate changes to mechanics, AI, economy, progression or exploits | Opt in, and never in `standard`. |

Each fix stays independently selectable. Fixes from other projects are named after their source,
such as `mcp-97` for Morrowind Code Patch fix 97.

## Deciding what to port

1. **Another project's fix is a lead, not a decision.** Port a fix only after showing that the
   Xbox build has the defect and that fixing it helps on the Xbox. The engines share code, so most
   PC fixes are worth a look, but platform differences make some unnecessary or impossible.
2. **The source's category is not ours.** Some of MCP's "Bug fixes" deliberately change balance,
   AI or progression; they are categorised by what they do.
3. **Be conservative, as OpenMW is.** Fix crashes, corruption and severe design errors by default;
   make arguable vanilla behaviour and balance changes optional.
4. **Split mixed fixes.** A real defect and a rebalance that another project shipped together get
   separate entries and separate switches.
5. **Prefer an ini switch.** When a fix's code is already installed, let `[Xbox]` keys in
   `Morrowind.ini` turn it off or tune it, so players can change their mind without repatching.
6. **Understand the Xbox code.** PC patch bytes never transplant, and the interface, input,
   renderer and save paths often differ from the PC's.
7. **Keep content fixes as mods.** Record, script and asset fixes belong in ordinary mods, which
   users supply. TES3X does not redistribute third-party files without permission.
8. **Record every decision.** Each reviewed fix gets an entry in `candidates.toml` with its reason,
   including decisions not to port it, so nobody has to repeat the investigation.
9. **One fix per commit**: its table entry, any payload code, its notes and its proof, so it can be
   reverted on its own.

## From candidate to default

A fix advances only with the matching evidence:

| from | to | evidence |
|---|---|---|
| `candidate` | `researching` | The upstream description, a minimal way to reproduce it, and the likely subsystem. |
| `researching` | `located` | The PC intent, the Xbox function and site, its calling convention, and the planned change. |
| `located` | `implemented` | An independently selectable patch that matches the retail XBE and passes structural checks. Move the entry from `candidates.toml` to `patches.toml`. |
| `implemented` | `verified-xemu` | A [proof record](verification.md): the reproducer fails without the patch and passes with it. |
| `verified-xemu` | `verified-hardware` | The same on an original Xbox. Performance decisions use this, never xemu timing. |

A fix that turns out not to apply becomes `not-applicable`, `infeasible` or `rejected`, with the
reason.

## Notes on the candidate list

- MWSE kept fixing the original engine after MCP. Its code is MWSE-specific, but each entry gives a
  way to reproduce the defect and the intent of the fix.
- Performance candidates stay opt in until timed on the Xbox's 733 MHz CPU. A faster PC version is
  not automatically faster on the Xbox, and memory cost matters as much as CPU cost with 64 MB.
- Priorities are a first estimate and change as fixes are reproduced.
