# OpenDirector documentation

This directory is the entry point for the repository's shared documentation. The documents below are tracked with the code; developers do not need local historical material, chat records, or neighboring repositories to use them.

## Reading order and responsibilities

| Document | What it covers | When to update it |
| --- | --- | --- |
| [Development guidance](../AGENTS.md) | Product direction and enduring principles | Architecture principles or product boundaries change |
| [Unified project architecture](architecture/unified-project.md) | Business objects, shared entry points, and interaction rules | Object responsibilities, commands, persistence, or interaction rules change |
| [Roadmap](development/roadmap.md) | Capability dependencies and delivery criteria | Scope, dependencies, or acceptance criteria change |
| [Validation guide](development/validation.md) | Reproducible checks and evidence requirements | Tools, runtime requirements, or validation entry points change |
| [Development lessons](development/lessons.md) | Risks identified from recent fixes | New evidence emerges or a lesson no longer applies |
| [macOS installation validation](development/macos-install-validation.md) | Installation and usage scenarios verified on Apple Silicon | The corresponding acceptance checks are repeated |
| [Bilingual interface](development/i18n.md) | Language selection, message organization, and checks | Language policy, message structure, or checks change |
| [Python installation and packaging](development/packaging.md) | Installation, platform wheel builds, and isolated verification | Packaging, resource paths, or build process changes |
| [Business CLI](guides/cli.md) | Querying, editing, and generating through a terminal or Agent Shell | Commands, authorization, or receipt semantics change |
| [Video model capabilities](guides/video-model-capabilities.md) | Declaring output specifications supported by a service | Configuration fields or capability integration changes |
| [Agent runtime](architecture/agent-runtime.md) | Agent runtime architecture | Runtime architecture changes |

The root [README](../README.md) introduces the product and examples and links to quick-start and documentation. See [installation and updates](guides/installation.md) ([中文](guides/installation.zh-CN.md)) for installation, and the [user guide](guides/user-guide.md) ([中文](guides/user-guide.zh-CN.md)) for configuration, creation, and troubleshooting. The README does not contain the full manual, implementation architecture, development tests, research sources, or iteration history. Source code and generated contracts describe the implementation; architecture and roadmap documents describe goals and do not establish that a capability is already available.

## Directory conventions

```text
AGENTS.md                 Long-term development guidance
README.md                 Product and getting-started entry point
docs/
  README.md               Documentation index and maintenance rules
  architecture/           Architecture, business semantics, and design decisions
  development/            Roadmap, development process, and validation
  guides/                 Instructions for users and integrators
```

Historical material and temporary artifacts do not automatically become official documentation. Ignore rules reserve the root of `docs/` for local historical files; put new shared documents in the maintained subdirectories above and explicitly add them to Git. Do not restore all old directories or delete local material merely to tidy the index.

## Maintenance rules

1. **Give each fact one primary home.** Put enduring principles in the development guidance, architecture semantics in architecture documentation, and operational instructions in guides. Link from other documents to avoid divergent copies.
2. **Separate goals, plans, and facts.** Architecture documents do not track progress; the roadmap records dependencies and gates. Prefer issues, PRs, and commit descriptions for task status, defects, and validation results.
3. **Add focused records when needed.** Document the context, decision, alternatives, and impact of important design choices. Standalone validation records should include the code version, environment, commands, results, and limitations. Create them when there is substantive content, not as empty templates or placeholder lists.
4. **Make explanations self-contained.** Describe designs and capabilities directly, without using another product's name as a substitute for business rules or relying on chat context, neighboring directories, or untracked files.
5. **Update documentation with capability changes.** Changes to objects, APIs, model parameters, or save and recovery semantics should update the relevant documents in the same change. Do not leave outdated conclusions presented as current behavior.
6. **Ship link targets with the repository.** Local targets should already be tracked or included in the same commit. Include needed images and examples; do not link to temporary files on a developer's machine.
7. **Keep records precise.** Distinguish test doubles from real services and intended designs from verified behavior. A historical regression result does not guarantee later versions.
8. **Stage explicit files.** Review the actual diff and stage the required documents rather than adding whole directories that may contain local history.

For documentation changes, check relative links, code and command paths, formatting, and tracking scope. Documentation-only changes do not require unrelated model, media, or browser tests.
