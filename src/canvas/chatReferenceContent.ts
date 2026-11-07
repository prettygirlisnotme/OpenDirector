import type { ChatAsset } from "../backend/chat.ts";
import { RULE_FACTS } from "../contracts/ruleFacts.generated.ts";
import type { Translate } from "../i18n/I18nProvider.tsx";
import { defaultTranslate } from "./assistantTranslate.ts";

export type ContentPart =
  | { type: "text"; text: string }
  | { type: "skill"; name: string }
  | {
      type: "reference";
      assetId: string;
      referenceId?: string;
      sourceRole?: string;
      binding?: "asset";
      nodeId?: string | null;
      name: string;
      fingerprint?: string | null;
    };
export type ReferenceContent = ContentPart[];
export type ReferenceAsset = ChatAsset & {
  referenceId?: string;
  sourceRole?: string;
  binding?: "asset";
  kind?: string;
  nodeTitle?: string;
  logicalNodeId?: string;
  nodeId?: string;
  fingerprintAliases?: string[];
  onCanvas?: boolean;
  available?: boolean;
  fileAvailable?: boolean;
  width?: number | null;
  height?: number | null;
  durationSeconds?: number | null;
};
/** Visibility is about source identity, never a matching filename or asset. */
export function referenceIsOffCanvas(
  part: Extract<ContentPart, { type: "reference" }>,
  asset: ReferenceAsset | undefined,
  visibleNodeIds: ReadonlySet<string>,
) {
  const id =
    part.binding === "asset"
      ? (asset?.logicalNodeId ?? asset?.nodeId)
      : (part.nodeId ?? asset?.nodeId ?? asset?.logicalNodeId);
  return !id || !visibleNodeIds.has(id);
}
export function fingerprintMatches(asset: ReferenceAsset, value: string) {
  return (
    asset.fingerprint === value || !!asset.fingerprintAliases?.includes(value)
  );
}
export const REFERENCE_LIMIT = RULE_FACTS.maxReferences; // Structural guard, separate from each provider's media budget.
export const CONTENT_LIMIT = RULE_FACTS.promptMaxLength;
export function assetKind(asset: ReferenceAsset) {
  return (
    asset.kind ??
    (asset.type.startsWith("image/")
      ? "image"
      : asset.type.startsWith("video/")
        ? "video"
        : asset.type.startsWith("audio/")
          ? "audio"
          : /\.(txt|md)$/i.test(asset.name) || asset.type.startsWith("text/")
            ? "text"
            : "other")
  );
}
export function normalizeContent(parts: ReferenceContent): ReferenceContent {
  const result: ReferenceContent = [];
  for (const part of parts) {
    if (part.type === "text") {
      const text = part.text.replaceAll("\u200b", "").replaceAll("\r\n", "\n");
      if (!text) continue;
      const last = result.at(-1);
      if (last?.type === "text") last.text += text;
      else result.push({ type: "text", text });
    } else if (part.type === "skill") result.push({type: "skill", name: part.name});
    else
      result.push({
        type: "reference",
        assetId: part.assetId,
        ...(part.sourceRole ? { sourceRole: part.sourceRole } : {}),
        ...(part.referenceId ? { referenceId: part.referenceId } : {}),
        ...(part.binding === "asset" ? { binding: "asset" as const } : {}),
        ...(part.binding !== "asset" && part.nodeId
          ? { nodeId: part.nodeId }
          : {}),
        name: part.name,
        fingerprint: part.fingerprint ?? null,
      });
  }
  return result;
}
// Line breaks and spaces left by editing are not content: an editor holding
// only those still shows its prompt and never publishes a blank message.
export function blankContent(content: ReferenceContent) {
  return content.every(
    (part) => part.type === "text" && !part.text.replace(/[\s\u200b]/g, ""),
  );
}
export function referenceIds(content: ReferenceContent) {
  return [
    ...new Set(
      content.flatMap((part) =>
        part.type === "reference" ? [part.assetId] : [],
      ),
    ),
  ];
}
export function contentText(
  content: ReferenceContent,
  speech = false,
  t: Translate = defaultTranslate,
) {
  const ids = referenceIds(content);
  let text = content
    .map((part) =>
      part.type === "text"
        ? part.text
        : part.type === "skill" ? (speech ? "" : `/${part.name}`)
        : speech
          ? ""
          : t("assistant.reference.token", {
              index: ids.indexOf(part.assetId) + 1,
              name: part.name,
            }),
    )
    .join("");
  if (speech) {
    // A reference-only first line is context, including its separating newline.
    let prefix = "",
      hasReference = false;
    for (const part of content) {
      if (part.type === "skill") continue;
      if (part.type === "reference") {
        hasReference = true;
        continue;
      }
      const newline = part.text.indexOf("\n");
      prefix += newline < 0 ? part.text : part.text.slice(0, newline);
      if (newline >= 0) {
        if (hasReference && !prefix.trim())
          text = text.slice(prefix.length + 1);
        break;
      }
    }
  }
  return text;
}
export function referenceContent(
  text: string,
  ids: string[],
  assets: ReferenceAsset[],
  content?: ReferenceContent | null,
  t: Translate = defaultTranslate,
): ReferenceContent {
  const body = content ?? (text ? [{ type: "text" as const, text }] : []);
  const located = new Set(referenceIds(body));
  const unlocated = [...new Set(ids)].filter((id) => !located.has(id));
  if (!unlocated.length) return body;
  const header = unlocated.flatMap((id, index): ReferenceContent => {
    const asset = assets.find((item) => item.id === id);
    return [
      ...(index ? [{ type: "text" as const, text: " " }] : []),
      {
        type: "reference",
        assetId: id,
        name: asset?.nodeTitle || asset?.name || t("assistant.reference.missing"),
        fingerprint: asset?.fingerprint ?? null,
      },
    ];
  });
  return body.some((part) => part.type !== "text" || part.text.length > 0)
    ? [...header, { type: "text", text: "\n" }, ...body]
    : header;
}
export function referencePart(asset: ReferenceAsset): ContentPart {
  return {
    type: "reference",
    assetId: asset.id,
    ...(asset.sourceRole ? { sourceRole: asset.sourceRole } : {}),
    ...(asset.referenceId ? { referenceId: asset.referenceId } : {}),
    ...(asset.binding === "asset" ? { binding: "asset" } : {}),
    ...(asset.binding !== "asset" && asset.nodeId
      ? { nodeId: asset.nodeId }
      : {}),
    name: asset.nodeTitle || asset.name,
    fingerprint: asset.fingerprint,
  };
}

/** Refresh only following tokens. Pinned versions and authored text stay put. */
export function currentReferences(
  content: ReferenceContent,
  assets: readonly ReferenceAsset[],
): ReferenceContent {
  return content.map((part) => {
    if (part.type !== "reference" || part.binding === "asset") return part;
    const asset = part.nodeId
      ? (assets.find(
          (asset) =>
            asset.nodeId === part.nodeId &&
            asset.sourceRole === part.sourceRole,
        ) ??
        (!part.sourceRole &&
        assets.filter((asset) => asset.nodeId === part.nodeId).length === 1
          ? assets.find((asset) => asset.nodeId === part.nodeId)
          : undefined))
      : assets.find((asset) => asset.id === part.assetId);
    return asset
      ? {
          ...referencePart(asset),
          ...(part.referenceId ? { referenceId: part.referenceId } : {}),
        }
      : part;
  });
}

export function validContent(content: ReferenceContent) {
  return (
    content.length <= 2000 &&
    content.filter(part => part.type === "skill").length <= 1 &&
    contentText(content).length <= CONTENT_LIMIT &&
    referenceIds(content).length <= REFERENCE_LIMIT
  );
}
