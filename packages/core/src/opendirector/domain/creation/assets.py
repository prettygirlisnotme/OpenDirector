"""Stable file identities and frozen content references, without browser URLs."""
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from opendirector.domain.rule_facts import PROMPT_MAX_LENGTH
from .base import Contract, Fingerprint, MediaKind, NonNegative, Positive, PositiveInt, ProjectEntity, Revision
from .models import ModelSnapshot


class AssetRef(Contract):
    assetId: UUID = Field(description='ID of a registered project asset from project.assets[].id or a successful asset command result.')
    revision: Revision = Field(description='Referenced immutable asset revision.')
    fingerprint: Fingerprint = Field(description='Immutable content fingerprint.')
    kind: MediaKind = Field(description='Business object or media type.')


class MediaMetadata(Contract):
    mimeType: str = Field(min_length=1, max_length=255, description='Verified media MIME type.')
    size: int = Field(ge=0, strict=True, description='Content size in bytes.')
    width: PositiveInt | None = Field(default=None, description='Width in pixels.')
    height: PositiveInt | None = Field(default=None, description='Height in pixels.')
    durationSeconds: Positive | None = Field(default=None, description='Duration in seconds.')
    frameRate: Positive | None = Field(default=None, description='Video frames per second.')
    hasAudio: bool | None = Field(default=None, description='Whether the media contains an audio stream.')
    sampleRate: PositiveInt | None = Field(default=None, description='Audio samples per second.')
    channels: PositiveInt | None = Field(default=None, description='Audio channel count.')

    @model_validator(mode='after')
    def dimensions(self):
        if (self.width is None) != (self.height is None):
            raise ValueError('Media dimensions must be supplied together')
        return self


class UploadInfo(Contract):
    id: UUID = Field(description='Stable object ID.')
    projectId: UUID = Field(description='Owning project ID.')
    name: str = Field(description='User-visible name.')
    status: Literal['uploading', 'ready', 'registered', 'cancelled'] = Field(description='Current operation lifecycle state.')
    kind: MediaKind | None = Field(default=None, description='Business object or media type.')
    fingerprint: Fingerprint | None = Field(default=None, description='Immutable content fingerprint.')
    metadata: MediaMetadata | None = Field(default=None, description='Verified media properties.')
    assetId: UUID | None = Field(default=None, description='Asset ID.')

    @model_validator(mode='after')
    def complete(self):
        if self.status in {'ready', 'registered'} and (self.kind is None or self.fingerprint is None or self.metadata is None):
            raise ValueError('Completed uploads require verified media facts')
        if (self.status == 'registered') != (self.assetId is not None):
            raise ValueError('Registered uploads identify their committed asset')
        return self


class AssetProvenance(Contract):
    source: Literal['upload', 'manual', 'agent_written', 'generated', 'edited', 'rendered', 'imported'] = Field(description='Input source or recorded provenance.')
    jobId: UUID | None = Field(default=None, description='Generation job ID.')
    toolTaskId: UUID | None = Field(default=None, description='Tool execution ID.')
    sourceItemId: UUID | None = Field(default=None, description='Source canvas item ID.')
    editorDocumentId: UUID | None = Field(default=None, description='Editable document ID.')
    editorRevision: Revision | None = Field(default=None, description='Expected editable document revision.')
    sources: tuple[AssetRef, ...] = Field(default=(), description='Ordered source asset versions.')
    inputFingerprint: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$', description='Fingerprint of the fixed execution input.')
    agentRunId: UUID | None = Field(default=None, description='Agent turn ID.')
    agentRuntime: Literal['opencode', 'codex', 'claude', 'jiuwenswarm', 'deepseek'] | None = Field(default=None, description='Native Agent runtime that produced this asset.')
    modelSnapshot: ModelSnapshot | None = Field(default=None, description='Fixed model connection recorded for provenance.')
    prompt: str | None = Field(default=None, max_length=PROMPT_MAX_LENGTH, description='Generation prompt text.')
    importedFrom: Literal['generated', 'edited'] | None = Field(default=None, description='Original source recorded by a canvas archive this asset was imported from.')

    @model_validator(mode='after')
    def evidence(self):
        if self.source == 'generated' and (self.jobId is None or self.inputFingerprint is None):
            raise ValueError('Generated assets require job and input identity')
        if self.source == 'imported' and self.importedFrom is None:
            raise ValueError('Imported assets record the source they were exported from')
        if self.source != 'imported' and self.importedFrom is not None:
            raise ValueError('Only imported assets carry an archive source')
        if self.source == 'agent_written' and (self.agentRunId is None or
                not ((self.modelSnapshot is not None and self.modelSnapshot.kind == 'agent') or self.agentRuntime is not None)):
            raise ValueError('Agent-written documents require a run and an agent model or native runtime')
        if self.source in {'edited', 'rendered'} and (self.editorDocumentId is None or self.editorRevision is None):
            raise ValueError('Edited outputs require their source document version')
        if (self.editorDocumentId is None) != (self.editorRevision is None):
            raise ValueError('Editor identity and version must be supplied together')
        return self


class Asset(ProjectEntity):
    kind: MediaKind = Field(description='Business object or media type.')
    name: str = Field(min_length=1, max_length=255, description='User-visible name.')
    fingerprint: Fingerprint = Field(description='Immutable content fingerprint.')
    path: str = Field(description='Immutable asset path relative to the project directory.')
    metadata: MediaMetadata = Field(description='Verified media properties.')
    provenance: AssetProvenance = Field(description='Recorded origin and execution context.')

    @model_validator(mode='after')
    def content_identity(self):
        if self.path != f'assets/{self.id}':
            raise ValueError('Asset path must use its stable project-relative ID')
        if any(source.assetId == self.id for source in self.provenance.sources):
            raise ValueError('An asset cannot be its own source')
        return self
