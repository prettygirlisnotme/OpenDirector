/** React Flow is a projection. These adapters translate typed editor/media
 * values; no browser URL is ever put into the authoritative project. */
import type {
  Asset,
  AssetRef,
  CanvasItem,
  EditorDocument,
  EditorDocumentInput,
  GenerationSpec,
  GenerationSpecInput,
  ObjectVersion,
  ProjectSnapshot,
  ReferenceInput,
  TimelineResource,
} from "../contracts/generated.ts";
import {
  AUDIO_CARD_SIZE,
  createDocument,
  createNode,
  DEFAULT_SETTINGS,
  type CanvasDocument,
  type CanvasNode,
  type CanvasResource,
  type GenerationSettings,
} from "../canvas/model.ts";
import { tNow } from "../i18n/messages.ts";
import {
  createLocalStage,
  parseLocalStageValue,
} from "../canvas/stageDocument.ts";
import { mediaBlobDataUrl } from "../canvas/localMediaRuntime.ts";
import { generationContentPatch } from "../canvas/generationReferenceContent.ts";
import { assemblyResource } from "../canvas/editorAssembly.ts";
import { RULE_FACTS } from "../contracts/ruleFacts.generated.ts";
import { canvasReferenceRole, MUSIC_SECONDS } from "./rules.ts";
import {
  assetReference,
  CreationClient,
  CreationCommandError,
  currentAsset,
  isCurrentCanvasReference,
  itemSpec,
  objectVersion,
  visibleItems,
  type CreationOperation,
} from "./creationClient.ts";

const same = (a: unknown, b: unknown) =>
  JSON.stringify(a) === JSON.stringify(b);
const presentationFields = [
  "title",
  "width",
  "height",
  "zIndex",
  "locked",
  "parentId",
] as const;
const extension: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
  "image/svg+xml": "svg",
  "video/mp4": "mp4",
  "audio/mpeg": "mp3",
  "audio/wav": "wav",
};

export class CreationProjection {
  baseline!: CanvasDocument;
  basis!: ProjectSnapshot;
  private hydrated?: { snapshot: ProjectSnapshot; document: CanvasDocument };
  private imported = new Map<string, Promise<AssetRef>>();
  private importHandles = new Map<string, string>();
  private masks = new Map<string, Promise<string>>();
  readonly client: CreationClient;
  constructor(client: CreationClient) {
    this.client = client;
  }

  async resource(ref: AssetRef, title?: string): Promise<CanvasResource> {
    const asset = this.client.snapshot.assets.find(
      (a) => a.id === ref.assetId,
    )!;
    return {
      id: asset.id,
      assetId: asset.id,
      byteSize: asset.metadata.size,
      kind: asset.kind as CanvasResource["kind"],
      title: title ?? asset.name,
      src: await this.client.mediaUrl(asset.id).catch(() => undefined),
      mimeType: asset.metadata.mimeType,
      width: asset.metadata.width ?? undefined,
      height: asset.metadata.height ?? undefined,
      durationSeconds: asset.metadata.durationSeconds ?? undefined,
    };
  }
  async importResource(resource: {
    id?: string;
    src?: string;
    title: string;
    kind: Asset["kind"];
    text?: string;
    identity?: string;
    fresh?: boolean;
    previous?: AssetRef;
    /** Archive provenance: the backend records an imported source instead of
     * treating the upload as a plain user file. */
    importedFrom?: "generated" | "edited";
  }) {
    const existing = resource.src && this.client.assetForSource(resource.src);
    if (existing && !resource.fresh) return assetReference(existing);
    const retained =
      resource.id &&
      this.client.snapshot.assets.find(
        (asset) => asset.id === resource.id && asset.kind === resource.kind,
      );
    if (retained && !resource.src) return assetReference(retained);
    // The archive source only widens the key when set: `editorData` seeds this
    // cache with `::<kind>:<src>`, so the plain form must stay byte-identical.
    const base = `${resource.identity ?? ""}:${resource.previous?.assetId ?? ""}:${resource.kind}:${resource.src ?? resource.text ?? ""}`;
    const key = resource.importedFrom
      ? `${base}:${resource.importedFrom}`
      : base;
    let pending = this.imported.get(key);
    if (!pending) {
      pending = (async () => {
        if (resource.kind === "text")
          return this.client.writeText(
            resource.title,
            resource.text ?? "",
            resource.previous,
          );
        if (!resource.src) throw new Error(tNow("errors.backend.mediaEmpty"));
        const response = await fetch(resource.src);
        if (!response.ok) throw new Error(tNow("errors.backend.mediaReadFailed"));
        const blob = await response.blob();
        const suffix = extension[blob.type] ?? blob.type.split("/")[1] ?? "bin";
        const name = /\.[\w]+$/.test(resource.title)
          ? resource.title
          : `${resource.title}.${suffix}`;
        const handle = this.importHandles.get(key) ?? crypto.randomUUID();
        this.importHandles.set(key, handle);
        return this.client.importBlob(
          blob,
          name,
          resource.kind,
          handle,
          resource.importedFrom,
        );
      })().catch((error) => {
        this.imported.delete(key);
        throw error;
      });
      this.imported.set(key, pending);
    }
    const imported = await pending;
    if (resource.src && !resource.identity)
      this.client.rememberSource(resource.src, imported.assetId);
    return imported;
  }

  /** The archive source a generation node's media carries, if any.
   * Without it the media is imported as a plain upload and the generation
   * binding would claim an output the project has no evidence for. */
  private archiveSource(
    node: CanvasNode,
  ): "generated" | "edited" | undefined {
    const evidence = node.data.generationProvenance;
    if (!evidence) return undefined;
    if (evidence.source === "generated") return "generated";
    if (evidence.source === "edited" || evidence.source === "rendered")
      return "edited";
    return undefined;
  }

  private maskSource(ref: AssetRef) {
    const key = `${ref.assetId}:${ref.fingerprint}`;
    let pending = this.masks.get(key);
    if (!pending) {
      pending = this.client
        .blob(ref.assetId)
        .then((blob) => mediaBlobDataUrl(blob, new AbortController().signal))
        .catch((error) => {
          this.masks.delete(key);
          throw error;
        });
      this.masks.set(key, pending);
    }
    return pending;
  }

  async editorData(doc: EditorDocument): Promise<Partial<CanvasNode["data"]>> {
    switch (doc.kind) {
      case "subject":
        return {
          subject: {
            description: doc.data.description,
            category: doc.data.category,
            ...(doc.data.librarySource
              ? { librarySource: { ...doc.data.librarySource } }
              : {}),
            main: doc.data.main
              ? await this.resource(doc.data.main)
              : undefined,
            auxiliaries: await Promise.all(
              doc.data.auxiliaries.map((r) => this.resource(r)),
            ),
            voice: doc.data.voice
              ? await this.resource(doc.data.voice)
              : undefined,
          },
        };
      case "timeline":
        return {
          timeline: {
            fps: doc.data.fps,
            width: doc.data.width,
            height: doc.data.height,
            importedAssets: await Promise.all(
              doc.data.importedAssets.map((r) =>
                this.resource(r.asset, r.title),
              ),
            ),
            tracks: await Promise.all(
              doc.data.tracks.map(async (track) => ({
                ...track,
                clips: await Promise.all(
                  track.clips.map(async (clip) => ({
                    ...clip,
                    start: clip.start ?? undefined,
                    resource: await this.resource(
                      clip.resource.asset,
                      clip.resource.title,
                    ),
                  })),
                ),
              })),
            ),
          },
        };
      case "stack":
        return { stack: { ...doc.data, nodeIds: [...doc.data.nodeIds] } };
      case "tool":
        return {
          tool: {
            ...doc.data,
            fields: doc.data.fields.map((field) => ({
              ...field,
              values: [...field.values],
            })),
            provenance: doc.data.provenance ?? undefined,
            palette: doc.data.palette
              ? {
                  name: doc.data.palette.name ?? undefined,
                  sourceImageUri: doc.data.palette.sourceImage
                    ? await this.client.mediaUrl(
                        doc.data.palette.sourceImage.assetId,
                      )
                    : undefined,
                }
              : undefined,
          },
        };
      case "director":
        return {
          external: { externalType: "director-stage" },
          localStage: parseLocalStageValue({
            format: "opendirector-local-stage",
            version: 2,
            ...doc.data,
          }),
        };
      case "layers":
        return {
          layers: {
            format: "opendirector-layers",
            version: 1,
            ...doc.data,
            layers: await Promise.all(
              doc.data.layers.map(async (layer) => ({
                ...layer,
                mask: layer.mask.map((stroke) => ({
                  ...stroke,
                  points: stroke.points.map((p) => ({ ...p })),
                })),
                content:
                  layer.content.kind === "image"
                    ? {
                        kind: "image" as const,
                        width: layer.content.width,
                        height: layer.content.height,
                        src: await this.client.mediaUrl(
                          layer.content.asset.assetId,
                        ),
                      }
                    : layer.content,
              })),
            ),
          },
        };
      case "image_crop":
        return {
          imageCrop: {
            src: await this.client.mediaUrl(doc.data.source.assetId),
            options: doc.data.options,
            width: doc.data.width,
            height: doc.data.height,
          },
        };
      case "media_edit": {
        // A changed source starts a fresh edit; keep the old document in history
        // rather than applying its mask to unrelated pixels of the same size.
        const ids = doc.data.sourceItems;
        if (
          !ids.length ||
          ids.some((id, index) => {
            const item = this.client.snapshot.canvasItems.find(
              (item) => item.id === id,
            );
            const actual = item && currentAsset(this.client.snapshot, item);
            return (
              actual?.id !== doc.data.sources[index]?.assetId ||
              actual?.fingerprint !== doc.data.sources[index]?.fingerprint
            );
          })
        )
          return {};
        const selection = doc.data.selection;
        const maskDataUrl = selection?.mask
          ? await this.maskSource(selection.mask)
          : undefined;
        if (selection?.mask && maskDataUrl)
          this.imported.set(
            `::image:${maskDataUrl}`,
            Promise.resolve(selection.mask),
          );
        return {
          taskDraft: {
            version: 1,
            tool: doc.data.tool,
            sourceNodeIds: [...ids],
            parameters: { ...doc.data.parameters },
            ...(selection
              ? {
                  selection: {
                    sourceNodeId: ids[0]!,
                    width: selection.width,
                    height: selection.height,
                    boxes: selection.boxes.map((box) => ({ ...box })),
                    ...(selection.mask
                      ? {
                          maskDataUrl,
                        }
                      : {}),
                  },
                }
              : {}),
          },
        };
      }
    }
  }
  /** A visibility-only receipt can reuse already hydrated nodes, including
   * hidden sources. Any business/content/layout change takes the full read path. */
  private visibilityProjection(
    snapshot: ProjectSnapshot,
  ): CanvasDocument | undefined {
    const cached = this.hydrated;
    if (!cached || snapshot.project.id !== cached.document.id) return;
    for (const key of [
      "project",
      "assets",
      "generationSpecs",
      "references",
      "editorDocuments",
      "jobs",
      "results",
    ] as const) {
      if (!same(snapshot[key], cached.snapshot[key])) return;
    }
    if (snapshot.canvasItems.length !== cached.snapshot.canvasItems.length)
      return;
    const previous = new Map(
      cached.snapshot.canvasItems.map((item) => [item.id, item]),
    );
    let changed = false;
    for (const item of snapshot.canvasItems) {
      const old = previous.get(item.id);
      if (
        !old ||
        !same(
          { ...old, visible: true, revision: 0 },
          { ...item, visible: true, revision: 0 },
        )
      )
        return;
      changed ||= old.visible !== item.visible;
    }
    if (!changed) return;
    const nodes = new Map(
      [...cached.document.nodes, ...(cached.document.hiddenNodes ?? [])].map(
        (node) => [node.id, node],
      ),
    );
    const visible = visibleItems(snapshot);
    const previouslyVisible = new Set(
      cached.document.nodes.map((node) => node.id),
    );
    // A source's visibility can change the content of another node's prompt.
    if (
      snapshot.references.some(
        (ref) =>
          isCurrentCanvasReference(ref, visible) !==
          isCurrentCanvasReference(ref, previouslyVisible),
      )
    )
      return;
    const ordered: CanvasNode[] = [];
    for (const item of snapshot.canvasItems) {
      const node = nodes.get(item.id);
      if (!node || (visible.has(item.id) && node.data.assetState === "missing"))
        return;
      ordered.push(node);
    }
    return {
      ...cached.document,
      nodes: ordered.filter((node) => visible.has(node.id)),
      hiddenNodes: ordered.filter((node) => !visible.has(node.id)),
      edges: cached.document.edges.map((edge) => ({
        ...edge,
        ...(this.client.referenceStates.has(edge.id)
          ? { referenceState: this.client.referenceStates.get(edge.id) }
          : {}),
      })),
    };
  }

  private rememberProjection(
    snapshot: ProjectSnapshot,
    document: CanvasDocument,
  ) {
    // Save acknowledgements advance the editing basis before hydration finishes.
    // Only a completed read may seed this cache, and late reads cannot roll it back.
    if (
      !this.hydrated ||
      snapshot.eventCursor >= this.hydrated.snapshot.eventCursor
    )
      this.hydrated = { snapshot, document };
    return document;
  }

  async read(snapshot = this.client.snapshot): Promise<CanvasDocument> {
    const visibility = this.visibilityProjection(snapshot);
    if (visibility) return this.rememberProjection(snapshot, visibility);
    const document = {
      ...createDocument(snapshot.project.name),
      id: snapshot.project.id,
    };
    const view = await this.client.view();
    document.viewport = view.viewport;
    document.viewPersisted = this.client.hasPersistedView();
    const visible = visibleItems(snapshot);
    const currentReferences = snapshot.references.filter((ref) =>
      isCurrentCanvasReference(ref, visible),
    );
    const currentReferenceById = new Map(
      currentReferences.map((ref) => [ref.id, ref]),
    );
    document.nodes = await Promise.all(
      snapshot.canvasItems.map(async (item) => {
        const node = {
          ...createNode(item.kind, item.position),
          id: item.id,
          width: item.width,
          height: item.height,
          locked: item.locked,
          zIndex: item.zIndex,
          parentId: item.parentId ?? undefined,
        };
        node.data.title = item.title;
        // Audio cards are a fixed control strip; older square cards are
        // compacted on read instead of waiting for a playback event.
        if (node.type === "audio") node.height = AUDIO_CARD_SIZE.height;
        const asset = currentAsset(snapshot, item);
        if (asset) {
          try {
            if (asset.kind === "text")
              node.data.text = await this.client.textContent(asset.id);
            else node.data.src = await this.client.mediaUrl(asset.id);
            node.data.assetState = "ready";
          } catch {
            node.data.assetState = "missing";
          }
          node.data.mimeType = asset.metadata.mimeType;
          node.data.byteSize = asset.metadata.size;
          node.data.backendAssetId = asset.id;
          node.data.mediaRole =
            item.binding.role === "material" ? "asset" : "generation";
          // Export records this so an imported generation node can be rebuilt
          // with real provenance instead of a fabricated one.
          const evidence = asset.provenance;
          node.data.generationProvenance = {
            source: evidence.source,
            ...(evidence.jobId ? { jobId: evidence.jobId } : {}),
            ...(evidence.inputFingerprint
              ? { inputFingerprint: evidence.inputFingerprint }
              : {}),
            ...(evidence.editorDocumentId
              ? { editorDocumentId: evidence.editorDocumentId }
              : {}),
            ...(evidence.editorRevision != null
              ? { editorRevision: evidence.editorRevision }
              : {}),
          };
          node.data.media = {
            width: asset.metadata.width ?? undefined,
            height: asset.metadata.height ?? undefined,
            durationSeconds: asset.metadata.durationSeconds ?? undefined,
          };
        } else if (item.binding.role === "empty" && item.kind === "text")
          node.data.text = item.binding.manualText ?? "";
        const spec = itemSpec(snapshot, item);
        if (spec) {
          node.data.prompt = spec.prompt;
          if (spec.promptContent) {
            node.data.promptContent = [];
            let prompt = "";
            const mentions: NonNullable<CanvasNode["data"]["promptMentions"]> =
              [];
            for (const part of spec.promptContent) {
              if (part.type === "text") {
                prompt += part.text;
                node.data.promptContent.push({ type: "text", text: part.text });
              } else {
                const ref = currentReferenceById.get(part.referenceId);
                if (!ref) continue;
                const source =
                  ref.binding === "follow_current"
                    ? snapshot.canvasItems.find((i) => i.id === ref.source.id)
                    : undefined;
                const asset =
                  ref.binding === "pinned"
                    ? snapshot.assets.find((a) => a.id === ref.source.assetId)
                    : undefined;
                const label = `@${source?.title ?? asset?.name ?? tNow("errors.backend.referenceFallback")}`;
                node.data.promptContent.push({
                  type: "reference",
                  assetId:
                    ref.binding === "follow_current"
                      ? `node:${ref.source.id}`
                      : ref.source.assetId,
                  ...(ref.binding === "follow_current"
                    ? { nodeId: ref.source.id }
                    : { binding: "asset" as const }),
                  referenceId: ref.id,
                  name: source?.title ?? asset?.name ?? tNow("errors.backend.referenceFallback"),
                  fingerprint: asset?.fingerprint ?? null,
                });
                mentions.push({
                  ...(source ? { sourceNodeId: source.id } : {}),
                  referenceId: ref.id,
                  start: prompt.length,
                  end: prompt.length + label.length,
                });
                prompt += label;
              }
            }
            node.data.prompt = prompt;
            node.data.promptMentions = mentions;
          }
          const located = new Set(
            node.data.promptContent?.flatMap((part) =>
              part.type === "reference" && part.referenceId
                ? [part.referenceId]
                : [],
            ) ?? [],
          );
          const pins = currentReferences.flatMap((ref) => {
            if (
              ref.targetSpecId !== spec.id ||
              ref.binding !== "pinned" ||
              located.has(ref.id)
            )
              return [];
            const asset = snapshot.assets.find(
              (asset) => asset.id === ref.source.assetId,
            );
            return asset
              ? [
                  {
                    type: "reference" as const,
                    assetId: asset.id,
                    referenceId: ref.id,
                    binding: "asset" as const,
                    name: asset.name,
                    fingerprint: asset.fingerprint,
                  },
                ]
              : [];
          });
          if (pins.length) {
            const body =
              node.data.promptContent ??
              (node.data.prompt
                ? [{ type: "text" as const, text: node.data.prompt }]
                : []);
            Object.assign(
              node.data,
              generationContentPatch([
                ...pins.flatMap((part, index) => [
                  ...(index ? [{ type: "text" as const, text: " " }] : []),
                  part,
                ]),
                ...(body.length
                  ? [{ type: "text" as const, text: "\n" }, ...body]
                  : []),
              ]),
            );
          }
          if (spec.kind === "text")
            node.data.agentSettings = {
              modelConfigId: spec.modelConfigId ?? undefined,
              ...spec.parameters,
            };
          else
            node.data.settings = {
              ...DEFAULT_SETTINGS,
              mode: spec.kind,
              modelConfigId: spec.modelConfigId ?? undefined,
              prompt: node.data.prompt,
              count: view.generationCounts?.[item.id] ?? 1,
              ...(spec.kind === "image"
                ? {
                    aspectRatio: spec.parameters.aspectRatio ?? "auto",
                    resolution:
                      spec.parameters.size === "auto"
                        ? "auto"
                        : spec.parameters.size,
                    imagePreset: spec.parameters.preset,
                  }
                : {}),
              ...(spec.kind === "video"
                ? {
                    videoMode:
                      spec.parameters.videoMode === "first_frame"
                        ? "first-last"
                        : spec.parameters.videoMode,
                    durationSeconds:
                      spec.parameters.durationSeconds ??
                      DEFAULT_SETTINGS.durationSeconds,
                    aspectRatio: spec.parameters.aspectRatio ?? "auto",
                    audioEnabled: spec.parameters.generateAudio,
                    resolution:
                      spec.parameters.resolution === "auto"
                        ? "auto"
                        : spec.parameters.resolution,
                    frameSlotsInitialized: true,
                  }
                : {}),
              ...(spec.kind === "audio"
                ? {
                    voiceName: spec.parameters.voice ?? undefined,
                    audioCreationType: spec.parameters.creationType,
                    audioMode: spec.parameters.mode,
                    ...(spec.parameters.creationType === "music"
                      ? {
                          lyrics: spec.parameters.lyrics,
                          scoreMode: spec.parameters.scoreMode ?? "melody",
                          durationFromReference:
                            spec.parameters.durationFromReference ?? undefined,
                          durationSeconds:
                            spec.parameters.durationSeconds ??
                            MUSIC_SECONDS.initial,
                        }
                      : {}),
                  }
                : {}),
            };
          if (spec.kind === "video" && node.data.settings) {
            const frames = currentReferences.filter(
              (ref) =>
                ref.targetSpecId === spec.id &&
                (ref.role === "first_frame" || ref.role === "last_frame") &&
                ref.binding === "follow_current",
            );
            for (const frame of frames)
              if (frame.binding === "follow_current")
                node.data.settings[
                  frame.role === "last_frame"
                    ? "lastFrameNodeId"
                    : "firstFrameNodeId"
                ] = frame.source.id;
          }
          const selected = snapshot.results.find(
            (r) => r.id === spec.currentResultId,
          );
          const job = snapshot.jobs.find((j) => j.id === selected?.jobId);
          if (job && asset) {
            const references: Record<string, string> = {};
            for (const ref of job.inputSnapshot.references) {
              if (ref.sourceVersion?.objectType === "canvas_item")
                references[ref.sourceVersion.id] = ref.asset.fingerprint;
            }
            node.data.generationEvidence = {
              output:
                asset.kind === "text"
                  ? (node.data.text ?? "")
                  : (node.data.src ?? ""),
              references,
            };
          }
        }
        if (item.binding.role === "editor") {
          const binding = item.binding;
          const doc = snapshot.editorDocuments.find(
            (d) => d.id === binding.objectRef.id,
          )!;
          Object.assign(node.data, await this.editorData(doc));
        }
        if ("editors" in item)
          for (const ref of item.editors) {
            const doc = snapshot.editorDocuments.find((d) => d.id === ref.id)!;
            Object.assign(node.data, await this.editorData(doc));
          }
        return node;
      }),
    );
    document.edges = snapshot.references.flatMap((ref) => {
      if (!ref.active) return [];
      const sourceId =
        ref.binding === "follow_current"
          ? ref.source.id
          : ref.sourceVersion?.id;
      if (
        !sourceId ||
        !snapshot.canvasItems.some((item) => item.id === sourceId)
      )
        return [];
      return snapshot.canvasItems
        .filter((item) => itemSpec(snapshot, item)?.id === ref.targetSpecId)
        .map((item) => ({
          id: ref.id,
          source: sourceId,
          referenceBinding: ref.binding,
          target: item.id,
          intent: "generation_input" as const,
          referenceState: this.client.referenceStates.get(ref.id),
        }));
    });
    for (const node of document.nodes) {
      const item = snapshot.canvasItems.find((item) => item.id === node.id)!;
      if (item.binding.role !== "editor") continue;
      const docId = item.binding.objectRef.id;
      const editor = snapshot.editorDocuments.find((doc) => doc.id === docId)!;
      for (const connection of editor.inputConnections ?? []) {
        if (
          !snapshot.canvasItems.some(
            (source) => source.id === connection.sourceId,
          )
        )
          continue;
        const resource = assemblyResource(node, connection.slotId);
        if (resource) resource.nodeId = connection.sourceId;
        document.edges.push({
          id: connection.id,
          source: connection.sourceId,
          target: node.id,
          intent: "assembly_input",
          assemblySlotId: connection.slotId,
        });
      }
    }
    for (const item of snapshot.canvasItems) {
      const asset = currentAsset(snapshot, item);
      const sourceId = asset?.provenance.sourceItemId;
      if (sourceId && snapshot.canvasItems.some((i) => i.id === sourceId))
        document.edges.push({
          id: `publication:${item.id}:${asset!.id}`,
          source: sourceId,
          target: item.id,
          intent: "post_edit_result",
        });
    }
    document.hiddenNodes = document.nodes.filter(
      (node) => !visible.has(node.id),
    );
    document.nodes = document.nodes.filter((node) => visible.has(node.id));
    return this.rememberProjection(snapshot, document);
  }
  async open() {
    await this.client.open();
    this.baseline = await this.read();
    this.basis = this.client.snapshot;
    return this.baseline;
  }

  private specInput(
    node: CanvasNode,
    current?: GenerationSpec,
  ): GenerationSpecInput {
    const common = {
      id: current?.id ?? crypto.randomUUID(),
      projectId: this.client.id,
      revision: current?.revision ?? 1,
      purpose: current?.purpose ?? "draft",
      modelConfigId:
        (node.type === "text"
          ? node.data.agentSettings?.modelConfigId
          : node.data.settings?.modelConfigId) ?? null,
      prompt: node.data.prompt,
      promptContent: current?.promptContent ?? null,
      currentResultId: current?.currentResultId ?? null,
    };
    switch (node.type) {
      case "text":
        return {
          ...common,
          kind: "text",
          parameters: {
            thinking: node.data.agentSettings?.thinking ?? "auto",
            maxTokens:
              node.data.agentSettings?.maxTokens ??
              RULE_FACTS.textDefaultMaxTokens,
          },
        };
      case "image":
        return {
          ...common,
          kind: "image",
          parameters: {
            size:
              !node.data.settings?.resolution ||
              // Empty/tool placeholder nodes still use the shared "1024"
              // initializer, which means no explicit output selection.
              node.data.settings.resolution === "1024" ||
              node.data.settings.resolution === "auto"
                ? "auto"
                : node.data.settings.resolution,
            aspectRatio:
              !node.data.settings?.aspectRatio ||
              node.data.settings.aspectRatio === "auto"
                ? null
                : node.data.settings.aspectRatio,
            preset: node.data.settings?.imagePreset ?? "none",
            responseFormat: "url",
            outputFormat: "png",
          },
        };
      case "audio":
        return {
          ...common,
          kind: "audio",
          parameters: {
            voice: node.data.settings?.voiceName ?? null,
            creationType: node.data.settings?.audioCreationType ?? "audio",
            mode: node.data.settings?.audioMode ?? "tts",
            ...(node.data.settings?.audioCreationType === "music"
              ? {
                  lyrics: node.data.settings.lyrics ?? "",
                  scoreMode: node.data.settings.scoreMode ?? "melody",
                  durationFromReference:
                    node.data.settings.durationFromReference ?? null,
                  durationSeconds:
                    node.data.settings.durationSeconds ?? MUSIC_SECONDS.initial,
                }
              : {}),
          },
        };
      case "video": {
        const settings = node.data.settings;
        return {
          ...common,
          kind: "video",
          parameters: {
            videoMode:
              settings?.videoMode === "first-last"
                ? "first_frame"
                : (settings?.videoMode ?? "reference"),
            durationSeconds: settings?.durationSeconds ?? null,
            aspectRatio:
              settings?.aspectRatio === "auto"
                ? null
                : (settings?.aspectRatio ?? null),
            generateAudio: settings?.audioEnabled ?? true,
            resolution:
              settings?.resolution === "auto"
                ? "auto"
                : (settings?.resolution ?? "auto"),
          },
        };
      }
      default:
        throw new Error(tNow("errors.backend.unsupportedGeneration"));
    }
  }

  private async editorInput(
    node: CanvasNode,
    kind: EditorDocument["kind"],
    current?: EditorDocument,
    edges: CanvasDocument["edges"] = [],
    nodes: readonly CanvasNode[] = [],
  ): Promise<EditorDocumentInput> {
    const common = {
      id: current?.id ?? crypto.randomUUID(),
      projectId: this.client.id,
      revision: current?.revision ?? 1,
      inputConnections: await Promise.all(
        edges
          .filter(
            (edge) =>
              edge.target === node.id && edge.intent === "assembly_input",
          )
          .map(async (edge) => {
            const resource = assemblyResource(node, edge.assemblySlotId);
            if (!resource)
              throw new Error(tNow("errors.backend.assemblyMissing"));
            const asset = await this.importResource(resource);
            return {
              id: edge.id,
              sourceId: edge.source,
              asset,
              slotId:
                node.type === "subject" && edge.assemblySlotId !== "main"
                  ? asset.assetId
                  : edge.assemblySlotId!,
            };
          }),
      ),
    };
    const ref = (resource: CanvasResource) => this.importResource(resource);
    switch (kind) {
      case "media_edit": {
        const task = node.data.taskDraft!;
        const sources = task.sourceNodeIds.map((id) => {
          const source = nodes.find((item) => item.id === id);
          const asset =
            source?.data.src && this.client.assetForSource(source.data.src);
          if (!asset)
            throw new Error(tNow("errors.backend.toolSourceMissing"));
          return assetReference(asset);
        });
        const selection = task.selection;
        return {
          ...common,
          inputConnections: [],
          kind,
          data: {
            tool: task.tool,
            sources,
            sourceItems: [...task.sourceNodeIds],
            parameters: { ...task.parameters },
            selection: selection
              ? {
                  source: sources[0]!,
                  width: selection.width,
                  height: selection.height,
                  boxes: selection.boxes?.map((box) => ({ ...box })) ?? [],
                  mask: selection.maskDataUrl
                    ? await this.importResource({
                        kind: "image",
                        title: tNow("errors.backend.toolSelectionFile"),
                        src: selection.maskDataUrl,
                      })
                    : null,
                }
              : null,
          },
        };
      }
      case "subject": {
        // A partially populated node must not throw while projecting the canvas.
        const data = node.data.subject ?? {
          description: "",
          category: "",
          auxiliaries: [],
        };
        return {
          ...common,
          kind,
          data: {
            description: data.description,
            category: data.category,
            librarySource: data.librarySource ?? null,
            main: data.main ? await ref(data.main) : null,
            auxiliaries: await Promise.all(data.auxiliaries.map(ref)),
            voice: data.voice ? await ref(data.voice) : null,
          },
        };
      }
      case "timeline": {
        const resource = async (
          r: CanvasResource,
        ): Promise<TimelineResource> => ({
          asset: await ref(r),
          title: r.title,
          durationSeconds: r.durationSeconds ?? null,
        });
        const data = node.data.timeline!;
        return {
          ...common,
          kind,
          data: {
            fps: data.fps,
            width: data.width,
            height: data.height,
            importedAssets: await Promise.all(
              (data.importedAssets ?? []).map(resource),
            ),
            tracks: await Promise.all(
              data.tracks.map(async (track) => ({
                ...track,
                clips: await Promise.all(
                  track.clips.map(async (clip) => ({
                    ...clip,
                    start: clip.start ?? null,
                    resource: await resource(clip.resource),
                  })),
                ),
              })),
            ),
          },
        };
      }
      case "stack":
        return { ...common, kind, data: node.data.stack! };
      case "tool": {
        const data = node.data.tool!;
        return {
          ...common,
          kind,
          data: {
            kind: data.kind,
            fields: data.fields,
            provenance: data.provenance ?? null,
            palette: data.palette
              ? {
                  name: data.palette.name ?? null,
                  sourceImage: data.palette.sourceImageUri
                    ? await this.importResource({
                        kind: "image",
                        title: data.palette.name ?? tNow("errors.backend.paletteSourceTitle"),
                        src: data.palette.sourceImageUri,
                      })
                    : null,
                }
              : null,
          },
        };
      }
      case "director": {
        const {
          format: _format,
          version: _version,
          ...data
        } = node.data.localStage ?? createLocalStage();
        return { ...common, kind, data };
      }
      case "image_crop": {
        const crop = node.data.imageCrop!;
        return {
          ...common,
          kind,
          data: {
            source: await this.importResource({
              kind: "image",
              src: crop.src,
              title: node.data.title,
            }),
            options: crop.options,
            width: crop.width,
            height: crop.height,
          },
        };
      }
      case "layers": {
        const {
          format: _format,
          version: _version,
          ...data
        } = node.data.layers!;
        return {
          ...common,
          kind,
          data: {
            ...data,
            layers: await Promise.all(
              data.layers.map(async (layer) => ({
                ...layer,
                content:
                  layer.content.kind === "image"
                    ? {
                        kind: "image",
                        width: layer.content.width,
                        height: layer.content.height,
                        asset: await this.importResource({
                          kind: "image",
                          src: layer.content.src,
                          title: layer.name,
                        }),
                      }
                    : layer.content,
              })),
            ),
          },
        };
      }
      default:
        throw new Error(tNow("errors.backend.editorCommand"));
    }
  }

  /** Compare the edited view to the view it was based on, then send explicit
   * operations with that basis' versions. Server events never silently grant a
   * stale form permission to overwrite newer business fields. */
  async prepare(document: CanvasDocument) {
    if (!this.baseline || document.id !== this.client.id)
      throw new Error(tNow("errors.backend.openUnifiedProject"));
    const basis = this.basis;
    const visible = visibleItems(basis);
    const oldNodes = new Map(
      [...this.baseline.nodes, ...(this.baseline.hiddenNodes ?? [])].map(
        (n) => [n.id, n],
      ),
    );
    const items = new Map(basis.canvasItems.map((i) => [i.id, i]));
    const operations: CreationOperation[] = [];
    const editedOutputs = new Map<
      string,
      { source: AssetRef; asset: AssetRef }
    >();
    const versions = new Map<string, ObjectVersion>();
    const expect = (
      type: ObjectVersion["objectType"],
      obj: { id: string; revision: number },
    ) => {
      versions.set(`${type}:${obj.id}`, objectVersion(type, obj));
    };
    const addReference = (reference: ReferenceInput) => {
      const previous = basis.references.find((ref) => ref.id === reference.id);
      if (previous) {
        expect("reference", previous);
        operations.push({
          type: "reference.restore",
          payload: {
            reference: {
              ...previous,
              ...reference,
              active: true,
              revision: previous.revision,
            },
          },
        });
      } else {
        operations.push({ type: "reference.add", payload: { reference } });
      }
    };
    const specs = new Map<string, string>();
    const moves: { itemId: string; position: { x: number; y: number } }[] = [];
    const writableNodes = [
      ...document.nodes,
      ...(document.hiddenNodes ?? []).filter((node) => !items.has(node.id)),
    ];
    // A child names its parent group, so the group has to exist first. The
    // document order is visual, not structural, and a batch is applied in
    // order: creating a member before its group fails with missing_object.
    for (const node of parentsFirst(writableNodes)) {
      const old = oldNodes.get(node.id),
        item = items.get(node.id);
      if (
        item &&
        !this.baseline.nodes.some((visible) => visible.id === item.id)
      ) {
        expect("canvas_item", item);
        operations.push({
          type: "canvas.show",
          payload: { item: { ...item, visible: true } },
        });
      }
      if (old && same(old, node)) {
        const spec = item && itemSpec(basis, item);
        if (spec) specs.set(node.id, spec.id);
        continue;
      }
      if (item) expect("canvas_item", item);
      let binding: CanvasItem["binding"] | undefined = item?.binding;
      const isMedia = ["text", "image", "video", "audio", "file"].includes(
        node.type,
      );
      if (isMedia) {
        const materialChanged =
          node.type === "text"
            ? node.data.text !== undefined &&
              node.data.text !== old?.data.text &&
              (!!node.data.text || !!old?.data.text)
            : !!node.data.src && node.data.src !== old?.data.src;
        if (materialChanged) {
          const previous =
            item && node.type === "text"
              ? currentAsset(basis, item)
              : undefined;
          const layerEdit =
            item &&
            node.type === "image" &&
            (node.data.layers || node.data.imageCrop);
          const source = layerEdit ? currentAsset(basis, item) : undefined;
          const copiedSource =
            !item && node.data.copiedSourceId
              ? items.get(node.data.copiedSourceId)
              : undefined;
          const copiedAsset =
            !item && node.type === "image"
              ? (basis.assets.find(
                  (asset) =>
                    asset.id === node.data.backendAssetId &&
                    asset.kind === "image",
                ) ??
                (copiedSource &&
                oldNodes.get(copiedSource.id)?.data.src === node.data.src
                  ? currentAsset(basis, copiedSource)
                  : undefined))
              : undefined;
          if (copiedSource) {
            expect("canvas_item", copiedSource);
            const copiedSpec = itemSpec(basis, copiedSource);
            if (copiedSpec) expect("generation_spec", copiedSpec);
          }
          const ref = copiedAsset
            ? assetReference(copiedAsset)
            : await this.importResource({
                kind: node.type as Asset["kind"],
                title: node.data.title,
                src: node.data.src,
                text: node.data.text,
                identity: layerEdit
                  ? `${node.id}:edit:${JSON.stringify(node.data.layers ?? node.data.imageCrop)}`
                  : node.type === "text"
                    ? node.id
                    : undefined,
                previous: previous ? assetReference(previous) : undefined,
                // A canvas archive carried this generation node's picture in
                // with the source it was exported from. Declaring it here lets
                // the backend keep the generation binding without inventing a
                // job, which a plain upload could never justify. Only a node
                // the project does not hold yet can come from an archive: an
                // existing item's new picture (a crop or layer edit output, a
                // replaced file) is an ordinary upload, and marking it as
                // imported makes the backend reject the edit.
                importedFrom: item ? undefined : this.archiveSource(node),
              });
          if (layerEdit && source) {
            editedOutputs.set(node.id, {
              source: assetReference(source),
              asset: ref,
            });
            expect(
              "asset",
              this.client.snapshot.assets.find(
                (asset) => asset.id === ref.assetId,
              )!,
            );
          } else if (item)
            operations.push({
              type: "canvas.materialize",
              payload: {
                itemId: item.id,
                asset: ref,
                discardGenerationDraft: true,
              },
            });
          binding =
            layerEdit && source
              ? item.binding
              : item?.binding.role === "generation" && node.type === "text"
                ? { ...item.binding, materialOverride: ref }
                : { role: "material", objectRef: ref };
        }
        if (node.type !== "file") {
          const current = item ? itemSpec(basis, item) : undefined;
          const configureText =
            node.type === "text" && !!node.data.agentSettings?.modelConfigId;
          const cloneGeneration =
            !item &&
            binding?.role === "material" &&
            node.data.mediaRole === "generation" &&
            // Only an archive that recorded where the picture came from may
            // rebuild the generation binding; a bare upload cannot be one.
            !!this.archiveSource(node);
          if (
            binding?.role !== "material" ||
            configureText ||
            cloneGeneration
          ) {
            const spec = {
              ...this.specInput(node, current),
              ...(cloneGeneration ? { purpose: "generation" as const } : {}),
            };
            specs.set(node.id, spec.id);
            if (!current)
              operations.push({
                type: "generationSpec.create",
                payload: { spec },
              });
            else if (!same(spec, this.specInput(old ?? node, current))) {
              expect("generation_spec", current);
              operations.push({
                type: "generationSpec.update",
                payload: { spec },
              });
            }
            if (cloneGeneration && binding?.role === "material")
              // The node is shown with this binding, so it must already be the
              // generation one: the backend refuses to convert a material item
              // afterwards (material items cannot be converted directly to generation items).
              binding = {
                role: "generation",
                objectRef: { objectType: "generation_spec", id: spec.id },
                materialOverride: binding.objectRef,
              };
            else if (!binding || binding.role === "empty")
              binding = {
                role: "empty",
                draftSpec: { objectType: "generation_spec", id: spec.id },
                manualText: null,
              };
            else if (binding.role === "material" && configureText) {
              if (!item)
                binding = { role: "material", objectRef: binding.objectRef };
              // Preserve handwritten content while explicitly enabling Agent.
              operations.push({
                type: "canvas.configure",
                payload: {
                  itemId: node.id,
                  specId: spec.id,
                  preserveMaterial: true,
                },
              });
              if (current) expect("generation_spec", current);
            }
          }
        }
        binding ??= { role: "empty", draftSpec: null, manualText: null };
      } else if (node.type !== "group") {
        const kind = node.type === "external" ? "director" : node.type;
        const editorBinding =
          item?.binding.role === "editor" ? item.binding : undefined;
        const current = editorBinding
          ? basis.editorDocuments.find(
              (d) => d.id === editorBinding.objectRef.id,
            )
          : undefined;
        const doc = await this.editorInput(
          node,
          kind as EditorDocument["kind"],
          current,
          document.edges,
        );
        if (!current)
          operations.push({
            type: "editor.create",
            payload: { document: doc },
          });
        else {
          const previous = await this.editorInput(
            old ?? node,
            kind as EditorDocument["kind"],
            current,
            this.baseline.edges,
          );
          if (!same(doc, previous)) {
            expect("editor_document", current);
            operations.push({
              type: "editor.save",
              payload: { document: doc },
            });
          }
        }
        binding = {
          role: "editor",
          objectRef: {
            objectType: "editor_document",
            id: doc.id,
            kind: doc.kind,
          },
        };
      } else binding = { role: "container" };
      if (!item) {
        // Show precedes configure in a new text material+Agent transaction.
        const configureIndex = operations.findIndex(
          (op) =>
            op.type === "canvas.configure" && op.payload.itemId === node.id,
        );
        const show: CreationOperation = {
          type: "canvas.show",
          payload: {
            item: {
              id: node.id,
              projectId: this.client.id,
              kind: node.type,
              title: node.data.title,
              position: node.position,
              width: node.width,
              height: node.height,
              zIndex: node.zIndex ?? 0,
              locked: node.locked ?? false,
              parentId: node.parentId ?? null,
              binding,
            } as CanvasItem,
          },
        };
        if (configureIndex >= 0) operations.splice(configureIndex, 0, show);
        else operations.push(show);
        if (!document.nodes.some((visible) => visible.id === node.id))
          operations.push({
            type: "canvas.hide",
            payload: { itemId: node.id },
          });
      } else {
        const presentation = {
          title: node.data.title,
          width: node.width,
          height: node.height,
          zIndex: node.zIndex ?? 0,
          locked: node.locked ?? false,
          parentId: node.parentId ?? null,
        };
        const oldPresentation = {
          title: old?.data.title,
          width: old?.width,
          height: old?.height,
          zIndex: old?.zIndex ?? 0,
          locked: old?.locked ?? false,
          parentId: old?.parentId ?? null,
        };
        const changedFields = presentationFields.filter(
          (field) => !same(presentation[field], oldPresentation[field]),
        );
        if (changedFields.length)
          operations.push({
            type: "canvas.update",
            payload: {
              itemId: node.id,
              ...Object.fromEntries(
                changedFields.map((field) => [field, presentation[field]]),
              ),
            },
          });
        if (!same(node.position, old?.position))
          moves.push({ itemId: node.id, position: node.position });
      }
    }
    // Retained image editors and the published output share one commit.
    for (const node of writableNodes.filter((node) =>
      ["image", "video", "audio"].includes(node.type),
    )) {
      const item = items.get(node.id);
      const output = editedOutputs.get(node.id);
      for (const [field, kind] of [
        ["layers", "layers"],
        ["imageCrop", "image_crop"],
        ["taskDraft", "media_edit"],
      ] as const) {
        const attached =
          item && "editors" in item
            ? item.editors.find((ref) => ref.kind === kind)
            : undefined;
        const current = attached
          ? basis.editorDocuments.find((doc) => doc.id === attached.id)
          : undefined;
        const old = oldNodes.get(node.id);
        const state = node.data[field];
        if (!(output && state) && same(state, old?.data[field])) continue;
        if (state) {
          const next = await this.editorInput(
            node,
            kind,
            current,
            [],
            [...document.nodes, ...(document.hiddenNodes ?? [])],
          );
          if (current) expect("editor_document", current);
          if (item) expect("canvas_item", item);
          operations.push(
            current
              ? { type: "editor.save", payload: { document: next } }
              : { type: "editor.create", payload: { document: next } },
          );
          if (!attached)
            operations.push({
              type: "canvas.attachEditor",
              payload: { itemId: node.id, editorId: next.id },
            });
          if (output && kind !== "media_edit")
            operations.push({
              type: "canvas.applyEdit",
              payload: { itemId: node.id, editorId: next.id, ...output },
            });
        } else if (attached && item) {
          expect("canvas_item", item);
          operations.push({
            type: "canvas.detachEditor",
            payload: { itemId: item.id, editorId: attached.id },
          });
        }
      }
    }
    if (moves.length)
      operations.push({ type: "canvas.move", payload: { items: moves } });
    for (const { id } of this.baseline.nodes)
      if (!document.nodes.some((n) => n.id === id)) {
        const item = items.get(id)!;
        expect("canvas_item", item);
        operations.push({ type: "canvas.hide", payload: { itemId: id } });
      }
    const oldEdges = new Map(this.baseline.edges.map((e) => [e.id, e]));
    // Hiding a node retains its business references. Explicit removals must
    // precede additions so replacing a relation to the same source in one
    // save does not have its new ID discarded as a duplicate.
    const stillVisible = new Set(
      [...document.nodes, ...(document.hiddenNodes ?? [])].map((n) => n.id),
    );
    for (const edge of this.baseline.edges)
      if (
        !document.edges.some((e) => e.id === edge.id) &&
        stillVisible.has(edge.source) &&
        stillVisible.has(edge.target)
      ) {
        const ref = basis.references.find((r) => r.id === edge.id);
        if (ref) {
          expect("reference", ref);
          const spec = basis.generationSpecs.find(
            (s) => s.id === ref.targetSpecId,
          )!;
          expect("generation_spec", spec);
          operations.push({
            type: "reference.remove",
            payload: { referenceId: ref.id },
          });
        }
      }
    for (const edge of document.edges) {
      if (edge.intent === "assembly_input") {
        if (!oldEdges.has(edge.id)) {
          const source = items.get(edge.source);
          if (source) expect("canvas_item", source);
        }
        continue;
      }
      if (
        edge.intent === "post_edit_result" ||
        edge.referenceBinding === "pinned" ||
        oldEdges.has(edge.id)
      )
        continue;
      const target = items.get(edge.target),
        current = target && itemSpec(basis, target);
      const specId = specs.get(edge.target) ?? current?.id;
      if (!specId) throw new Error(tNow("errors.backend.materialReference"));
      if (current) expect("generation_spec", current);
      if (target) expect("canvas_item", target);
      const existingOrders = basis.references
        .filter((r) => r.active && r.targetSpecId === specId)
        .map((r) => r.order);
      const addedOrders = operations.flatMap((op) =>
        (op.type === "reference.add" || op.type === "reference.restore") &&
        op.payload.reference.targetSpecId === specId
          ? [op.payload.reference.order ?? 0]
          : [],
      );
      const reference: ReferenceInput = {
        id: edge.id,
        projectId: this.client.id,
        targetSpecId: specId,
        binding: "follow_current",
        source: { id: edge.source },
        order: Math.max(-1, ...existingOrders, ...addedOrders) + 1,
        role: "reference",
      };
      addReference(reference);
    }
    for (const node of document.nodes.filter((node) => node.type === "video")) {
      const target = items.get(node.id),
        specId =
          specs.get(node.id) ??
          (target ? itemSpec(basis, target)?.id : undefined);
      if (!specId) continue;
      const settings = node.data.settings;
      for (const ref of basis.references.filter(
        (ref) => ref.active && ref.targetSpecId === specId,
      )) {
        if (
          operations.some(
            (op) =>
              op.type === "reference.remove" &&
              op.payload.referenceId === ref.id,
          )
        )
          continue;
        const sourceKind =
          ref.binding === "follow_current"
            ? items.get(ref.source.id)?.kind
            : ref.source.kind;
        const role = canvasReferenceRole(settings, sourceKind, ref);
        if (role !== ref.role) {
          expect("reference", ref);
          operations.push({
            type: "reference.update",
            payload: { reference: { ...ref, role } },
          });
        }
      }
      for (let index = 0; index < operations.length; index++) {
        const op = operations[index]!;
        if (
          (op.type === "reference.add" || op.type === "reference.restore") &&
          op.payload.reference.targetSpecId === specId
        ) {
          const ref = op.payload.reference;
          const sourceKind =
            ref.binding === "follow_current"
              ? (document.nodes.find((item) => item.id === ref.source.id)
                  ?.type ?? items.get(ref.source.id)?.kind)
              : ref.source.kind;
          const role = canvasReferenceRole(settings, sourceKind, ref);
          operations[index] = {
            type: op.type,
            payload: { reference: { ...ref, role } },
          };
        }
      }
    }
    for (const node of document.nodes) {
      const old = oldNodes.get(node.id),
        specId =
          specs.get(node.id) ??
          (items.has(node.id)
            ? itemSpec(basis, items.get(node.id)!)?.id
            : undefined);
      if (
        !specId ||
        (node.data.prompt === old?.data.prompt &&
          same(node.data.promptContent, old?.data.promptContent) &&
          same(node.data.promptMentions, old?.data.promptMentions))
      )
        continue;
      const previousMentions = new Set(
        old?.data.promptContent?.flatMap((part) =>
          part.type === "reference" && part.referenceId
            ? [part.referenceId]
            : [],
        ) ?? [],
      );
      const nextMentions = new Set(
        node.data.promptContent?.flatMap((part) =>
          part.type === "reference" && part.referenceId
            ? [part.referenceId]
            : [],
        ) ?? [],
      );
      // Fixed references have no canvas edge, so their removals are derived
      // from the prompt and must also precede any replacement additions.
      for (const ref of basis.references.filter(
        (ref) =>
          isCurrentCanvasReference(ref, visible) &&
          ref.targetSpecId === specId &&
          node.data.promptContent !== undefined &&
          (ref.binding === "pinned" || previousMentions.has(ref.id)) &&
          !nextMentions.has(ref.id),
      )) {
        if (
          operations.some(
            (op) =>
              op.type === "reference.remove" &&
              op.payload.referenceId === ref.id,
          )
        )
          continue;
        expect("reference", ref);
        operations.push({
          type: "reference.remove",
          payload: { referenceId: ref.id },
        });
      }
      for (const part of node.data.promptContent ?? []) {
        if (
          part.type !== "reference" ||
          part.binding !== "asset" ||
          !part.referenceId
        )
          continue;
        if (
          basis.references.some(
            (ref) =>
              ref.id === part.referenceId &&
              ref.targetSpecId === specId &&
              ref.active,
          )
        )
          continue;
        if (
          operations.some(
            (op) =>
              (op.type === "reference.add" ||
                op.type === "reference.restore") &&
              op.payload.reference.id === part.referenceId,
          )
        )
          continue;
        const asset = basis.assets.find((asset) => asset.id === part.assetId);
        if (
          !asset ||
          (part.fingerprint && part.fingerprint !== asset.fingerprint)
        )
          throw new Error(tNow("errors.backend.fixedReferenceMissing"));
        const orders = [
          ...basis.references
            .filter((ref) => ref.active && ref.targetSpecId === specId)
            .map((ref) => ref.order),
          ...operations.flatMap((op) =>
            (op.type === "reference.add" || op.type === "reference.restore") &&
            op.payload.reference.targetSpecId === specId
              ? [op.payload.reference.order ?? 0]
              : [],
          ),
        ];
        addReference({
          id: part.referenceId,
          projectId: this.client.id,
          targetSpecId: specId,
          binding: "pinned",
          source: assetReference(asset),
          order: Math.max(-1, ...orders) + 1,
        });
      }
      const content: (
        | { type: "text"; text: string }
        | { type: "reference"; referenceId: string }
      )[] = [];
      let cursor = 0;
      for (const mention of node.data.promptMentions ?? []) {
        const edge = document.edges.find(
          (edge) =>
            edge.source === mention.sourceNodeId && edge.target === node.id,
        );
        const existing = basis.references.find(
          (ref) =>
            ref.active &&
            ref.targetSpecId === specId &&
            (ref.id === mention.referenceId ||
              (ref.binding === "follow_current" &&
                ref.source.id === mention.sourceNodeId)),
        );
        const pending = operations.find(
          (op) =>
            (op.type === "reference.add" || op.type === "reference.restore") &&
            op.payload.reference.id === mention.referenceId &&
            op.payload.reference.targetSpecId === specId,
        );
        const referenceId =
          edge?.id ??
          existing?.id ??
          (pending &&
          (pending.type === "reference.add" ||
            pending.type === "reference.restore")
            ? pending.payload.reference.id
            : undefined);
        const removed = operations.some(
          (op) =>
            op.type === "reference.remove" &&
            op.payload.referenceId === referenceId,
        );
        if (
          !referenceId ||
          removed ||
          mention.start < cursor ||
          mention.end > node.data.prompt.length
        )
          continue;
        if (mention.start > cursor)
          content.push({
            type: "text",
            text: node.data.prompt.slice(cursor, mention.start),
          });
        content.push({ type: "reference", referenceId });
        cursor = mention.end;
      }
      if (cursor < node.data.prompt.length)
        content.push({ type: "text", text: node.data.prompt.slice(cursor) });
      const spec = basis.generationSpecs.find((spec) => spec.id === specId);
      if (spec) expect("generation_spec", spec);
      operations.push({
        type: "generationSpec.setPrompt",
        payload: { specId, prompt: node.data.prompt, promptContent: content },
      });
    }
    return { operations, versions: [...versions.values()] };
  }

  /** Retry a rejected layout change only when its fields still match the
   * original basis (or already equal our intent). Generated media and unrelated
   * presentation changes can then arrive without trapping a drag in stale CAS.
   * Content edits and changes to hierarchy, visibility or locks keep their CAS.
   */
  private rebasePresentation(
    operations: CreationOperation[],
    versions: readonly ObjectVersion[],
  ): ObjectVersion[] | undefined {
    if (
      !operations.every(
        (op) => op.type === "canvas.move" || op.type === "canvas.update",
      ) ||
      versions.some((version) => version.objectType !== "canvas_item")
    )
      return;
    const before = new Map(
      this.basis.canvasItems.map((item) => [item.id, item]),
    );
    const current = new Map(
      this.client.snapshot.canvasItems.map((item) => [item.id, item]),
    );
    for (const version of versions) {
      const old = before.get(version.id),
        next = current.get(version.id);
      if (
        !old ||
        !next ||
        old.revision !== version.revision ||
        old.kind !== next.kind ||
        old.visible !== next.visible ||
        old.locked !== next.locked ||
        old.parentId !== next.parentId
      )
        return;
    }
    const unchanged = (
      id: string,
      field: keyof CanvasItem,
      intended: unknown,
    ) => {
      const old = before.get(id),
        next = current.get(id);
      return (
        old &&
        next &&
        (same(old[field], next[field]) || same(intended, next[field]))
      );
    };
    for (const op of operations) {
      if (op.type === "canvas.move") {
        if (
          op.payload.items.some(
            (item) => !unchanged(item.itemId, "position", item.position),
          )
        )
          return;
      } else if (op.type === "canvas.update") {
        if (
          presentationFields.some(
            (field) =>
              field in op.payload &&
              !unchanged(op.payload.itemId, field, op.payload[field]),
          )
        )
          return;
      }
    }
    return versions.map((version) =>
      objectVersion("canvas_item", current.get(version.id)!),
    );
  }

  viewError = "";
  async save(document: CanvasDocument, options: { saveView?: boolean } = {}) {
    // Run inside the caller's save queue, after any pending acknowledgement.
    // A no-op must not advance the editing basis to unseen server changes.
    const unchanged =
      this.baseline &&
      document.id === this.baseline.id &&
      (document === this.baseline ||
        same(
          [document.nodes, document.hiddenNodes, document.edges],
          [this.baseline.nodes, this.baseline.hiddenNodes, this.baseline.edges],
        ));
    if (unchanged) {
      // The business content is already stored. Only a changed viewport or
      // generation count needs the separate preference write.
      if (options.saveView !== false) await this.saveView(document);
      return false;
    }
    const { operations, versions } = await this.prepare(document);
    let expected = versions;
    for (let attempt = 0; ; attempt++) {
      try {
        await this.client.batch(operations, expected);
        break;
      } catch (error) {
        // command() refreshes after a definitive receipt. A lost response or
        // any other rejection must keep its original delivery and edit basis.
        if (
          attempt >= 2 ||
          !(error instanceof CreationCommandError) ||
          !error.receipt.issues.length ||
          error.receipt.issues.some(
            (issue) => issue.code !== "revision_conflict",
          )
        )
          throw error;
        const rebased = this.rebasePresentation(operations, versions);
        if (!rebased) throw error;
        expected = rebased;
      }
    }
    // Business acknowledgement cannot be undone by a view preference failure.
    this.basis = this.client.snapshot;
    this.baseline = document;
    if (options.saveView === false) return operations.length > 0;
    await this.saveView(document);
    return operations.length > 0;
  }

  /** View state is independent of the business commit; panning only needs this. */
  async saveView(document: CanvasDocument) {
    this.viewError = "";
    try {
      const view = await this.client.view();
      const generationCounts = {
        ...view.generationCounts,
        ...Object.fromEntries(
          document.nodes
            .filter((node) => node.data.settings)
            .map((node) => [node.id, node.data.settings!.count ?? 1]),
        ),
      };
      // Autosave, switch flushes and open all end here. Rewriting an unchanged
      // viewport delays the next document without persisting any new fact.
      const unchangedCounts =
        Object.keys(view.generationCounts).length ===
          Object.keys(generationCounts).length &&
        same(
          Object.entries(view.generationCounts).sort(),
          Object.entries(generationCounts).sort(),
        );
      if (same(view.viewport, document.viewport) && unchangedCounts) return;
      await this.client.view({ viewport: document.viewport, generationCounts });
    } catch (error) {
      this.viewError = error instanceof Error ? error.message : String(error);
    }
  }
}

/** Orders nodes so every parent precedes the children that name it.
 * Documents store nodes in visual order, but a batch applies operations in
 * sequence, so a member created before its group fails as a missing object.
 * Cycles fall back to the original order rather than dropping nodes. */
function parentsFirst(nodes: CanvasNode[]): CanvasNode[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const ordered: CanvasNode[] = [];
  const state = new Map<string, "visiting" | "done">();
  const visit = (node: CanvasNode) => {
    if (state.get(node.id) === "done") return;
    // A cycle would otherwise recurse forever; the backend rejects it anyway.
    if (state.get(node.id) === "visiting") return;
    state.set(node.id, "visiting");
    const parent = node.parentId ? byId.get(node.parentId) : undefined;
    if (parent) visit(parent);
    state.set(node.id, "done");
    ordered.push(node);
  };
  for (const node of nodes) visit(node);
  return ordered.length === nodes.length ? ordered : nodes;
}
