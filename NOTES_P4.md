# ShareSafe P4 - AI/ML #2 Development Notes

## Owner: P4 (AI/ML #2)
**Brief**: Custodian Agent, LLM layer, Policy Compiler, Adversarial Suite.

## What is Built:
- In progress: Initial scaffolding and LLM wrapper.

## Architecture & Principles:
- Hard boundary: Bounded Autonomy. Agents only execute safe-direction/reversible actions via `app.core.authority.execute()`.
- Deterministic core: Everything operates fully when LLM is turned off or keys are absent.
- User input is untrusted data: Never in decision pathways.
- LLM output is enrichment only.
