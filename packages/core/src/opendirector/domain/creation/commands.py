"""Shared HTTP/Agent command and query schemas. No operation is executed here."""
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import ConfigDict, Field, TypeAdapter, create_model, model_validator

from .assets import AssetRef, MediaMetadata
from .base import Contract, Digest, GenerationKind, MediaKind, ObjectVersion, Point, Positive, Revision, digest, unique
from .canvas import CanvasItem
from .editors import EditorDocument
from opendirector.domain.rule_facts import PROMPT_MAX_LENGTH
from .generation import GenerationSpec, Job, PromptPart, Reference


class ProjectCreate(Contract):
    name: str = Field(min_length=1, max_length=100, pattern=r'\S', description='User-visible name.')


class ProjectRename(ProjectCreate):
    pass


class AssetImport(Contract):
    uploadHandle: UUID = Field(description='Server-issued staged upload ID.')  # File bytes are transferred separately; no arbitrary path.
    name: str = Field(min_length=1, max_length=255, description='User-visible name.')
    expectedKind: MediaKind = Field(description='Required media kind, when specified.')


class ArtifactPublish(Contract):
    """Publish a file from a server-owned execution context without generating media."""
    model_config = ConfigDict(json_schema_extra={'examples': [
        {'path': 'clips/segment-01.mp4', 'name': '片段 01'},
    ]})

    path: str = Field(min_length=1, max_length=4096, description='Artifact path relative to the server-owned execution directory.')
    name: str = Field(min_length=1, max_length=255, description='User-visible name.')
    expectedKind: MediaKind | None = Field(default=None, description='Required media kind, when specified.')
    sources: tuple[AssetRef, ...] = Field(default=(), max_length=100, description='Ordered source asset versions.')


class AssetWriteText(Contract):
    name: str = Field(min_length=1, max_length=255, description='User-visible name.')
    text: str = Field(max_length=1024 * 1024, description='UTF-8 text content or search query.')
    previousAsset: AssetRef | None = Field(default=None, description='Previous immutable version for a new text asset version.')
    # Manual/Agent provenance is supplied by the trusted application context.


class AssetMetadataUpdate(Contract):
    assetId: UUID = Field(description='Asset ID.')
    name: str = Field(min_length=1, max_length=255, description='User-visible name.')


class AssetDelete(Contract):
    assetIds: tuple[UUID, ...] = Field(min_length=1, max_length=100, description='Target asset IDs.')
    clearHistory: bool = Field(default=False, description='Whether the confirmed deletion also clears dependent history.')

    @model_validator(mode='after')
    def assets(self):
        unique(self.assetIds, 'delete asset IDs')
        return self


class CanvasShow(Contract):
    item: CanvasItem = Field(description='Canvas display item to create or restore.')


class CanvasCreateGeneration(Contract):
    spec: GenerationSpec = Field(description='New configuration with purpose=generation.')
    item: CanvasItem = Field(description='Visible item bound to spec.id with role=generation.')
    references: tuple[Reference, ...] = Field(default=(), description='Input references. Every canvas-derived reference must retain its source node: source.id for follow_current, sourceVersion for pinned. Never replace a canvas reference with an asset-only binding.')

    @model_validator(mode='after')
    def bindings(self):
        if not self.item.visible:
            raise ValueError('New generation node must be visible')
        if self.spec.purpose != 'generation':
            raise ValueError('Generation node requires spec.purpose=generation')
        if self.item.binding.role != 'generation' or self.item.binding.objectRef.id != self.spec.id:
            raise ValueError('Canvas item must bind to the new generation configuration')
        if self.item.kind != self.spec.kind or self.item.projectId != self.spec.projectId:
            raise ValueError('Canvas item and generation configuration must have the same project and kind')
        if any(ref.targetSpecId != self.spec.id or ref.projectId != self.spec.projectId for ref in self.references):
            raise ValueError('References must belong to the new generation configuration')
        return self


class CanvasMoveItem(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    position: Point = Field(description='Position in the owning canvas or stage.')


class CanvasMove(Contract):
    items: tuple[CanvasMoveItem, ...] = Field(min_length=1, max_length=500, description='Ordered entries in this collection.')

    @model_validator(mode='after')
    def items_unique(self):
        unique([item.itemId for item in self.items], 'moved canvas IDs')
        return self


class CanvasPresentationUpdate(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    title: str | None = Field(default=None, max_length=255, description='User-visible title.')
    width: Positive | None = Field(default=None, description='Width in pixels.')
    height: Positive | None = Field(default=None, description='Height in pixels.')
    zIndex: int | None = Field(default=None, strict=True, description='Canvas stacking order.')
    locked: bool | None = Field(default=None, description='Whether edits to this object are blocked.')
    parentId: UUID | None = Field(default=None, description='Parent canvas group ID, or null for the root.')
    # Presence of parentId distinguishes retaining a group from ungrouping.


class CanvasHide(Contract):
    itemId: UUID = Field(description='Canvas item ID.')


class CanvasMaterialize(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    asset: AssetRef = Field(description='Immutable asset version.')
    discardGenerationDraft: bool = Field(default=False, description='Whether material replacement discards the generation draft.')


class CanvasApplyEdit(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    source: AssetRef = Field(description='Input source or recorded provenance.')
    asset: AssetRef = Field(description='Immutable asset version.')
    editorId: UUID = Field(description='Editable document ID.')


class TimelinePublishRender(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    editorId: UUID = Field(description='Editable document ID.')
    asset: AssetRef = Field(description='Immutable asset version.')
    outputId: UUID = Field(description='Published canvas output ID.')
    title: str = Field(default='合成视频', max_length=255, description='User-visible title.')
    position: Point = Field(default_factory=Point, description='Position in the owning canvas or stage.')


class MediaPublicationOutput(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    asset: AssetRef = Field(description='Immutable asset version.')
    title: str = Field(min_length=1, max_length=255, description='User-visible title.')
    position: Point = Field(default_factory=Point, description='Position in the owning canvas or stage.')
    width: float = Field(default=352, gt=0, le=10000, description='Width in pixels.')
    height: float = Field(default=198, gt=0, le=10000, description='Height in pixels.')


class CanvasPublishMedia(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    source: AssetRef | None = Field(default=None, description='Input source or recorded provenance.')
    sourceEditorId: UUID | None = Field(default=None, description='Source editable document ID.')
    document: EditorDocument = Field(description='Editable document owned by this operation.')
    outputs: tuple[MediaPublicationOutput, ...] = Field(min_length=1, max_length=50, description='Ordered published media outputs.')

    @model_validator(mode='after')
    def distinct_outputs(self):
        unique([output.itemId for output in self.outputs], 'output item IDs')
        unique([output.asset.assetId for output in self.outputs], 'output asset IDs')
        if self.document.kind not in {'media_edit', 'director'}:
            raise ValueError('Local publication requires a media edit or director snapshot')
        return self


class CanvasConfigure(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    specId: UUID = Field(description='Generation configuration ID.')
    # Explicit conversion keeps the immutable handwritten asset in the catalog.
    preserveMaterial: bool = Field(default=False, description='Whether configuring generation retains current material.')


class CanvasEditorAttach(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    editorId: UUID = Field(description='Editable document ID.')


class SpecRestore(Contract):
    specId: UUID = Field(description='Generation configuration ID.')
    commandId: UUID = Field(description='Historical command providing the settings to restore.')
    referencePolicy: Literal['follow_current', 'pinned'] = Field(default='follow_current', description='Policy for restoring associated reference bindings.')


class NodeVersionApply(Contract):
    itemId: UUID = Field(description='Canvas item ID.')
    asset: AssetRef = Field(description='Immutable asset version.')
    referencePolicy: Literal['follow_current', 'pinned'] = Field(default='follow_current', description='Policy for restoring associated reference bindings.')


class SpecPromptWrite(Contract):
    specId: UUID = Field(description='Generation configuration ID.')
    prompt: str = Field(max_length=PROMPT_MAX_LENGTH, description='Generation prompt text.')
    promptContent: tuple[PromptPart, ...] | None = Field(default=None, description='Structured prompt text and reference tokens.')


class SpecWrite(Contract):
    spec: GenerationSpec = Field(description='Configuration with its current revision; the server increments it on commit.')


class ReferenceWrite(Contract):
    reference: Reference = Field(description='Generation input reference. Preserve source canvas identity for canvas-derived references, whether following or pinned.')


class ReferenceRemove(Contract):
    referenceId: UUID = Field(description='Generation reference ID.')


class GenerationSubmit(Contract):
    specId: UUID = Field(description='Include its current generation_spec revision in expectedVersions.',
        json_schema_extra={'x-version-object-type': 'generation_spec'})
    expectedSpecRevision: Revision = Field(description='Must match the specId revision in expectedVersions.')
    originItemId: UUID | None = Field(default=None,
        description='When set, include its current canvas_item revision in expectedVersions.',
        json_schema_extra={'x-version-object-type': 'canvas_item'})
    previousJobId: UUID | None = Field(default=None, description='Previous generation attempt ID.')
    adoptionPolicy: Literal['if_current', 'candidate_only'] = Field(default='if_current', description='Policy for adopting the completed result.')
    batchId: UUID | None = Field(default=None, description='Generation batch ID.')
    showAdditionalResult: bool = Field(default=False, description='Whether to display an additional result item.')

    @model_validator(mode='after')
    def presentation(self):
        if self.showAdditionalResult and (self.originItemId is None or self.adoptionPolicy != 'candidate_only'):
            raise ValueError('Additional result display requires an origin and candidate-only adoption')
        return self


class JobControl(Contract):
    jobId: UUID = Field(description='Generation job ID.')


class JobCheckpoint(Contract):
    job: Job = Field(description='Generation execution record.')


class ResultSelect(Contract):
    specId: UUID = Field(description='Generation configuration ID.')
    resultId: UUID = Field(description='Generation result ID.')


class EditorWrite(Contract):
    document: EditorDocument = Field(description='Editable document owned by this operation.')


class HistoryReplay(Contract):
    targetCommandId: UUID = Field(description='Historical command to undo or redo.')


class EmptyPayload(Contract):
    pass


class CommandBase(Contract):
    commandId: UUID = Field(description='Stable command ID for idempotent replay.')
    projectId: UUID = Field(description='Owning project ID.')
    type: str = Field(description='Operation or variant discriminator.')
    expectedVersions: tuple[ObjectVersion, ...] = Field(default=(), description='Current versions of mutated existing objects; payload revision fields do not replace these.')
    expectedPreview: Digest | None = Field(default=None, description='Fingerprint of the confirmed impact preview.')

    @model_validator(mode='after')
    def scope_and_versions(self):
        unique([(version.objectType, version.id) for version in self.expectedVersions], 'expected object versions')
        payload = self.payload
        owned = getattr(payload, 'item', None) or getattr(payload, 'spec', None) or getattr(payload, 'reference', None) or getattr(payload, 'document', None)
        if owned and owned.projectId != self.projectId:
            raise ValueError('Command payload belongs to a different project')
        required = self.required_versions()
        if self.type == 'asset.delete' and self.expectedPreview is None:
            raise ValueError('Asset deletion requires an impact preview fingerprint')
        versions = {(version.objectType, version.id): version.revision for version in self.expectedVersions}
        missing = [f'{kind}:{identity}' for kind, identity in required if (kind, identity) not in versions]
        if missing:
            raise ValueError('expectedVersions missing current versions for ' + ', '.join(missing))
        if self.type == 'generation.submit' and versions[('generation_spec', payload.specId)] != payload.expectedSpecRevision:
            raise ValueError('Submission version differs from expected spec version')
        if self.type in {'generationSpec.update', 'editor.save', 'reference.update', 'reference.restore'}:
            obj = (payload.spec if self.type == 'generationSpec.update' else
                   payload.document if self.type == 'editor.save' else payload.reference)
            if versions[required[0]] != obj.revision:
                raise ValueError('Payload version differs from its expected version')
        return self


    def required_versions(self) -> list[tuple[str, UUID]]:
        payload = self.payload
        required = []
        if self.type in {'project.rename', 'project.trash', 'project.restore'}:
            required = [('project', self.projectId)]
        elif self.type == 'canvas.publishMedia':
            required = [('canvas_item', payload.itemId), *[('asset', o.asset.assetId) for o in payload.outputs]]
            if payload.sourceEditorId:
                required.append(('editor_document', payload.sourceEditorId))
        elif self.type in {'canvas.applyEdit', 'timeline.publishRender'}:
            required = [('canvas_item', payload.itemId), ('asset', payload.asset.assetId), ('editor_document', payload.editorId)]
        elif self.type in {'canvas.update', 'canvas.hide', 'canvas.materialize', 'canvas.applyEdit', 'canvas.configure', 'canvas.attachEditor', 'canvas.detachEditor', 'canvas.applyVersion'}:
            required = [('canvas_item', payload.itemId)]
        elif self.type == 'canvas.move':
            required = [('canvas_item', item.itemId) for item in payload.items]
        elif self.type == 'asset.updateMetadata':
            required = [('asset', payload.assetId)]
        elif self.type == 'asset.writeText' and payload.previousAsset:
            required = [('asset', payload.previousAsset.assetId)]
        elif self.type == 'asset.delete':
            required = [('asset', asset_id) for asset_id in payload.assetIds]
        elif self.type == 'generationSpec.update':
            required = [('generation_spec', payload.spec.id)]
        elif self.type in {'reference.update', 'reference.restore', 'reference.remove'}:
            required = [('reference', payload.referenceId if self.type == 'reference.remove' else payload.reference.id)]
        elif self.type == 'reference.add':
            required = [('generation_spec', payload.reference.targetSpecId)]
        elif self.type == 'generation.submit':
            required = [('generation_spec', payload.specId)]
        elif self.type == 'result.select':
            required = [('generation_spec', payload.specId)]
        elif self.type == 'editor.save':
            required = [('editor_document', payload.document.id)]
        return required


# HTTP/application contracts; Agent tool registration is a separate boundary.
_COMMANDS = (
    ('ProjectCreateCommand', 'project.create', ProjectCreate, 'Create a project.'),
    ('ProjectRenameCommand', 'project.rename', ProjectRename, 'Rename the project.'),
    ('ProjectTrashCommand', 'project.trash', EmptyPayload, 'Move the project to trash.'),
    ('ProjectRestoreCommand', 'project.restore', EmptyPayload, 'Restore a trashed project.'),
    ('AssetImportCommand', 'asset.import', AssetImport, 'Register an uploaded file as a project asset.'),
    ('ArtifactPublishCommand', 'artifact.publish', ArtifactPublish, 'Register an execution file as a project asset.'),
    ('AssetWriteTextCommand', 'asset.writeText', AssetWriteText, 'Write a text asset or a new version of an existing text asset.'),
    ('AssetMetadataCommand', 'asset.updateMetadata', AssetMetadataUpdate, 'Rename an asset.'),
    ('AssetDeleteCommand', 'asset.delete', AssetDelete, 'Delete assets after an impact preview.'),
    ('CanvasCreateGenerationCommand', 'canvas.createGeneration', CanvasCreateGeneration, 'Create a visible generation node and references atomically, preserving canvas source connections, including pinned ones.'),
    ('CanvasShowCommand', 'canvas.show', CanvasShow, 'Create or restore a canvas display item.'),
    ('CanvasMoveCommand', 'canvas.move', CanvasMove, 'Move one or more canvas items.'),
    ('CanvasUpdateCommand', 'canvas.update', CanvasPresentationUpdate, 'Update canvas presentation, grouping or lock state.'),
    ('CanvasHideCommand', 'canvas.hide', CanvasHide, 'Remove a canvas item; preserves project contents. Remove all visible items to clear the canvas.'),
    ('CanvasMaterializeCommand', 'canvas.materialize', CanvasMaterialize, 'Bind an asset to an empty or material canvas item.'),
    ('CanvasApplyEditCommand', 'canvas.applyEdit', CanvasApplyEdit, 'Apply an editor result to a canvas item.'),
    ('CanvasPublishMediaCommand', 'canvas.publishMedia', CanvasPublishMedia, 'Publish edited media outputs with their editor snapshot.'),
    ('TimelinePublishRenderCommand', 'timeline.publishRender', TimelinePublishRender, 'Publish a rendered timeline result.'),
    ('NodeVersionApplyCommand', 'canvas.applyVersion', NodeVersionApply, 'Apply a historical asset version and its reference policy to a canvas item.'),
    ('CanvasConfigureCommand', 'canvas.configure', CanvasConfigure, 'Bind a generation configuration to a canvas item.'),
    ('CanvasEditorAttachCommand', 'canvas.attachEditor', CanvasEditorAttach, 'Attach an editor document to a canvas item.'),
    ('CanvasEditorDetachCommand', 'canvas.detachEditor', CanvasEditorAttach, 'Detach an editor document from a canvas item.'),
    ('SpecCreateCommand', 'generationSpec.create', SpecWrite, 'Create a generation configuration without submitting a job.'),
    ('SpecUpdateCommand', 'generationSpec.update', SpecWrite, 'Replace the complete generation configuration; preserve unedited fields and use the current version. Jobs are unchanged.'),
    ('SpecRestoreCommand', 'generationSpec.restore', SpecRestore, 'Restore generation settings from a historical command.'),
    ('SpecPromptCommand', 'generationSpec.setPrompt', SpecPromptWrite, 'Update prompt text and structured references.'),
    ('ReferenceAddCommand', 'reference.add', ReferenceWrite, 'Add a generation reference; expectedVersions must include its target generation_spec.'),
    ('ReferenceUpdateCommand', 'reference.update', ReferenceWrite, 'Update a generation reference.'),
    ('ReferenceRestoreCommand', 'reference.restore', ReferenceWrite, 'Restore a generation reference.'),
    ('ReferenceRemoveCommand', 'reference.remove', ReferenceRemove, 'Deactivate a generation reference.'),
    ('GenerationSubmitCommand', 'generation.submit', GenerationSubmit, 'Submit a generation job with fixed inputs and an adoption policy.'),
    ('JobCancelCommand', 'job.cancel', JobControl, 'Request cancellation of an existing job.'),
    ('JobRecoverCommand', 'job.recover', JobControl, 'Recover an existing job without creating a new attempt.'),
    ('JobCheckpointCommand', 'job.checkpoint', JobCheckpoint, 'Persist a job execution checkpoint.'),
    ('ResultSelectCommand', 'result.select', ResultSelect, 'Adopt an existing generation result.'),
    ('EditorCreateCommand', 'editor.create', EditorWrite, 'Create an editable document.'),
    ('EditorSaveCommand', 'editor.save', EditorWrite, 'Save an editable document.'),
    ('HistoryUndoCommand', 'history.undo', HistoryReplay, 'Undo a recorded reversible command.'),
    ('HistoryRedoCommand', 'history.redo', HistoryReplay, 'Reapply an undone command.'),
)
COMMAND_MODELS = {}
for _name, _type, _payload, _description in _COMMANDS:
    _model = create_model(_name, __base__=CommandBase, __doc__=_description, type=(Literal[_type], Field(description='Command discriminator.')),
        payload=(_payload, Field(description=_description)))
    COMMAND_MODELS[_type] = _model
    globals()[_name] = _model

_BATCH_TYPES = {'canvas.show', 'canvas.move', 'canvas.update', 'canvas.hide', 'canvas.materialize', 'canvas.applyEdit',
                'canvas.configure', 'canvas.attachEditor', 'canvas.detachEditor', 'generationSpec.create', 'generationSpec.update',
                'reference.add', 'reference.update', 'reference.restore', 'reference.remove', 'editor.create', 'editor.save', 'result.select', 'generationSpec.setPrompt'}
_OPERATIONS = tuple(create_model(name + 'Operation', __base__=Contract,
                    __doc__=description, type=(Literal[kind], Field(description='Operation discriminator.')),
                    payload=(payload, Field(description=description)))
                    for name, kind, payload, description in _COMMANDS if kind in _BATCH_TYPES)
Operation = Annotated[Union[_OPERATIONS], Field(discriminator='type')]


class BatchPayload(Contract):
    operations: tuple[Operation, ...] = Field(min_length=1, max_length=500, description='Ordered operations committed as one atomic transaction.')


BatchCommand = create_model('BatchCommand', __base__=CommandBase,
                            __doc__='Apply multiple operations atomically; all succeed or none are committed.',
                            type=(Literal['creation.batch'], Field(description='Batch command discriminator.')),
                            payload=(BatchPayload, Field(description='Ordered operations committed atomically.')))
COMMAND_MODELS['creation.batch'] = BatchCommand
Command = Annotated[Union[tuple(COMMAND_MODELS.values())], Field(discriminator='type')]
COMMAND_ADAPTER = TypeAdapter(Command)


def parse_command(value) -> Command:
    """The same input validator can be used by HTTP and Agent adapters."""
    return COMMAND_ADAPTER.validate_python(value)


def command_fingerprint(command: Command) -> str:
    return digest(command)


class ContractIssue(Contract):
    code: Literal['invalid_input', 'revision_conflict', 'role_restricted', 'missing_object', 'reference_invalid',
                  'capability_unsupported', 'preview_expired', 'storage_unavailable', 'submission_unknown', 'provider_error'] = Field(description='Stable error or issue code.')
    message: str = Field(min_length=1, max_length=500, description='Human-readable issue description.')
    objectId: UUID | None = Field(default=None, description='Target business object ID.')
    field: str | None = Field(default=None, max_length=200, description='Path of the field responsible for this issue.')


class CommandReceipt(Contract):
    commandId: UUID = Field(description='Stable command ID for idempotent replay.')
    projectId: UUID = Field(description='Owning project ID.')
    inputFingerprint: Digest = Field(description='Fingerprint of the fixed execution input.')
    status: Literal['applied', 'accepted', 'noop', 'rejected'] = Field(description='Current operation lifecycle state.')
    changed: tuple[ObjectVersion, ...] = Field(default=(), description='Business objects changed by the operation.')
    jobIds: tuple[UUID, ...] = Field(default=(), description='Generation jobs associated with this operation.')
    issues: tuple[ContractIssue, ...] = Field(default=(), description='Validation or execution issues.')
    eventCursor: int | None = Field(default=None, strict=True, ge=0, description='Latest committed project event cursor.')

    @model_validator(mode='after')
    def outcome(self):
        unique(self.jobIds, 'receipt job IDs')
        if self.status == 'rejected' and (self.changed or self.jobIds or not self.issues):
            raise ValueError('Rejected commands need issues and cannot claim changes')
        if self.status == 'accepted' and not self.jobIds:
            raise ValueError('Accepted background work needs a task receipt')
        if self.status == 'noop' and (self.changed or self.jobIds):
            raise ValueError('A noop cannot create objects or tasks')
        return self


class QueryBase(Contract):
    queryId: UUID = Field(description='Caller-supplied query ID.')
    projectId: UUID = Field(description='Owning project ID.')


class ProjectQuery(QueryBase):
    type: Literal['project.get'] = Field(description='Read the current project.')


class ObjectQuery(QueryBase):
    type: Literal['object.get', 'job.get'] = Field(description='Read an object or generation job.')
    objectId: UUID = Field(description='Target business object ID.')
    revision: Revision | None = Field(default=None, description='Historical object revision, or current when omitted.')


class VersionsQuery(QueryBase):
    type: Literal['generationSpec.versions', 'canvas.versions'] = Field(description='Read object versions.')
    objectId: UUID = Field(description='Target configuration or canvas item ID.')


class AssetSearchQuery(QueryBase):
    type: Literal['asset.search'] = Field(description='Search project assets.')
    text: str = Field(default='', max_length=1000, description='Asset search text.')
    offset: int = Field(default=0, strict=True, ge=0, description='Number of matching records to skip.')
    limit: int = Field(default=50, strict=True, ge=1, le=200, description='Maximum number of records to return.')


class AssetUsageQuery(QueryBase):
    type: Literal['asset.usage'] = Field(description='Read asset dependencies.')
    objectId: UUID | None = Field(default=None, description='Asset ID, or all assets when omitted.')
    offset: int = Field(default=0, strict=True, ge=0, description='Number of matching records to skip.')
    limit: int = Field(default=50, strict=True, ge=1, le=200, description='Maximum number of records to return.')


class CanvasStatusQuery(QueryBase):
    type: Literal['canvas.status'] = Field(description='Read node status, current asset and reusable referenceSources.')
    objectId: UUID = Field(description='Canvas item ID.')
    waitSeconds: float = Field(default=0, ge=0, le=30, description='Wait for generation changes; zero returns immediately.')


class AssetPreviewQuery(QueryBase):
    type: Literal['asset.preview'] = Field(description='Preview an asset; does not establish model observation.')
    objectId: UUID = Field(description='Asset ID.')


Query = Annotated[ProjectQuery | ObjectQuery | VersionsQuery | AssetSearchQuery | AssetUsageQuery |
                  CanvasStatusQuery | AssetPreviewQuery, Field(discriminator='type')]


class ChangedObject(Contract):
    version: ObjectVersion = Field(description='Version of the referenced record.')
    change: Literal['created', 'updated', 'hidden', 'deleted'] = Field(description='Kind of business object change.')


class ProjectEvent(Contract):
    schemaVersion: Literal[1] = Field(default=1, description='Record schema version.')
    projectId: UUID = Field(description='Owning project ID.')
    cursor: int = Field(strict=True, ge=1, description='Monotonically increasing project event cursor.')
    commandId: UUID | None = Field(default=None, description='Stable command ID for idempotent replay.')
    source: Literal['ui', 'agent', 'background', 'external'] = Field(description='Input source or recorded provenance.')
    changed: tuple[ChangedObject, ...] = Field(default=(), description='Business objects changed by the operation.')
    jobIds: tuple[UUID, ...] = Field(default=(), description='Generation jobs associated with this operation.')
