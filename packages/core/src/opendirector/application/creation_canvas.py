"""Semantic creation commands and projections over the single project aggregate.

Mutations work on a private draft. The repository validates and publishes the
entire transaction once, including versions, receipt and outbox event.
"""
import asyncio
from uuid import UUID

from pydantic import JsonValue
from pydantic_core import to_jsonable_python

from opendirector.application.creation_assets import CreationAssets

from opendirector.domain.creation.assets import AssetRef
from opendirector.domain.creation.base import CanvasItemVersion, ObjectVersion
from opendirector.domain.creation.commands import CanvasShow, Query, ReferenceWrite, SpecWrite
from opendirector.domain.creation.generation import FollowingSource, ResolvedReference
from opendirector.domain.creation.canvas import CanvasReferenceSources, FollowingReferenceSource, PinnedReferenceSource, media_card_size, node_capabilities, VideoCanvasItem
from opendirector.domain.creation.models import ModelConfigPublic
from opendirector.domain.creation.project import ProjectSnapshot, asset_references
from opendirector.domain.creation.index import object_index
from opendirector.domain.creation.validation import ContractError
from opendirector.domain.input_rules import input_rules
from opendirector.domain.rule_facts import FRAME_IMAGE_LIMIT
from opendirector.domain.projects import ProjectError
from opendirector.ports.gateway import gateway

COLLECTIONS = {'canvas_item': 'canvasItems', 'generation_spec': 'generationSpecs',
               'reference': 'references', 'editor_document': 'editorDocuments',
               'asset': 'assets', 'job': 'jobs', 'result': 'results'}
DOWNSTREAM = {'text': {'text', 'image', 'video'}, 'image': {'text', 'image', 'video', 'audio', 'timeline', 'subject'},
              'video': {'text', 'video', 'timeline', 'subject'}, 'audio': {'text', 'video', 'audio', 'timeline'},
              'subject': {'image', 'video', 'audio'}}


def downstream_kinds(snapshot, source):
    if source.kind == 'subject':
        doc = next(doc for doc in snapshot.editorDocuments if doc.id == source.binding.objectRef.id)
        if doc.data.main and doc.data.main.kind == 'video':
            return {'video'}
    return DOWNSTREAM.get(source.kind, set())


def prompt_projection(data, spec):
    """Structured text is authoritative; labels never identify relationships."""
    if spec['promptContent'] is None:
        return spec['prompt']
    references = {ref['id']: ref for ref in data['references']}
    items = {item['id']: item for item in data['canvasItems']}
    assets = {asset['id']: asset for asset in data['assets']}
    result = []
    for part in spec['promptContent']:
        if part['type'] == 'text':
            result.append(part['text'])
        else:
            ref = references.get(part['referenceId'])
            if not ref or not ref['active'] or ref['targetSpecId'] != spec['id']:
                raise ContractError('reference_invalid', '正文引用的参考关系已不存在。')
            source = items.get(ref['source']['id']) if ref['binding'] == 'follow_current' else assets.get(ref['source']['assetId'])
            result.append('@' + ((source.get('title') or source.get('name') or '参考') if source else '参考'))
    return ''.join(result)


def visible_item_ids(snapshot):
    items = {item.id: item for item in snapshot.canvasItems}
    states = {}
    for item in snapshot.canvasItems:
        chain, current = [], item
        while current is not None and current.id not in states:
            chain.append(current)
            current = items.get(current.parentId)
        visible = states[current.id] if current else True
        for value in reversed(chain):
            visible = visible and value.visible
            states[value.id] = visible
    return {key for key, visible in states.items() if visible}


def asset_ref(asset):
    return AssetRef(assetId=asset.id, revision=asset.revision, fingerprint=asset.fingerprint, kind=asset.kind)


def current_asset(snapshot, item):
    if item.binding.role == 'material':
        return next((a for a in snapshot.assets if a.id == item.binding.objectRef.assetId), None)
    if item.binding.role == 'generation':
        if item.binding.materialOverride is not None:
            return next((a for a in snapshot.assets if a.id == item.binding.materialOverride.assetId), None)
        spec = next(s for s in snapshot.generationSpecs if s.id == item.binding.objectRef.id)
        result = next((r for r in snapshot.results if r.id == spec.currentResultId), None)
        return next((a for a in snapshot.assets if result and a.id == result.asset.assetId), None)
    return None


def resolve_reference(snapshot, reference):
    """Resolve one level only: hidden/current and cyclic sources are ordinary reads."""
    assets = {a.id: a for a in snapshot.assets}
    source_version, document_version, sources, source_title = None, None, [], ''
    if reference.binding == 'pinned':
        source_version = reference.sourceVersion
        sources = [(reference.source, reference.subjectRole)]
    else:
        item = next((i for i in snapshot.canvasItems if i.id == reference.source.id), None)
        if item is None:
            raise ContractError('reference_invalid', '参考节点不存在。', reference.id)
        source_version = ObjectVersion(objectType='canvas_item', id=item.id, revision=item.revision)
        source_title = item.title
        if item.kind == 'subject':
            doc = next(d for d in snapshot.editorDocuments if d.id == item.binding.objectRef.id)
            document_version = ObjectVersion(objectType='editor_document', id=doc.id, revision=doc.revision)
            if doc.data.main:
                sources = [(doc.data.main, 'main'), *((a, 'auxiliary') for a in doc.data.auxiliaries),
                           *(([(doc.data.voice, 'voice')]) if doc.data.voice else [])]
        else:
            asset = current_asset(snapshot, item)
            if asset:
                sources = [(asset_ref(asset), None)]
    if not sources:
        raise ContractError('reference_invalid', '空白节点或缺失素材不能作为参考。', reference.id)
    resolved = []
    for ref, subject_role in sources:
        asset = assets.get(ref.assetId)
        if asset is None or asset.fingerprint != ref.fingerprint:
            raise ContractError('reference_invalid', '参考素材不存在或内容已变化。', reference.id)
        resolved.append(ResolvedReference(referenceId=reference.id, role=reference.role, asset=asset_ref(asset),
            metadata=asset.metadata, sourceVersion=source_version, documentVersion=document_version, subjectRole=subject_role,
            name=asset.name, sourceTitle=source_title))
    return resolved


def reference_policy(spec, values, rules=None, *, selected_frame=False):
    output = []
    frame_roles = set(rules.frameRoles) if rules and rules.frameRoles else {'first_frame'}
    for value in values:
        kind = value.asset.kind
        # Speech reads its own text; only a music model that follows a recording takes one.
        ignored = spec.kind == 'audio' and not (kind == 'audio' and spec.parameters.creationType == 'music'
                                                and rules is not None and 'audio' in rules.allowedKinds)
        if spec.kind == 'video' and spec.parameters.videoMode == 'first_frame':
            ignored = kind not in {'text', 'image'} or kind == 'image' and (value.subjectRole is not None or not selected_frame or value.role not in frame_roles)
            if selected_frame and value.role in {'first_frame', 'last_frame'} and ignored:
                raise ContractError('reference_invalid', '指定的首尾帧不能作为图片传入当前模型；请使用明确的图片素材引用，且不要填写主体槽位 subjectRole。', value.referenceId)
        if value.subjectRole is not None and rules is not None:
            ignored = ignored or kind not in rules.allowedKinds or (kind == 'image' and not rules.maxImages) or (kind == 'video' and not rules.maxVideos) or (kind == 'audio' and not rules.maxAudios)
            if spec.kind == 'video' and spec.parameters.videoMode == 'continuation' and kind == 'video':
                ignored = True  # Subject clips are not a remote continuation ID.
        transmission = 'context_only' if ignored else 'latent' if value.role == 'continuation' else 'metadata' if spec.kind == 'text' and kind in {'audio', 'video'} else 'content'
        output.append(value.model_copy(update={'transmission': transmission}))
    return tuple(output)


def resolved_inputs(snapshot, spec_id, rules=None, *, excluded_ids=frozenset()):
    spec = next(s for s in snapshot.generationSpecs if s.id == spec_id)
    mentioned = list(dict.fromkeys(part.referenceId for part in spec.promptContent or () if part.type == 'reference'))
    refs = sorted((r for r in snapshot.references if r.active and r.targetSpecId == spec_id and r.id not in excluded_ids),
        key=lambda r: (mentioned.index(r.id) if r.id in mentioned else len(mentioned), r.order, str(r.id)))
    items = {item.id: item for item in snapshot.canvasItems}
    for ref in refs:
        if ref.binding == 'follow_current':
            source = items.get(ref.source.id)
            if source and spec.kind not in downstream_kinds(snapshot, source):
                raise ContractError('reference_invalid', '来源节点当前内容不支持作为该类型节点的参考。', ref.id)
    return reference_policy(spec, [value for ref in refs for value in resolve_reference(snapshot, ref)], rules,
        selected_frame=any(ref.role in {'first_frame', 'last_frame'} for ref in refs))


def reference_source_visible(ref, visible):
    if ref.binding == 'follow_current':
        return ref.source.id in visible
    return ref.sourceVersion is None or ref.sourceVersion.id in visible


def current_generation_inputs(snapshot, spec, rules, visible=None):
    """Project current inputs while preserving saved references and history."""
    if visible is None:
        visible = visible_item_ids(snapshot)
    excluded = {ref.id for ref in snapshot.references
                if ref.active and ref.targetSpecId == spec.id and not reference_source_visible(ref, visible)}
    references = resolved_inputs(snapshot, spec.id, rules, excluded_ids=excluded)
    if spec.promptContent is not None and excluded:
        parts = tuple(part for part in spec.promptContent if part.type == 'text' or part.referenceId not in excluded)
        if parts != spec.promptContent:
            data = snapshot.model_dump(mode='json', include={'references', 'canvasItems', 'assets'})
            spec = spec.model_copy(update={'promptContent': parts})
            spec = spec.model_copy(update={'prompt': prompt_projection(data, spec.model_dump(mode='json'))})
    return spec, references


def validate_frame_reference_limit(snapshot):
    items = {item.id: item for item in snapshot.canvasItems}
    visible = visible_item_ids(snapshot)
    for spec in snapshot.generationSpecs:
        if spec.kind != 'video' or spec.parameters.videoMode != 'first_frame':
            continue
        images = set()
        for ref in snapshot.references:
            if not ref.active or ref.targetSpecId != spec.id or not reference_source_visible(ref, visible):
                continue
            if ref.binding == 'pinned' and ref.source.kind == 'image' and ref.subjectRole is None:
                images.add(('asset', ref.source.assetId))
            elif ref.binding == 'follow_current' and items[ref.source.id].kind == 'image':
                images.add(('node', ref.source.id))
        if len(images) > FRAME_IMAGE_LIMIT:
            raise ContractError('reference_invalid', f'首尾帧模式最多连接 {FRAME_IMAGE_LIMIT} 个图片节点，请先断开多余连线。', spec.id)


def reference_evidence(snapshot, spec, reference):
    result = next((r for r in snapshot.results if r.id == spec.currentResultId), None)
    if result is None:
        return 'pending'
    job = next((j for j in snapshot.jobs if j.id == result.jobId), None)
    if job is None:
        return 'unknown'
    old = [r for r in job.inputSnapshot.references if r.referenceId == reference.id and r.transmission != 'context_only']
    if reference.binding == 'pinned' and reference.sourceVersion:
        # A historical subject reference expands into independent fixed slots.
        # Match the recorded source + asset + role, not the newly split ID.
        old = [r for r in job.inputSnapshot.references
               if r.sourceVersion and r.sourceVersion.id == reference.sourceVersion.id
               and r.asset.assetId == reference.source.assetId and r.role == reference.role
               and r.subjectRole == reference.subjectRole and r.transmission != 'context_only']
        old = old[:1]

    try:
        now = reference_policy(spec, resolve_reference(snapshot, reference), job.inputSnapshot.model.inputRules,
            selected_frame=any(r.active and r.role in {'first_frame', 'last_frame'} and r.targetSpecId == spec.id for r in snapshot.references))
        now = [r for r in now if r.transmission != 'context_only']
    except ContractError:
        return 'unavailable'
    # Layout/visibility/title revisions are not evidence of a creative change.
    identity = lambda refs: [(r.asset.assetId, r.asset.fingerprint, r.role, r.subjectRole) for r in refs]
    if reference.binding == 'pinned' and reference.sourceVersion:
        source = next((i for i in snapshot.canvasItems if i.id == reference.sourceVersion.id), None)
        asset = current_asset(snapshot, source) if source else None
        if source and source.kind == 'subject':
            doc = next(d for d in snapshot.editorDocuments if d.id == source.binding.objectRef.id)
            candidates = ([doc.data.main] if reference.subjectRole == 'main' else
                          [doc.data.voice] if reference.subjectRole == 'voice' else doc.data.auxiliaries)
            matches = any(ref and ref.assetId == reference.source.assetId and ref.fingerprint == reference.source.fingerprint for ref in candidates)
        else:
            matches = bool(asset and asset.id == reference.source.assetId and asset.fingerprint == reference.source.fingerprint)
        if not matches:
            return 'pending'
    return 'used' if old and identity(old) == identity(now) else 'pending'


class CreationCanvas:
    def __init__(self, projects):
        self.projects = projects
        from opendirector.application.creation_assets import CreationAssets
        self.assets = CreationAssets(projects)

    def _available(self, asset):
        return self.assets.reference_available(asset.projectId, asset.id)

    def execute(self, command, *, session=None, source='ui'):
        return self.projects.commit(command, lambda before: self.apply(before, command), session=session, source=source)

    def apply(self, before, command):
        data = before.model_dump(mode='json')
        operations = command.payload.operations if command.type == 'creation.batch' else [command]
        for operation in operations:
            if operation.type != 'canvas.applyEdit':
                self._mutate(data, operation.type, operation.payload)
        # Publish against the final document in this atomic batch.
        published = set()
        for operation in operations:
            if operation.type == 'canvas.applyEdit':
                if operation.payload.itemId in published:
                    raise ContractError('invalid_input', '一次提交只能对同一节点应用一次编辑。')
                published.add(operation.payload.itemId)
                self._apply_edit(data, operation.payload, before, command)
        previous_specs = {str(spec.id): spec.model_dump(mode='json') for spec in before.generationSpecs}
        for spec in data['generationSpecs']:
            previous = previous_specs.get(spec['id'])
            if spec['promptContent'] is not None and (previous is None or any(spec[key] != previous[key] for key in ('prompt', 'promptContent'))):
                spec['prompt'] = prompt_projection(data, spec)
        # One revision per object per atomic transaction, regardless of how many
        # semantic operations touch it. New objects always begin at revision 1.
        for collection in COLLECTIONS.values():
            old = {str(obj.id): obj.model_dump(mode='json') for obj in getattr(before, collection)}
            for obj in data[collection]:
                previous = old.get(str(obj['id']))
                obj['revision'] = previous['revision'] if previous else 1
                if previous and obj != previous:
                    obj['revision'] += 1
        result = ProjectSnapshot.model_validate(data)
        validate_frame_reference_limit(result)
        from opendirector.application.subject_library import SubjectLibrary
        SubjectLibrary(self.projects).validate_imports(before, result)
        previous_inputs = {value.id: value for doc in before.editorDocuments for value in doc.inputConnections}
        items = {item.id: item for item in result.canvasItems}
        for doc in result.editorDocuments:
            for value in doc.inputConnections:
                # The retained snapshot survives later source changes. New assembly
                # still requires an editable source of the declared media type.
                if previous_inputs.get(value.id) == value:
                    continue
                source = items.get(value.sourceId)
                if source is None or source.kind != value.asset.kind or doc.kind not in downstream_kinds(result, source):
                    raise ContractError('reference_invalid', '该节点类型不能装配到目标节点。', value.sourceId)
                self._editable(data, value.sourceId)
                actual = current_asset(result, source)
                if actual is None or asset_ref(actual) != value.asset:
                    raise ContractError('reference_invalid', '装配素材与来源节点当前内容不一致，请重新读取。', value.sourceId)
                if actual.kind in {'video', 'audio'} and actual.metadata.durationSeconds is None:
                    raise ContractError('invalid_input', '请先读取视频或音频的实际时长，再添加到目标节点。', value.sourceId)
                self._available(actual)
        return result

    def _apply_edit(self, data, payload, before, command):
        item = self._editable(data, payload.itemId)
        if item['kind'] != 'image' or item['binding']['role'] not in {'material', 'generation'}:
            raise ContractError('role_restricted', '图片编辑只能应用到已有图片内容。', payload.itemId)
        snapshot = ProjectSnapshot.model_validate(data)
        current = current_asset(snapshot, next(i for i in snapshot.canvasItems if i.id == payload.itemId))
        if current is None or asset_ref(current) != payload.source:
            raise ContractError('revision_conflict', '编辑来源已变化，请重新打开图片编辑器。', payload.itemId)
        doc = next((d for d in snapshot.editorDocuments if d.id == payload.editorId), None)
        if doc is None or doc.kind not in {'layers', 'image_crop'} or not any(ref['id'] == str(doc.id) for ref in item['editors']):
            raise ContractError('reference_invalid', '请先关联本次图片编辑文档。', payload.editorId)
        old_doc = next((d for d in before.editorDocuments if d.id == doc.id), None)
        if doc.kind == 'image_crop':
            # A reopened crop keeps its original input; a new crop starts at the
            # current image. Swapping unrelated sources is not a crop operation.
            prior_item = next((i for i in before.canvasItems if i.id == payload.itemId), None)
            retained = old_doc is not None and old_doc.kind == 'image_crop' and old_doc.data.source == doc.data.source and prior_item is not None and any(ref.id == doc.id for ref in prior_item.editors)
            if doc.data.source != payload.source and not retained:
                raise ContractError('reference_invalid', '裁剪来源不属于当前图片或保留的编辑文档。', doc.id)
            source_asset = self.assets._reference(snapshot, doc.data.source)
            if not source_asset.metadata.width or not source_asset.metadata.height:
                raise ContractError('invalid_input', '裁剪来源缺少实际图片尺寸。', source_asset.id)
            if doc.data.options.output_size(source_asset.metadata.width, source_asset.metadata.height) != (doc.data.width, doc.data.height):
                raise ContractError('invalid_input', '裁剪参数与输出尺寸不一致。', doc.id)

        expected = {(v.objectType, v.id): v.revision for v in command.expectedVersions}
        if old_doc and expected.get(('editor_document', doc.id)) != old_doc.revision:
            raise ContractError('revision_conflict', '应用编辑需要编辑文档版本。', doc.id)
        output = self.assets._reference(snapshot, payload.asset)
        if output.kind != 'image' or output.id == current.id:
            raise ContractError('invalid_input', '编辑输出必须是独立图片素材。', output.id)
        if (output.metadata.width, output.metadata.height) != (doc.data.width, doc.data.height):
            raise ContractError('invalid_input', '编辑输出尺寸与编辑文档不一致。', output.id)
        if output.provenance.source != 'upload' or any(ref.assetId == output.id for ref in asset_references(snapshot)):
            raise ContractError('role_restricted', '编辑输出已被使用，请保留其原有来源。', output.id)
        # A detached historical upload is not an unpublished render artifact.
        if any(ref.assetId == output.id for record in self.projects.history(before.project.id)
               for ref in asset_references(record.snapshot)):
            raise ContractError('role_restricted', '编辑输出已有历史用途，不能改写其来源。', output.id)
        sources = {current.id: asset_ref(current)}
        for ref in asset_references(doc):
            sources[ref.assetId] = ref
        for ref in (*sources.values(), payload.asset):
            self._available(self.assets._reference(snapshot, ref))
        revision = 1 if old_doc is None else old_doc.revision + (old_doc != doc)
        asset = self._get(data, 'asset', output.id)
        asset['provenance'] = dict(source='edited', editorDocumentId=str(doc.id),
            editorRevision=revision, sources=[ref.model_dump(mode='json') for ref in sources.values()])
        item['editors'] = [ref for ref in item['editors'] if ref['kind'] not in {'layers', 'image_crop'} or ref['id'] == str(doc.id)]
        if item['binding']['role'] == 'generation':
            item['binding']['materialOverride'] = payload.asset.model_dump(mode='json')
        else:
            item['binding']['objectRef'] = payload.asset.model_dump(mode='json')

    @staticmethod
    def _get(data, kind, object_id):
        obj = next((o for o in data[COLLECTIONS[kind]] if str(o['id']) == str(object_id)), None)
        if obj is None:
            raise ContractError('missing_object', '业务对象不存在。', object_id)
        return obj

    @classmethod
    def _editable(cls, data, item_id, *, unlocking=False):
        item = cls._get(data, 'canvas_item', item_id)
        current = item
        seen = set()
        while current:
            if str(current['id']) in seen:
                raise ContractError('invalid_input', '分组层级存在循环。', item_id)
            seen.add(str(current['id']))
            if current['locked'] and not (unlocking and current is item):
                raise ContractError('role_restricted', '节点或所属分组已锁定。', item_id)
            current = cls._get(data, 'canvas_item', current['parentId']) if current['parentId'] else None
        return item

    def _validate_timeline(self, document, snapshot):
        sources = {clip.resource.asset.assetId: clip.resource.asset for track in document.data.tracks for clip in track.clips}
        for ref in sources.values():
            self._available(self.assets._reference(snapshot, ref))
        for track in document.data.tracks:
            for clip in track.clips:
                asset = self.assets._reference(snapshot, clip.resource.asset)
                duration = asset.metadata.durationSeconds
                if asset.kind != 'image' and (duration is None or clip.outPoint > duration + 1e-6):
                    raise ContractError('invalid_input', '时间线片段超出实际素材时长。', asset.id)

    def _publish_render(self, data, payload):
        item = self._editable(data, payload.itemId)
        if item['kind'] != 'timeline' or item['binding']['objectRef']['id'] != str(payload.editorId):
            raise ContractError('reference_invalid', '合成来源不是当前时间线。', payload.itemId)
        snapshot = ProjectSnapshot.model_validate(data)
        doc = next(d for d in snapshot.editorDocuments if d.id == payload.editorId)
        self._validate_timeline(doc, snapshot)
        output = self.assets._reference(snapshot, payload.asset)
        if output.kind != 'video' or output.provenance.source != 'upload' or any(r.assetId == output.id for r in asset_references(snapshot)):
            raise ContractError('role_restricted', '合成输出必须是尚未发布的视频素材。', output.id)
        if any(r.assetId == output.id for record in self.projects.history(snapshot.project.id) for r in asset_references(record.snapshot)):
            raise ContractError('role_restricted', '合成输出已有历史用途，不能改写来源。', output.id)
        duration = 0
        for track in doc.data.tracks:
            cursor = 0
            for clip in track.clips:
                start = cursor if track.kind == 'visual' else (clip.start or 0)
                cursor = start + clip.duration()
                duration = max(duration, cursor)
        meta = output.metadata
        if (meta.width, meta.height) != (doc.data.width, doc.data.height) or meta.durationSeconds is None or abs(meta.durationSeconds - duration) > max(.15, 2 / doc.data.fps):
            raise ContractError('invalid_input', '合成视频的实际尺寸或时长与时间线不一致。', output.id)
        sources = {clip.resource.asset.assetId: clip.resource.asset for track in doc.data.tracks for clip in track.clips}
        for ref in (*sources.values(), payload.asset):
            self._available(self.assets._reference(snapshot, ref))
        asset = self._get(data, 'asset', output.id)
        asset['provenance'] = dict(source='rendered', editorDocumentId=str(doc.id), editorRevision=doc.revision,
                                  sources=[ref.model_dump(mode='json') for ref in sources.values()])
        if any(i['id'] == str(payload.outputId) for i in data['canvasItems']):
            raise ContractError('invalid_input', '输出节点标识已存在。', payload.outputId)
        result = VideoCanvasItem(id=payload.outputId, projectId=snapshot.project.id, kind='video', title=payload.title,
            position=payload.position, binding=dict(role='material', objectRef=payload.asset))
        data['canvasItems'].append(result.model_dump(mode='json'))

    def _mutate(self, data, kind, payload):
        value = payload.model_dump(mode='json', exclude_unset=True)
        if kind in {'editor.create', 'editor.save'} and payload.document.kind == 'timeline':
            self._validate_timeline(payload.document, ProjectSnapshot.model_validate(data))
        if kind == 'canvas.publishMedia':
            from .creation_media_publication import publish_media
            publish_media(self, data, payload)
        elif kind == 'timeline.publishRender':
            self._publish_render(data, payload)
        elif kind == 'canvas.createGeneration':
            self._mutate(data, 'generationSpec.create', SpecWrite(spec=payload.spec))
            self._mutate(data, 'canvas.show', CanvasShow(item=payload.item))
            for reference in payload.references:
                self._mutate(data, 'reference.add', ReferenceWrite(reference=reference))
        elif kind in {'generationSpec.create', 'editor.create'}:
            key, collection = ('spec', 'generationSpecs') if kind.startswith('generation') else ('document', 'editorDocuments')
            obj = getattr(payload, key).model_dump(mode='json')
            if str(obj['projectId']) != str(data['project']['id']):
                raise ContractError('invalid_input', '对象不属于当前工程。')
            if any(str(o['id']) == str(obj['id']) for o in data[collection]):
                raise ContractError('invalid_input', '对象标识已存在。', UUID(obj['id']))
            if key == 'spec' and obj['currentResultId'] is not None:
                raise ContractError('invalid_input', '新配置不能指定生成结果。')
            data[collection].append(obj)
        elif kind in {'generationSpec.update', 'editor.save'}:
            key, object_type = ('spec', 'generation_spec') if kind.startswith('generation') else ('document', 'editor_document')
            obj = getattr(payload, key).model_dump(mode='json')
            old = self._get(data, object_type, obj['id'])
            immutable = ('kind', 'purpose', 'currentResultId') if key == 'spec' else ('kind',)
            if any(obj[field] != old[field] for field in (*immutable, 'projectId')):
                raise ContractError('role_restricted', '配置编辑不能修改对象角色或结果选择。', UUID(obj['id']))
            for item in data['canvasItems']:
                binding = item['binding']
                ref = binding.get('objectRef') or binding.get('draftSpec')
                attached = any(str(editor['id']) == str(obj['id']) for editor in item.get('editors', []))
                if (ref and str(ref.get('id')) == str(obj['id'])) or attached:
                    self._editable(data, item['id'])
            old.update(obj)
        elif kind == 'canvas.show':
            obj = payload.item.model_dump(mode='json')
            if str(obj['projectId']) != str(data['project']['id']):
                raise ContractError('invalid_input', '节点不属于当前工程。')
            old = next((o for o in data['canvasItems'] if o['id'] == obj['id']), None)
            if old:
                # Restoring an existing identity cannot replace its binding or
                # overwrite newer presentation data supplied by another client.
                if {**old, 'visible': True} != {**obj, 'visible': True}:
                    raise ContractError('revision_conflict', '请读取原节点后恢复显示。', payload.item.id)
                old['visible'] = True
                parent_id = old['parentId']
                seen = {old['id']}
                while parent_id:
                    if parent_id in seen:
                        raise ContractError('invalid_input', '分组层级存在循环。', payload.item.id)
                    seen.add(parent_id)
                    parent = self._get(data, 'canvas_item', parent_id)
                    parent['visible'] = True
                    parent_id = parent['parentId']
            else:
                if obj['binding']['role'] == 'material' and not ({'width', 'height'} & payload.item.model_fields_set):
                    asset = self._get(data, 'asset', obj['binding']['objectRef']['assetId'])
                    metadata = asset.get('metadata') or {}
                    obj['width'], obj['height'] = media_card_size(obj['kind'], metadata.get('width'),
                        metadata.get('height'), obj['width'], obj['height'])
                obj['visible'] = True
                data['canvasItems'].append(obj)
        elif kind == 'canvas.move':
            for move in payload.items:
                self._editable(data, move.itemId)['position'] = move.position.model_dump()
        elif kind == 'canvas.update':
            item = self._editable(data, payload.itemId, unlocking=value.get('locked') is False)
            item.update({key: val for key, val in value.items() if key != 'itemId' and (val is not None or key == 'parentId')})
        elif kind == 'canvas.hide':
            self._editable(data, payload.itemId)['visible'] = False
        elif kind == 'canvas.materialize':
            item = self._editable(data, payload.itemId)
            binding = item['binding']
            if item['kind'] == 'text' and binding['role'] == 'generation' and payload.discardGenerationDraft:
                binding['materialOverride'] = payload.asset.model_dump(mode='json')
                return
            if binding['role'] not in {'empty', 'material'} or item['kind'] != payload.asset.kind:
                raise ContractError('role_restricted', '该节点不能替换为素材。', payload.itemId)
            draft = binding.get('draftSpec')
            if draft:
                spec = self._get(data, 'generation_spec', draft['id'])
                if (spec['prompt'] or spec['modelConfigId'] or spec['promptContent']) and not payload.discardGenerationDraft:
                    raise ContractError('revision_conflict', '替换已有生成草稿需要明确确认。', payload.itemId)
            item['binding'] = {'role': 'material', 'objectRef': payload.asset.model_dump(mode='json')}
            asset = next((entry for entry in data['assets'] if entry['id'] == str(payload.asset.assetId)), None)
            if asset:
                metadata = asset.get('metadata') or {}
                item['width'], item['height'] = media_card_size(item['kind'], metadata.get('width'), metadata.get('height'), item['width'], item['height'])
            if item['kind'] == 'image':
                item['editors'] = [ref for ref in item.get('editors', []) if ref['kind'] not in {'layers', 'image_crop'}]
        elif kind == 'canvas.configure':
            item = self._editable(data, payload.itemId)
            spec = self._get(data, 'generation_spec', payload.specId)
            binding = item['binding']
            if item['kind'] != spec['kind'] or item['kind'] not in {'text', 'image', 'video', 'audio'}:
                raise ContractError('role_restricted', '生成配置类型不匹配。', payload.itemId)
            if binding['role'] == 'material' and not (item['kind'] == 'text' and payload.preserveMaterial):
                raise ContractError('role_restricted', '素材节点不能直接改为生成节点。', payload.itemId)
            if binding['role'] == 'generation' and binding['objectRef']['id'] != str(payload.specId):
                raise ContractError('role_restricted', '不能替换节点已有生成配置。', payload.itemId)
            if binding['role'] == 'material':
                spec['purpose'] = 'generation'
                item['binding'] = {'role': 'generation', 'objectRef': {'objectType': 'generation_spec', 'id': spec['id']},
                    'materialOverride': binding['objectRef']}
            elif spec['purpose'] == 'generation':
                item['binding'] = {'role': 'generation', 'objectRef': {'objectType': 'generation_spec', 'id': spec['id']}}
            else:
                item['binding'] = {'role': 'empty', 'draftSpec': {'objectType': 'generation_spec', 'id': spec['id']}}
        elif kind == 'canvas.attachEditor':
            item = self._editable(data, payload.itemId)
            doc = self._get(data, 'editor_document', payload.editorId)
            if 'editors' not in item:
                raise ContractError('role_restricted', '仅媒体节点可附加媒体编辑器。', payload.itemId)
            if not any(ref['id'] == doc['id'] for ref in item['editors']):
                if any(ref['kind'] == doc['kind'] for ref in item['editors']):
                    raise ContractError('role_restricted', '节点已有同类型编辑文档，请更新或先解除关联。', payload.itemId)
                item['editors'].append({'objectType': 'editor_document', 'id': doc['id'], 'kind': doc['kind']})
        elif kind == 'canvas.detachEditor':
            item = self._editable(data, payload.itemId)
            self._get(data, 'editor_document', payload.editorId)
            if 'editors' not in item:
                raise ContractError('role_restricted', '仅媒体节点可解除附属编辑器。', payload.itemId)
            item['editors'] = [ref for ref in item['editors'] if ref['id'] != str(payload.editorId)]
        elif kind in {'reference.add', 'reference.update', 'reference.restore'}:
            ref = payload.reference.model_dump(mode='json')
            changed_source = False
            if str(ref['projectId']) != str(data['project']['id']):
                raise ContractError('invalid_input', '参考不属于当前工程。')
            spec = self._get(data, 'generation_spec', payload.reference.targetSpecId)
            for item in data['canvasItems']:
                binding_ref = item['binding'].get('objectRef') or item['binding'].get('draftSpec')
                if binding_ref and binding_ref.get('id') == spec['id']:
                    self._editable(data, item['id'])
            if kind == 'reference.add':
                if not payload.reference.active:
                    raise ContractError('invalid_input', '新增参考必须为有效关系。', payload.reference.id)
                duplicate = next((r for r in data['references'] if r['active'] and (r['targetSpecId'], r['binding'], r['source']) == (ref['targetSpecId'], ref['binding'], ref['source'])), None)
                if duplicate:
                    return
                if any(r['id'] == ref['id'] for r in data['references']):
                    raise ContractError('invalid_input', '参考标识已使用。', payload.reference.id)
                spec['purpose'] = 'generation'
                for item in data['canvasItems']:
                    # An empty binding serialises draftSpec as null, not absent.
                    if (item['binding'].get('draftSpec') or {}).get('id') == spec['id']:
                        item['binding'] = {'role': 'generation', 'objectRef': {'objectType': 'generation_spec', 'id': spec['id']}}
                data['references'].append(ref)
            else:
                old = self._get(data, 'reference', payload.reference.id)
                changed_source = (old['binding'], old['source']) != (ref['binding'], ref['source'])
                if kind == 'reference.restore':
                    if old['revision'] != ref['revision']:
                        raise ContractError('revision_conflict', '参考已发生变化，请重新读取后恢复。', payload.reference.id)
                    if old['active'] or not ref['active']:
                        raise ContractError('invalid_input', '仅能恢复已移除的参考关系。', payload.reference.id)
                    if (old['targetSpecId'], old['binding'], old['source']) != (ref['targetSpecId'], ref['binding'], ref['source']):
                        raise ContractError('invalid_input', '恢复参考不能改变原有来源和目标。', payload.reference.id)
                elif old['active'] != ref['active']:
                    raise ContractError('role_restricted', '参考的移除与恢复需使用对应生命周期命令。', payload.reference.id)
                if old['targetSpecId'] != ref['targetSpecId']:
                    raise ContractError('invalid_input', '不能转移参考的目标配置。', payload.reference.id)
                if any(other['id'] != old['id'] and other['active'] and (other['targetSpecId'], other['binding'], other['source']) == (ref['targetSpecId'], ref['binding'], ref['source']) for other in data['references']):
                    raise ContractError('reference_invalid', '该参考来源已连接到目标配置。', payload.reference.id)
                old.update(ref)
            if ref['binding'] == 'follow_current':
                source = self._get(data, 'canvas_item', ref['source']['id'])
                source_kind = source['kind']
                if kind in {'reference.add', 'reference.restore'} or changed_source:
                    self._editable(data, source['id'])
            else:
                source_kind = ref['source']['kind']
            snapshot = ProjectSnapshot.model_validate(data)
            source_item = next((item for item in snapshot.canvasItems if str(item.id) == ref['source'].get('id')), None)
            allowed = downstream_kinds(snapshot, source_item) if source_item else DOWNSTREAM.get(source_kind, set())
            if spec['kind'] not in allowed:
                raise ContractError('reference_invalid', '该节点类型不能作为目标节点的参考。', payload.reference.id)
            for value in resolve_reference(snapshot, payload.reference):
                self._available(next(asset for asset in snapshot.assets if asset.id == value.asset.assetId))
        elif kind == 'reference.remove':
            ref = self._get(data, 'reference', payload.referenceId)
            spec = self._get(data, 'generation_spec', ref['targetSpecId'])
            for item in data['canvasItems']:
                owner = item['binding'].get('objectRef') or item['binding'].get('draftSpec')
                if owner and owner.get('id') == spec['id']:
                    self._editable(data, item['id'])
            if spec['promptContent'] is not None:
                spec['promptContent'] = [part for part in spec['promptContent'] if part.get('referenceId') != ref['id']]
            ref['active'] = False
        elif kind == 'generationSpec.setPrompt':
            spec = self._get(data, 'generation_spec', payload.specId)
            for item in data['canvasItems']:
                owner = item['binding'].get('objectRef') or item['binding'].get('draftSpec')
                if owner and owner.get('id') == spec['id']:
                    self._editable(data, item['id'])
            spec.update(prompt=payload.prompt, promptContent=[part.model_dump(mode='json') for part in payload.promptContent] if payload.promptContent is not None else None)
        elif kind == 'result.select':
            spec = self._get(data, 'generation_spec', payload.specId)
            for item in data['canvasItems']:
                if item['binding'].get('objectRef', {}).get('id') == spec['id']:
                    self._editable(data, item['id'])
            result = self._get(data, 'result', payload.resultId)
            if result['specId'] != spec['id']:
                raise ContractError('invalid_input', '结果不属于该生成配置。', payload.resultId)
            stream, _ = self.assets.open_media(data['project']['id'], result['asset']['assetId'])
            stream.close()
            spec['currentResultId'] = result['id']
            for item in data['canvasItems']:
                if item['binding'].get('objectRef', {}).get('id') == spec['id'] and item['binding']['role'] == 'generation':
                    item['binding']['materialOverride'] = None
                    item['editors'] = [ref for ref in item.get('editors', []) if ref['kind'] not in {'layers', 'image_crop'}]
        else:
            raise ContractError('capability_unsupported', '未实现该业务命令。')

    def item_status(self, project_id: UUID, item_id: UUID) -> dict:
        snapshot = self.projects.snapshot(project_id)
        item = next((item for item in snapshot.canvasItems if item.id == item_id), None)
        if item is None:
            raise ProjectError('画布组件不在当前工程中。', 404)
        spec_id = (item.binding.objectRef.id if item.binding.role == 'generation' else
            item.binding.draftSpec.id if item.binding.role == 'empty' and item.binding.draftSpec else None)
        spec = next((spec for spec in snapshot.generationSpecs if spec.id == spec_id), None)
        related = [job for job in reversed(snapshot.jobs) if job.originItemId == item.id or
            spec_id is not None and job.inputSnapshot.spec.id == spec_id]
        active = [job for job in related if job.status in {'queued', 'running'}]
        jobs = active[:50] if active else related[:1]
        asset = current_asset(snapshot, item)
        reference_sources = CanvasReferenceSources(
            followCurrent=FollowingReferenceSource(source=FollowingSource(id=item.id)),
            pinned=PinnedReferenceSource(source=asset_ref(asset), sourceVersion=CanvasItemVersion(
                objectType='canvas_item', id=item.id, revision=item.revision))) if asset else None
        return {'referenceSources': reference_sources.model_dump(mode='json') if reference_sources else None, 'item': item.model_dump(mode='json'),
            'generationSpec': {'id': str(spec.id), 'revision': spec.revision,
                'modelConfigId': str(spec.modelConfigId) if spec.modelConfigId else None,
                'currentResultId': str(spec.currentResultId) if spec.currentResultId else None} if spec else None,
            'status': jobs[0].status if jobs else 'idle',
            'jobs': [job.model_dump(mode='json', include={'id', 'revision', 'status', 'phase',
                'errorDetails', 'technicalCheck', 'cancelRequested', 'adopted', 'asset'}) for job in jobs],
            'asset': asset.model_dump(mode='json') if asset else None}

    async def wait_item_status(self, project_id: UUID, item_id: UUID, wait_seconds: float = 0) -> dict:
        current = await asyncio.to_thread(self.item_status, project_id, item_id)
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while current['status'] in {'queued', 'running'}:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.5, remaining))
            updated = await asyncio.to_thread(self.item_status, project_id, item_id)
            if updated != current:
                return updated
        return current

    def _asset_checker(self, snapshot):
        """One verified snapshot and one file check per asset in this query.

        Failures are cached too; the next query retries files that were restored.
        A projection shares one checker across reference edges and capabilities.
        """
        availability = {}

        def check_asset(asset):
            if asset.id not in availability:
                try:
                    availability[asset.id] = self.assets.reference_available(
                        asset.projectId, asset.id, snapshot=snapshot)
                except (ContractError, ProjectError, OSError) as error:
                    availability[asset.id] = error
            value = availability[asset.id]
            if isinstance(value, Exception):
                raise value
            return value

        return check_asset

    def reference_edges(self, project_id, snapshot=None, check_asset=None):
        """Authoritative canvas reference states for one snapshot.

        The canvas renders connection evidence from these states, so the open
        path may compute them without also resolving per-node capabilities and
        models. `projection()` passes its shared asset checker so both passes
        still check each asset file only once.
        """
        if snapshot is None:
            snapshot = self.projects.snapshot(project_id)
        if check_asset is None:
            check_asset = self._asset_checker(snapshot)

        edges = []
        for reference in snapshot.references:
            if not reference.active:
                continue
            spec = next(s for s in snapshot.generationSpecs if s.id == reference.targetSpecId)
            state = reference_evidence(snapshot, spec, reference)
            try:
                for value in resolve_reference(snapshot, reference):
                    check_asset(next(asset for asset in snapshot.assets if asset.id == value.asset.assetId))
            except (ContractError, ProjectError, OSError):
                state = 'unavailable'
            edges.append(dict(referenceId=reference.id, sourceItemId=reference.source.id if reference.binding == 'follow_current' else reference.sourceVersion.id if reference.sourceVersion else None,
                targetSpecId=spec.id, state=state))
        return edges

    def projection(self, project_id, snapshot=None):
        snapshot = self.projects.snapshot(project_id) if snapshot is None else snapshot
        check_asset = self._asset_checker(snapshot)
        visible = visible_item_ids(snapshot)
        edges = self.reference_edges(project_id, snapshot, check_asset)
        capabilities = {}
        models = gateway().models(self.projects.local.repository)
        public_models = {}
        presentation = snapshot.model_dump(mode='json')
        for item in snapshot.canvasItems:
            spec, model, references, available = None, None, (), False
            owner = item.binding.objectRef if item.binding.role == 'generation' else item.binding.draftSpec if item.binding.role == 'empty' else None
            if owner:
                spec = next(s for s in snapshot.generationSpecs if s.id == owner.id)
                try:
                    key = (str(spec.modelConfigId), spec.kind)
                    if key not in public_models:
                        config = models.resolve(str(spec.modelConfigId), 'agent' if spec.kind == 'text' else spec.kind)
                        public_models[key] = ModelConfigPublic.model_validate({k: v for k, v in config.items() if k != 'apiKey'} | {'inputRules': input_rules(config)})
                    model = public_models[key]
                    spec, references = current_generation_inputs(snapshot, spec, model.inputRules, visible)
                    for reference in references:
                        if reference.transmission != 'context_only':
                            check_asset(next(asset for asset in snapshot.assets if asset.id == reference.asset.assetId))
                except (ContractError, ProjectError, OSError, ValueError):
                    model = None
            asset = current_asset(snapshot, item)
            if asset:
                try:
                    check_asset(asset)
                    available = True
                except (ProjectError, ContractError, OSError):
                    pass
            effective = item
            try:
                self._editable(presentation, item.id)
            except ContractError:
                effective = item.model_copy(update={'locked': True})
            value = node_capabilities(effective, spec=spec, model=model, references=references, assetAvailable=available)
            if item.kind == 'subject':
                doc = next(d for d in snapshot.editorDocuments if d.id == item.binding.objectRef.id)
                if doc.data.main:
                    try:
                        check_asset(next(asset for asset in snapshot.assets if asset.id == doc.data.main.assetId))
                        value = value.model_copy(update={'canUseAsReference': not effective.locked})
                    except (ProjectError, ContractError, OSError):
                        pass
            capabilities[str(item.id)] = value
        return dict(snapshot=snapshot, visibleItemIds=[i.id for i in snapshot.canvasItems if i.id in visible],
                    hiddenItemIds=[i.id for i in snapshot.canvasItems if i.id not in visible], edges=edges, capabilities=capabilities)

    async def query(self, query: Query) -> JsonValue:
        if query.type == 'canvas.status':
            return await self.wait_item_status(query.projectId, query.objectId, query.waitSeconds)
        if query.type == 'asset.preview':
            target = self.projects.local.state_dir / 'previews' / str(query.projectId)
            return await asyncio.to_thread(CreationAssets(self.projects).preview, query.projectId, query.objectId, target)
        return to_jsonable_python(await asyncio.to_thread(self.query_snapshot, query))

    def query_snapshot(self, query):
        from opendirector.application.creation_history import CreationHistory
        snapshot = self.projects.snapshot(query.projectId)
        history = CreationHistory(self.projects)
        if query.type == 'project.get':
            return snapshot
        if query.type == 'generationSpec.versions':
            return history.versions(query.projectId, query.objectId)
        if query.type == 'canvas.versions':
            return history.node_versions(query.projectId, query.objectId)
        if query.type == 'asset.search':
            return history.asset_list(query.projectId, text=query.text, offset=query.offset, limit=query.limit)
        if query.type == 'asset.usage':
            assets = [a for a in snapshot.assets if query.objectId is None or a.id == query.objectId]
            return [history.usage(snapshot, a.id) for a in assets[query.offset:query.offset+query.limit]]
        snapshots = [snapshot] if query.revision is None else [record.snapshot for record in self.projects.history(query.projectId)]
        for state in snapshots:
            for (kind, object_id), value in object_index(state).items():
                if object_id == query.objectId and (query.type != 'job.get' or kind == 'job') and (query.revision is None or value.revision == query.revision):
                    return value
        raise ContractError('missing_object', '未找到该对象或历史版本。', query.objectId)
