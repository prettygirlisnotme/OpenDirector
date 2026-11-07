"""Project-scoped assistant persistence and native runtime streaming."""
import asyncio
from contextlib import aclosing, suppress
import json
import logging
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid5

from anyio import CancelScope
from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opendirector.agent.catalog import model_choices
from opendirector.agent.context import RunContext, RuntimeId
from opendirector.agent.runtime import AgentRuntimes, runtime_available, configured_runtimes, runtime_spec, runtime_catalog, runtime_status
from opendirector.agent.skills import skills
from opendirector.domain.projects import ProjectError
from opendirector.domain.rule_facts import MAX_REFERENCES, PROMPT_MAX_LENGTH
from opendirector.infrastructure.creation_projects import durable_json
from opendirector.infrastructure.local_projects import child, read_json
from opendirector_api.localization import localized_error_for_language

logger = logging.getLogger(__name__)


Runtime = RuntimeId


class Draft(BaseModel):
    conversationId: UUID | None = None
    model_config = ConfigDict(extra='forbid')
    draft: str = Field(default='', max_length=PROMPT_MAX_LENGTH)
    draftContent: list[dict[str, JsonValue]] | None = None
    assets: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=MAX_REFERENCES)
    runtime: Runtime = 'default'
    runtimeModel: str | None = Field(default=None, max_length=200)
    harnessModels: dict[Literal["opencode", "codex", "claude", "jiuwenswarm", "deepseek"], Annotated[str, Field(max_length=200)] | None] = Field(default_factory=dict)
    thinking: Literal['auto', 'low', 'medium', 'high'] = 'auto'
    builtinModelThinking: dict[str, Literal['auto', 'low', 'medium', 'high']] = Field(default_factory=dict)
    modelSelections: dict[str, str | None] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=1)


class Turn(BaseModel):
    conversationId: UUID | None = None
    model_config = ConfigDict(extra='forbid')
    id: UUID
    modelId: UUID | None = None
    text: str = Field(min_length=1, max_length=PROMPT_MAX_LENGTH)
    content: list[dict[str, JsonValue]] | None = None
    assets: list[UUID] = Field(default_factory=list, max_length=MAX_REFERENCES)
    runtime: Runtime = 'default'
    runtimeModel: str | None = Field(default=None, max_length=200)
    thinking: Literal['auto', 'low', 'medium', 'high'] = 'auto'
    modelSelections: dict[str, str | None] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=1)


class Conversations:
    def __init__(self, projects):
        self.projects = projects

    def path(self, project_id: UUID, session: str | None = None) -> Path:
        with self.projects.local.transaction() as db:
            self.projects._recover()
            directory, _, _ = self.projects._load(db, project_id)
            if session:
                self.projects._lease(db, project_id, directory, session)
            return directory

    def update(self, project_id: UUID, session: str | None = None, *, draft: Draft | None = None,
               begin: dict | None = None, finish: dict | None = None, conversation_id: UUID | None = None) -> dict:
        with self.projects.local.transaction() as db:
            self.projects._recover()
            directory, _, _ = self.projects._load(db, project_id)
            if session:
                self.projects._lease(db, project_id, directory, session)
            folder = child(directory, 'agent-runs')
            folder.mkdir(exist_ok=True)
            file = child(folder, 'conversation.json')
            value = read_json(file) if file.exists() else {'messages': [], **Draft().model_dump()}
            value['conversationId'] = value.get('conversationId') or str(uuid5(project_id, 'assistant'))
            supplied = draft.conversationId if draft is not None else conversation_id
            if (draft is not None or begin is not None or finish is not None) and str(supplied) != value['conversationId'] and (supplied is not None or value.get('archivedConversations')):
                raise ProjectError('当前对话已切换，请重新加载后重试；原对话未覆盖。', 409)
            changed = False
            if draft is not None:
                value.update(draft.model_dump(mode="json", exclude={"conversationId"}))
                changed = True
            if begin is not None:
                if any(message['id'] == begin['id'] for message in value['messages']):
                    raise ProjectError('这条消息已接受，请读取对话，不要重复发送。', 409)
                value['messages'].append(begin)
                value['messages'].append({'id': str(uuid5(UUID(begin['id']), 'assistant')),
                    'role': 'assistant', 'text': '', 'status': 'streaming',
                    **{key: begin[key] for key in ('model', 'runtime') if key in begin}})
                value.update(draft='', draftContent=None, assets=[], skills=[])
                changed = True
            if finish is not None:
                value['messages'] = [finish if message['id'] == finish['id'] else message for message in value['messages']]
                changed = True
            if changed:
                durable_json(file, value)
            return {key: item for key, item in value.items() if key != 'archivedConversations'}

    def list(self, project_id: UUID) -> dict:
        directory = self.path(project_id)
        file = child(child(directory, 'agent-runs'), 'conversation.json')
        with self.projects.local.transaction():
            current = read_json(file) if file.exists() else {'messages': [], **Draft().model_dump()}
            current['conversationId'] = current.get('conversationId') or str(uuid5(project_id, 'assistant'))
            return {'activeId': current['conversationId'], 'items': [
                {'id': item['conversationId'],
                 'title': next((message['text'].strip().split('\n')[0][:48] or '素材对话'
                                for message in item['messages'] if message['role'] == 'user'),
                               (item.get('draft') or '').strip().split('\n')[0][:48] or '新会话'),
                 'messageCount': len(item['messages']),
                 'hasDraft': bool((item.get('draft') or '').strip() or item.get('assets') or item.get('skills'))}
                for item in [current, *current.get('archivedConversations', [])]]}

    def activate(self, project_id: UUID, conversation_id: UUID, session: str, *, create=False) -> dict:
        with self.projects.local.transaction() as db:
            self.projects._recover()
            directory, _, _ = self.projects._load(db, project_id)
            self.projects._lease(db, project_id, directory, session)
            folder = child(directory, 'agent-runs')
            folder.mkdir(exist_ok=True)
            file = child(folder, 'conversation.json')
            current = read_json(file) if file.exists() else {'messages': [], **Draft().model_dump(mode='json')}
            current['conversationId'] = current.get('conversationId') or str(uuid5(project_id, 'assistant'))
            archives = current.pop('archivedConversations', [])
            if current['conversationId'] == str(conversation_id):
                return current
            target = next((item for item in archives if item['conversationId'] == str(conversation_id)), None)
            if target is None:
                if not create:
                    raise ProjectError('对话不存在。', 404)
                if len(archives) >= 99:
                    raise ProjectError('项目对话已达 100 个，暂不能继续新建。', 409)
                target = {'messages': [], **Draft().model_dump(mode='json'),
                          **{key: current[key] for key in ('runtime', 'runtimeModel', 'harnessModels', 'thinking', 'builtinModelThinking', 'modelSelections') if key in current},
                          'conversationId': str(conversation_id)}
            archives = [item for item in archives if item['conversationId'] != str(conversation_id)]
            durable_json(file, {**target, 'archivedConversations': [current, *archives]})
            return target


class ConversationCreate(BaseModel):
    id: UUID


class RunEvents:
    """Events of one accepted turn, kept until the turn ends.

    The run belongs to the project, not to the HTTP request that started it:
    leaving the project closes the stream, and returning attaches a new one that
    replays from the beginning. Only an explicit stop cancels the run.
    """

    def __init__(self):
        self.items: list[dict] = []
        self.changed = asyncio.Event()

    def put(self, event: dict) -> None:
        self.items.append(event)
        self.changed.set()
        self.changed = asyncio.Event()

    async def follow(self):
        index = 0
        while True:
            while index < len(self.items):
                event = self.items[index]
                index += 1
                yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                if event['type'] == 'end':
                    return
            await self.changed.wait()


def agent_routes(projects, generation, configs, provider=None) -> APIRouter:
    router = APIRouter(prefix='/api/creation/projects/{project_id}/assistant')
    conversations = Conversations(projects)
    runtimes = AgentRuntimes(projects, generation, configs, provider)

    @router.get('/traces/{turn_id}/input')
    def trace_input(project_id: UUID, turn_id: UUID):
        file = child(child(conversations.path(project_id), 'agent-runs'), 'conversation.json')
        with projects.local.transaction():
            conversation = read_json(file) if file.is_file() else {}
        for item in [conversation, *conversation.get('archivedConversations', [])]:
            for message in item.get('messages', []):
                if message['role'] == 'user' and message['id'] == str(turn_id):
                    return {'text': message['text']}
        raise ProjectError('该轮用户消息不存在。', 404)

    @router.get('/traces/{turn_id}/{source}')
    def trace(project_id: UUID, turn_id: UUID,
              source: Literal['opencode', 'codex', 'claude', 'jiuwenswarm', 'deepseek', 'model']):
        folder = child(conversations.path(project_id), 'agent-runs')
        file = child(folder, f'{turn_id}.{source}.jsonl')
        if not file.is_file():
            raise ProjectError('轨迹文件不存在。', 404)
        return FileResponse(file, media_type='text/plain', headers={'Cache-Control': 'no-store'})

    active: dict[UUID, tuple[UUID, asyncio.Task, RunEvents]] = {}
    admission: dict[UUID, asyncio.Lock] = {}

    @router.get('')
    async def conversation(project_id: UUID):
        value = await asyncio.to_thread(conversations.update, project_id)
        running = active.get(project_id)
        # A streaming message without an active turn was interrupted (for
        # example by a restart); with one, the client can attach to it.
        if running and not running[1].done():
            value = {**value, 'activeTurnId': str(running[0])}
        return value

    @router.get('/conversations')
    async def list_conversations(project_id: UUID):
        return await asyncio.to_thread(conversations.list, project_id)

    async def change_conversation(project_id: UUID, conversation_id: UUID, session: UUID, *, create=False):
        async with admission.setdefault(project_id, asyncio.Lock()):
            if project_id in active:
                raise ProjectError('请先停止或等待当前回复结束，再切换对话。', 409)
            return await asyncio.to_thread(conversations.activate, project_id, conversation_id, str(session), create=create)

    @router.post('/conversations')
    async def new_conversation(project_id: UUID, value: ConversationCreate, x_opendirector_session: UUID = Header()):
        return await change_conversation(project_id, value.id, x_opendirector_session, create=True)

    @router.post('/conversations/{conversation_id}/activate')
    async def activate_conversation(project_id: UUID, conversation_id: UUID, x_opendirector_session: UUID = Header()):
        return await change_conversation(project_id, conversation_id, x_opendirector_session)

    @router.put('/draft')
    async def draft(project_id: UUID, value: Draft, x_opendirector_session: UUID = Header()):
        return await asyncio.to_thread(conversations.update, project_id, str(x_opendirector_session), draft=value)

    @router.get('/runtimes')
    async def available_runtimes(project_id: UUID, probe: bool = True):
        await asyncio.to_thread(conversations.path, project_id)
        if probe:
            return await configured_runtimes()
        return [{**item, 'status': await runtime_status(item['id'], probe=False)}
                for item in runtime_catalog()]

    @router.get('/runtimes/{runtime}/status')
    async def runtime_detail(project_id: UUID, runtime: Runtime):
        await asyncio.to_thread(conversations.path, project_id)
        item = next(item for item in runtime_catalog() if item['id'] == runtime)
        return {**item, 'status': await runtime_status(runtime)}

    @router.get('/runtimes/{runtime}/models')
    async def runtime_models(project_id: UUID, runtime: Literal['opencode', 'codex', 'claude', 'jiuwenswarm', 'deepseek'], selected: str | None = None):
        await asyncio.to_thread(conversations.path, project_id)
        if runtime == 'deepseek':
            from opendirector.agent.deepseek import model_choices as deepseek_models
            return await deepseek_models(selected)
        return await model_choices(runtime)

    @router.get('/skills')
    async def catalog(project_id: UUID):
        directory = await asyncio.to_thread(conversations.path, project_id)
        found = await skills(projects.local.repository, directory)
        return [{key: item[key] for key in ('name', 'description', 'source')} for item in found]

    @router.post('/turns/{turn_id}/stop')
    async def stop(project_id: UUID, turn_id: UUID, x_opendirector_session: UUID = Header()):
        await asyncio.to_thread(conversations.path, project_id, str(x_opendirector_session))
        running = active.get(project_id)
        if running and running[0] == turn_id:
            with CancelScope(shield=True):
                if not running[1].cancelling():
                    running[1].cancel()
                with suppress(asyncio.CancelledError):
                    await running[1]
        return {'ok': True}

    @router.get('/turns/{turn_id}/events')
    async def turn_events(project_id: UUID, turn_id: UUID):
        await asyncio.to_thread(conversations.path, project_id)
        running = active.get(project_id)
        if not running or running[0] != turn_id:
            raise ProjectError('这轮回复已结束，请重新读取对话。', 404)
        return StreamingResponse(running[2].follow(), media_type='text/event-stream')

    @router.post('/turns')
    async def turn(project_id: UUID, value: Turn, x_opendirector_session: UUID = Header(),
                   accept_language: str | None = Header(default=None, alias='Accept-Language')):
        async with admission.setdefault(project_id, asyncio.Lock()):
            if project_id in active:
                raise ProjectError('当前工程已有回复正在执行。', 409)
            session = str(x_opendirector_session)
            directory = await asyncio.to_thread(conversations.path, project_id, session)
            if not runtime_available(value.runtime):
                raise ProjectError(f'{value.runtime} 未安装，请安装 CLI 并登录。', 503)
            spec = runtime_spec(value.runtime)
            if not spec.supportsSkills and value.skills:
                raise ProjectError(f'{spec.label} 不支持技能，请移除技能后发送。', 409)
            if value.runtime == 'jiuwenswarm' and value.thinking != 'auto':
                raise ProjectError('JiuwenSwarm 当前使用原生默认推理强度。', 409)
            config = await asyncio.to_thread(configs.resolve, str(value.modelId), 'agent') if spec.modelSource == 'settings' else None
            available = await skills(projects.local.repository, directory) if spec.supportsSkills else []
            if set(value.skills) - {item['name'] for item in available}:
                raise ProjectError('技能已失效，请重新选择。', 409)
            snapshot = await asyncio.to_thread(projects.snapshot, project_id)
            assets = {str(asset.id): asset for asset in snapshot.assets}
            for asset_id in value.assets:
                if str(asset_id) not in assets:
                    raise ProjectError('引用素材不属于当前工程。', 409)
            model = config['modelId'] if config else (value.runtimeModel or 'CLI 默认模型')
            model_selections = dict(value.modelSelections)
            model_names = {row['id']: row['modelId'] for row in configs.list()}
            preferences = {kind: {'modelConfigId': model_id, 'modelName': model_names.get(model_id)}
                for kind, model_id in model_selections.items() if model_id}
            events = RunEvents()
            async def produce() -> None:
                text, status, error, accepted = '', 'complete', None, False
                activities = []
                plan = None
                try:
                    user = {'id': str(value.id), 'role': 'user', 'text': value.text, 'content': value.content,
                        'assets': [{'id': str(asset.id), 'name': asset.name, 'type': asset.metadata.mimeType,
                            'size': asset.metadata.size, 'fingerprint': asset.fingerprint}
                            for asset in (assets[str(key)] for key in value.assets)],
                        'skills': value.skills, 'runtime': value.runtime, 'runtimeModel': value.runtimeModel,
                        'modelSelections': dict(value.modelSelections),
                        'model': model, 'status': 'complete'}
                    saved = await asyncio.to_thread(conversations.update, project_id, session, begin=user, conversation_id=value.conversationId)
                    accepted = True
                    events.put({'type': 'start', 'conversation': saved})
                    context = []
                    for message in saved['messages'][:-1]:
                        content = message['text'] or '(empty reply)'
                        references = []
                        for attachment in message.get('assets', []):
                            asset = assets.get(attachment['id'])
                            if asset is not None:
                                references.append({'id': str(asset.id), 'name': attachment['name'],
                                    'kind': asset.kind, 'path': str((directory / asset.path).resolve()),
                                    'metadata': asset.metadata.model_dump(mode='json', exclude_none=True)})
                        if references:
                            content += '\nReferenced project assets: ' + json.dumps(references, ensure_ascii=False)
                            content += '\nThese are asset references, not visual observations. Inspect media with available tools before describing its contents.'
                        context.append({'role': message['role'], 'content': content})
                    context[-1]['content'] += '\nSummarize the delivered result and material limitations. Keep execution IDs, revisions and receipts in tool details unless requested.'
                    if preferences:
                        context[-1]['content'] += '\nUser model preferences for this turn: ' + json.dumps(preferences, ensure_ascii=False)
                        context[-1]['content'] += '\nUse these models for new generation unless the user explicitly requests another. If unavailable or unsuitable, explain and ask before substituting. Keep existing nodes on their configured models unless the user requests a change.'
                    run = RunContext(runtime=value.runtime, project_id=project_id, turn_id=value.id,
                        session=session, directory=directory, config=config, messages=context,
                        selected_skills=value.skills, thinking=value.thinking, model_selections=model_selections,
                        runtime_model=value.runtimeModel, skills=available, conversation_id=UUID(saved['conversationId']))
                    async with aclosing(runtimes.stream(run)) as stream:
                        async for event in stream:
                            if event['type'] == 'delta':
                                text += event['text']
                            if event['type'] == 'plan':
                                plan = event['entries']
                            if event['type'] == 'tool':
                                previous = next((item for item in activities if item['id'] == event['id']), None)
                                activity = {**{key: item for key, item in event.items() if key != 'type'},
                                            'textOffset': previous['textOffset'] if previous else len(text)}
                                if previous is None:
                                    activities.append(activity)
                                else:
                                    activities[activities.index(previous)] = activity
                                event = {'type': 'tool', **activity}
                            events.put(event)
                except asyncio.CancelledError:
                    status = 'stopped'
                    raise
                except Exception as failure:
                    # Persist partial output even when an integration fails unexpectedly.
                    status = 'interrupted'
                    if isinstance(failure, ProjectError):
                        error = localized_error_for_language(
                            accept_language, chinese=str(failure),
                            status=failure.status, code=failure.code)
                    else:
                        error = localized_error_for_language(
                            accept_language,
                            chinese='Agent 执行失败，请检查后端日志。',
                            status=500, code='operation_rejected')
                    logger.exception(f'agent.turn.failed project_id={project_id} turn_id={value.id}')
                finally:
                    if accepted:
                        for activity in activities:
                            if activity['status'] == 'running':
                                activity['status'] = 'interrupted'
                        saved = await asyncio.to_thread(conversations.update, project_id, finish={
                            'id': str(uuid5(value.id, 'assistant')), 'role': 'assistant', 'text': text, 'status': status,
                            'model': model,
                            'runtime': value.runtime, 'thinking': value.thinking, 'tools': activities,
                            **({'plan': plan} if plan is not None else {})}, conversation_id=value.conversationId)
                    else:
                        saved = None
                    events.put({'type': 'end', 'conversation': saved, 'error': error})

            if project_id in active:
                raise ProjectError('当前工程已有回复正在执行。', 409)
            task = asyncio.create_task(produce())
            active[project_id] = (value.id, task, events)
            def release(completed: asyncio.Task) -> None:
                running = active.get(project_id)
                if running and running[0] == value.id and running[1] is completed:
                    active.pop(project_id)
            task.add_done_callback(release)
            # Closing this stream (switching projects, reloading) only detaches
            # the viewer; the run continues and can be attached again.
            return StreamingResponse(events.follow(), media_type='text/event-stream')

    return router
