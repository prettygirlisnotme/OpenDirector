# Development lessons

This record highlights risks identified from actual fixes. It does not imply that the same defects remain in the current code. Consult relevant lessons before a change and verify the current implementation and tests; the [validation guide](validation.md) defines commands and evidence requirements.

When implementing or reviewing a change, trace the affected behavior from its entry point to persisted state and side effects, and inspect related entry points for the same defect when relevant. Apply the checks that fit the change and record verification and remaining gaps as described in the validation guide.

When work exposes a consequential risk or a recurring problem, add a lesson or update an existing one with the trigger, failure, suggested check, and a verifiable issue, PR, or commit. Add evidence when a problem recurs. Move requirements proven durable in practice into the validation guide, and revise or remove outdated lessons. Do not count distinct defects as repetitions of the same problem, or add a lesson for every routine fix.

## Model capabilities and requests can diverge

- **Trigger:** Creating a generation node, configuring model capabilities, or constructing a provider request.
- **Lesson:** Defaults, reference slots, and request parameters can fail at different layers. Commit `f623fedd` corrected node defaults that did not match the adapter; `f9e358cf` filled the last-frame slot for models that support it; `1bafbbd6` corrected an integer duration sent to Ark as a float.
- **Check:** Trace representative input from the relevant UI or Agent entry point through backend validation to the provider request. Check that unsupported input does not create a job. Distinguish protocol mocks from real service evidence.

## Operations across boundaries can lose identity or context

- **Trigger:** Importing a project, referencing assets across conversation turns, or switching projects during a run.
- **Lesson:** Commit `e8c29c9b` fixed provenance and creation-order problems in a canvas archive round trip; `44857448` fixed asset context across turns; `00d8899c` fixed Agent runs stopping when switching projects. These are different defects, but each shows that the current display alone cannot establish state and context.
- **Check:** As relevant to the change, inspect persisted object identities, references, and execution inputs after conversion or recovery. Reopen the project or reconnect to the run when needed, and verify the actual result.

## Baseline failures still need tracking

- **Trigger:** A relevant test fails and comparison shows that it also fails on the baseline.
- **Lesson:** Commits `28d61dce` and `0a5630ac` each recorded the same generation test failing on the baseline. That historical record does not establish whether the test still fails today.
- **Check:** Record the versions and results used in the comparison. Link an existing issue or create one when appropriate. Report the check as failed; a baseline failure is not a pass.
