import { RULE_FACTS } from "../contracts/ruleFacts.generated.ts";
import type { CanvasNode } from "./model.ts";
import { assistantReferences } from "./assistantReferences.ts";
import type { ReferenceInput } from "../backend/generation.ts";
import { tNow } from "../i18n/messages.ts";

export interface PendingCanvasReference {
  nodeId: string;
  title: string;
  kind: CanvasNode["type"];
  thumbnail?: string;
  metadataOnly: boolean;
  error: string | null;
  identity: {
    assetState: CanvasNode["data"]["assetState"];
    references: ReferenceInput[];
  };
  node: CanvasNode;
}

/** Selecting a node only stages context. No upload, attachment or draft write. */
export function pendingCanvasReferences(
  nodes: readonly CanvasNode[],
): PendingCanvasReference[] {
  return [...new Map(nodes.map((node) => [node.id, node])).values()].map(
    (node) => {
      const data = node.data;
      const supported = ["text", "image", "video", "audio", "subject"].includes(
        node.type,
      );
      const available =
        node.type === "text"
          ? Boolean(data.text?.trim())
          : node.type === "subject"
            ? Boolean(data.subject?.main?.src)
            : Boolean(data.src);
      const error = !supported
        ? tNow("errors.pendingRef.unsupported")
        : data.assetState === "missing"
          ? tNow("errors.pendingRef.mediaMissing")
          : !available
            ? tNow("errors.pendingRef.noAsset")
            : null;
      return {
        nodeId: node.id,
        title: data.title || tNow("errors.pendingRef.blankNode"),
        kind: node.type,
        thumbnail:
          node.type === "image"
            ? data.src
            : node.type === "subject" && data.subject?.main?.kind === "image"
              ? data.subject.main.src
              : undefined,
        metadataOnly: node.type === "video" || node.type === "audio",
        error,
        identity: {
          assetState: data.assetState,
          references: assistantReferences([node]),
        },
        node,
      };
    },
  );
}

/** Compare the immutable source strings directly; serializing a media data URL
 * on each selection/draft render would copy potentially large video bytes. */
export function samePendingReferences(
  left: readonly PendingCanvasReference[],
  right: readonly PendingCanvasReference[],
) {
  return (
    left.length === right.length &&
    left.every((item, index) => {
      const other = right[index]!;
      return (
        item.nodeId === other.nodeId &&
        item.kind === other.kind &&
        item.title === other.title &&
        item.error === other.error &&
        item.identity.assetState === other.identity.assetState &&
        item.identity.references.length === other.identity.references.length &&
        item.identity.references.every((ref, offset) => {
          const next = other.identity.references[offset]!;
          return (
            ref.kind === next.kind &&
            ref.title === next.title &&
            ref.src === next.src &&
            ref.text === next.text
          );
        })
      );
    })
  );
}

export function pendingReferenceInsertion(
  value: string,
  end: number,
  items: readonly PendingCanvasReference[],
  maxLength: number = RULE_FACTS.promptMaxLength,
) {
  if (items.length > RULE_FACTS.maxReferences)
    throw new Error(
      tNow("errors.pendingRef.limit", { count: RULE_FACTS.maxReferences }),
    );
  const invalid = items.filter((item) => item.error);
  if (invalid.length)
    throw new Error(
      invalid.map((item) => `${item.title}：${item.error}`).join(" ") +
        ` ${tNow("errors.pendingRef.batchSuffix")}`,
    );
  const start = Math.max(0, Math.min(value.length, end));
  const inserted =
    (start > 0 && !/\s/.test(value[start - 1]!) ? " " : "") +
    items.map((item) => `@${item.title}`).join(" ") +
    " ";
  const next = value.slice(0, start) + inserted + value.slice(start);
  if (next.length > maxLength)
    throw new Error(
      tNow("errors.pendingRef.textLimit", { max: maxLength }),
    );
  return { value: next, start, inserted, caret: start + inserted.length };
}
