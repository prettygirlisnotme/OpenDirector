# OpenDirector development guidance

## Purpose of this document

This document applies to the entire repository. It defines the enduring product direction, architectural principles, and development path for developers and coding agents.

It describes goals and the reasoning behind decisions, not a claim that every capability has already been implemented. Do not record completion status, current defects, temporary compatibility branches, commit IDs, or test counts here. Record requirements, implementation tasks, gap analyses, and validation evidence in their respective documents; check code and tests to determine what is implemented.

## Product direction

OpenDirector enables users and agents to create videos together. **Users and agents have equivalent creative capabilities under the same business conditions and authorization**, covering asset management, content generation, media editing, candidate adoption, timeline arrangement, and video export. Both work on the same project, and each can continue editing content created by the other.

The agent is a core entry point for improving video creation efficiency. Users can express a creative goal directly; the agent uses an agent loop to break down work, select capabilities, arrange dependencies, execute operations, observe actual results, and adjust subsequent steps. This reduces individual operations, repeated input, and human waiting. Evaluate efficiency against creative requirements, quality, and resource constraints; do not substitute skipping necessary work or reducing delivery scope for an improvement.

The canvas, timeline, and editing panels support user interaction, content inspection, and direct modification. Business capabilities exist independently of these interfaces: users call them through the UI, and agents through tool use or function calling. The canvas presentation does not limit agent capabilities or steps. Preserve a consistent visual design, smooth interaction, complete model integrations, and advanced editing capabilities.

The right-hand AI assistant chat is the entry point for conversational collaboration. Users express goals, reference assets, delegate tasks, give feedback, and adjust requirements in natural language. The agent uses project context to call business capabilities and reports progress, results, and decisions needed from the user. Actual content produced by execution is synchronized to the project and canvas, where users can edit it directly and continue delegating through chat. This forms a loop of conversational delegation, execution feedback, direct editing, and continued collaboration.

Capability equivalence is based on executable business operations, rules, and results that remain editable. It does not require a tool for each button or simulated mouse gestures. Users may operate directly, delegate to an agent, or alternate between them. Authorization, locks, and acceptance requirements remain in effect; an agent does not gain decision authority beyond its authorization merely because it has a capability.

**The unified project model means that the same business capability shares commands, validation, project state, execution evidence, and receipts.** The number of interfaces, transport protocols, or presentation alone cannot demonstrate unification.

Canvas connections express generation references; the timeline expresses video arrangement. A reference relationship does not automatically execute upstream or downstream tasks and does not mean videos are concatenated in connection order. The user or agent explicitly arranges task order.

The product serves Chinese and English users by default. UI text, backend errors and status receipts, display names in tool and model catalogs, and export and download prompts are available in both languages. Language is a presentation choice and does not change business values, object identity, or project data.

## Core design principles

### 1. Users and agents have equivalent capabilities and share business services

- Consider both UI and agent entry points from contract design onward for every creative capability. Business operations in scope must be available through both; do not permanently restrict agents to generation or a few convenience tools.
- Direct user operations and conversations with the right-hand AI assistant share project context. The agent executes chat requests through business services; promises in replies must agree with actual receipts and results.
- Place core business rules in domain and application services. HTTP, UI adapters, and agent tools translate parameters, invoke services, and present results.
- Built-in agents may call application services directly; external callers may use HTTP or tool protocols. Entry points apply the same business rules.
- Design tools around creative intent, such as creating configurations, adding references, submitting generation, adopting results, and editing the timeline. Do not require one tool per button.
- Agents may use batch and composite capabilities to complete multi-step work efficiently, while users can still inspect and continue editing the objects and results. Consistent business semantics do not require identical steps, creative strategies, or open-ended generation results.
- Composite operations continue to use base capabilities and obey version, lock, resource, and transaction constraints. Different operation granularity must not bypass validation.
- Formal agent operations use business objects and stable identities, not simulated mouse input, a React Flow runtime instance, or a particular panel remaining open.

### 2. The project is the authoritative source of business facts

- Backend project storage owns persisted business facts. The browser stores reconstructible caches, UI preferences, and explicit temporary drafts, not a second independently editable complete project.
- React Flow nodes and edges are presentation projections of project objects. Optimistic interaction and local drafts are allowed, but formal results must be confirmed by server receipts.
- Business objects can exist without a visible canvas representation. Hiding an item does not delete its logical source, assets, references, or history; showing it again preserves its identity and relationships.
- Read-only queries of a source project do not request an editing session or switch the current project. Cross-project reuse registers required assets in the target project and does not depend on the source page or a temporary URL.
- Retain the project directory, file locks, atomic writes, and recoverable commit storage mechanisms. Keep runtime coordination and model credentials separate from project content.

### 3. Separate generation intent, execution facts, and current adoption

- Generation configuration and references express intent for the next creation.
- An accepted job fixes its actual assets, model, parameters, and input snapshot. Later edits do not rewrite this evidence.
- Record results, candidates, and current adoption separately. Successful generation does not automatically grant permission to overwrite current content; automatic adoption must obey an explicit policy and current-version validation.
- Applying a historical version restores the appropriate scope through an explicit business operation while preserving old execution records. Parameter restoration, result switching, and full version application each have distinct semantics.
- Express file generation, technical checks, and creative quality acceptance separately. Tool success does not mean the user has reviewed or approved the result.

### 4. Keep capabilities and rules consistent

- Model node media type, business role, and operation capability separately. Validate uploads to an existing node according to the operation's intent; do not implicitly change the node type.
- Source identity, reference relationship identity, asset identity, and content fingerprint serve different purposes. Matching names or content do not prove that two logical sources are identical.
- References support following a source's current version or pinning an asset version and resolve to deterministic inputs before execution. Text references and canvas connections jointly project actual reference relationships.
- All entry points share file probing, asset preparation, and validation foundations, with rules distinguished by business intent such as replacing content, generation references, subject slots, and timeline use.
- The backend performs final validation of model, mode, type, count, actual bytes, aggregate budget, locks, object versions, and file availability. UI prechecks provide immediate feedback.
- Derive available UI operations and rejection reasons from shared capability contracts rather than maintaining separate business rules in each node, panel, and agent.

### 5. Asynchronous operations are traceable and recoverable

- Mutations use stable operation identities and object versions where needed. Replaying the same ID with the same input returns the original receipt; the same ID with different input is rejected.
- Coordinate recovery of objects, assets, receipts, and change records across explicit commit boundaries. Do not describe partial writes as complete success.
- Model queries, previews, mutations, and long-running jobs separately. Queries do not cause creation or session-switching side effects; long-running jobs return a queryable acceptance record.
- Keep cancellation request, remote cancellation confirmation, job interruption, unknown submission, resuming the original job, and starting a new attempt distinct. Do not automatically resubmit an unknown submission with a new ID.
- Undo changes only project content that can be rolled back; it cannot undo remote calls, charges, or existing execution facts.
- Tool tasks may retain separate types and execution ledgers, but must participate in shared asset dependencies, deletion protection, provenance queries, and change notifications.
- Assets or results registered but not published to a target retain an explainable state. Retrying after failure must not silently create duplicates.

### 6. Synchronization and interaction work together

- Formal changes completed by the UI, agent, and background work enter the same recoverable notification channel. Events signal changes; project queries provide authoritative state.
- Update the interface by affected object and handle duplicate, out-of-order, and disconnected events. Fetch a fresh snapshot when changes cannot be recovered.
- Background updates preserve the user's selection, focus, playhead, and uncommitted input. Resolve conflicts by rereading, comparing, and handling them explicitly; do not automatically replace versions and overwrite content.
- Dragging and zooming use temporary client state and submit layout after the interaction. Avoid writing to disk on every pointer event, repeatedly scanning large media, or reading full history during rendering.
- Confirm business saves and UI state saves separately. A failed viewport or preference save must not change the fact that a business operation was committed.

### 7. User-visible content is bilingual by default

- Include Chinese and English when designing contracts and interactions for new features. Deliver both `zh-CN` and `en-US` entries together; do not defer translation to a later patch.
- Express UI copy, backend errors and status receipts, display names in tool and model catalogs, and export and download prompts through message catalogs or presentation-layer lookup tables. Do not hardcode either language in components, server receipts, or static data.
- Keep stable business values such as `auto`, tool IDs, node types, and voice IDs unchanged; translate only in the presentation layer. Add both languages when introducing catalog items, backend states, or error codes.
- Agent reply language follows the user's question, not the interface language; changing the interface language does not change tool behavior.
- Acceptance covers switching between Chinese and English: both languages have matching key sets, neither leaks the other language, and backend errors are readable. Deliver user-facing documentation in both languages with the feature.

## Object responsibilities

| Object | Primary responsibility |
| --- | --- |
| Project | Stable project identity, object scope, and storage lifecycle |
| Asset | Asset content, immutable content identity, fingerprint, media facts, and actual provenance |
| GenerationSpec | Editable prompts, model, parameters, and generation intent |
| Reference | Source, generation target, purpose, order, and version policy |
| CanvasItem | Presentation object, position, size, layering, grouping, and locking |
| Editing document | Editable structures such as subjects, layers, media edits, and the timeline |
| Job / tool task | Fixed inputs, status, remote identity, and result evidence for one execution |
| Result versions and adoption relationships | Candidate ownership, history, and the currently explicitly adopted result |
| Sessions and agent run records | Right-hand AI assistant conversations, reference snapshots, operation links, and execution context |
| ViewState | Client state such as selection, viewport, active editor, and playhead |

Object names may change with implementation, but responsibility boundaries must not be merged for UI convenience. Shared node interactions use shared components, typed contracts, and capability composition; each type extends only its specific content and behavior.

## Development path

The following is a long-term dependency-ordered path, not a current progress report. Build shared services, UI, and agent entry points capability by capability, verifying equivalence and creative efficiency for each. Do not adopt a long-term product split in which all canvas features are completed before only some tools are exposed.

### Path 1: Establish creative capabilities independent of interaction method

Build typed business objects, commands, queries, and execution services for asset management, references, generation, editing, adoption, timeline arrangement, export, and reopening projects. Define stable identity, versioning, receipts, provenance, history, and recovery boundaries. Capabilities must be callable without operating the canvas interface.

Acceptance focuses on a usable business workflow, complete persistence, recoverable failures, and preservation of model, media, and advanced editing capabilities.

### Path 2: Build equivalent user and agent entry points

Connect shared capabilities to the canvas, timeline, and editing panels as well as agent tools; connect user goals to agent execution through the right-hand AI assistant chat. Reuse or derive parameter schemas, capability descriptions, validation, and error semantics. The UI supports precise direct operations, while tools support operations and composition based on creative intent.

Acceptance focuses on equivalent entry points for business operations in scope and consistent business semantics for the same intent, input, and constraints, with no necessary creation step available only by clicking the UI.

### Path 3: Form a collaboration loop that can resume at any time

Users and agents query and modify the same objects. Assets, configurations, references, candidates, and timelines produced by agents are actual project content that users can inspect, modify, and use for further delegation. Before continuing, the agent reads relevant new state and respects user changes and locks.

Integrate right-hand AI assistant sessions, drafts, message reference snapshots, and agent run records. Users can express goals, give feedback, adjust requirements, and continue execution in chat. Collapsing the chat retains sessions and accepted jobs; stopping a reply, stopping subsequent orchestration, and canceling submitted jobs have distinct semantics. Continue within existing user authorization; request a decision only when exceeding that scope or when a creative choice is needed, not mechanically for every tool call.

Acceptance focuses on direct operations and chat delegation being interchangeable and resumable, chat progress matching the actual project, and neither party losing content, bypassing locks, or adopting results incorrectly. Users should not need to rebuild work already completed by the agent.

### Path 4: Improve end-to-end video creation efficiency

The agent selects capabilities and arranges task dependencies for the creative goal, processes independent work in batches or in parallel when permitted, observes actual results, and adjusts subsequent steps. Reduce repeated operations, human follow-up while waiting, and unnecessary rework to improve efficiency from creative intent to usable video. Composite orchestration reuses base operations rather than creating a separate asset graph or execution state outside the project.

Provide agent-callable execution entry points for necessary business steps such as export, with clear conditions for client or background execution. If requirements include autonomously exporting the final video after the browser closes, provide a renderer callable in the background. If browser composition is retained, accurately state where client participation is required. The ability of tools to modify the timeline does not establish that background rendering exists.

Acceptance focuses on output that actually meets creative requirements, recoverable jobs, explainable results and adoption scope, and measurements of end-to-end time, human interventions, repeated operations, and resource consumption for comparable tasks. Tool-call success or a greater number of calls alone does not demonstrate improved efficiency.

## Implementation and validation constraints

- Before changing or reviewing code, read the [development lessons](docs/development/lessons.md), verify the current implementation and tests, and apply checks relevant to the change. When fixing a defect, trace the affected business path and check related entry points; record verifiable new risks or recurring problems as described in the lessons.
- Start with a modular monolith, clear service boundaries, and recoverable background tasks. Do not introduce microservices, a separate message queue, or a general workflow platform without a real need.
- Preserve OpenDirector's interface, model protocols, adapters, scheduling capabilities, and advanced editing experience. When adopting an external design, describe business semantics directly and adapt them to the object boundaries above; do not depend on another repository or copy a coupled implementation.
- Fix shared services, contracts, or components first, then add type-specific logic as needed. Avoid entry-point-specific exceptions that bypass rules.
- Switch a default entry point only when the complete creation workflow is ready. Transitional adapters need explicit boundaries and exit conditions; do not maintain two independently editable sources of business truth indefinitely.
- Do not build legacy-data migration or compatibility layers by default. Reject unsupported formats explicitly, preserve original projects and credentials, and do not automatically delete or overwrite them.
- Validation covers actual inputs, persisted objects, execution snapshots, and outputs; checking only that a node, reference label, or connection appears is insufficient.
- Capability acceptance covers both user and agent entry points, including editing, adoption, timeline, and export. Efficiency acceptance uses real end-to-end tasks; do not claim the goal is met from tool counts or a partial demo alone.
- Select tests according to change risk: shared rules and type boundaries, command idempotency and conflicts, failure recovery, equivalent UI and agent operations, and representative real media editing and export scenarios.
- Distinguish mocked models, real file validation, and real service calls. Performance conclusions require comparable measurements; completion reports accurately state validation scope and limitations.
- Production studios, complete scriptwriting and full-series delivery systems, additional model capabilities, and background rendering engines require explicit requirements; do not expand scope automatically because of the unified project model.
- When adding or changing user-visible content, follow the new-feature checklist in [Bilingual interface](docs/development/i18n.md) and run `npm run check:i18n`. Automated checks alone are insufficient; switch between both languages and inspect the new flow.

## Documentation maintenance

Update this document when architectural principles or development direction change; do not maintain implementation progress here. The [documentation index and maintenance rules](docs/README.md) index the formal documentation, which is delivered with the code through Git.

- [Unified project architecture](docs/architecture/unified-project.md) describes objects, business boundaries, and execution semantics.
- [Roadmap](docs/development/roadmap.md) describes capability dependencies, delivery scope, and acceptance thresholds.
- [Validation guide](docs/development/validation.md) describes reproducible checks and evidence requirements.
- [Bilingual interface](docs/development/i18n.md) describes language selection, message organization, review entry points, and checks.

Maintain current tasks, defects, and execution progress in issues, PRs, and topic records where needed. The documentation index links only to files included in Git; local development materials and neighboring repositories must not become required reading for other developers. Explain relevant designs and capabilities directly in this repository rather than using another product's name in place of specific semantics.
