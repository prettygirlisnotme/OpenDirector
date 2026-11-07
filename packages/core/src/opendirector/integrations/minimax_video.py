"""MiniMax Video V2 jobs using fixed local assets and explicit frame roles."""
import asyncio
import json
from urllib.parse import quote, urlsplit

import httpx

from opendirector.domain.input_rules import input_rules
from opendirector.domain.rule_facts import FRAME_IMAGE_LIMIT, REFERENCE_MEDIA_SECONDS
from opendirector.domain.minimax_models import MINIMAX_MODELS
from opendirector.domain.projects import ProjectError
from opendirector.integrations.generation_errors import protocol_failure, provider_failure, transport_failure
from opendirector.integrations.http_settings import MODEL_HTTP_HOOKS, auth_headers, trust_environment
from opendirector.integrations.reference_inputs import data_uri, image_type, references, request_size, reference_text
from opendirector.integrations.volcengine import bounded
from opendirector.media.probe import probe, require_ffprobe

class MinimaxVideoGeneration:
    def __init__(self, store, transport=None, poll_interval=3):
        self.store, self.transport, self.poll_interval = store, transport, poll_interval

    def validate(self, kind, config):
        if kind != 'video' or config['modelId'] not in MINIMAX_MODELS:
            raise ProjectError('MiniMax Video V2 不支持所选视频模型。')
        require_ffprobe()

    def prepare_inputs(self, project_id, task, config, *, complete=True):
        self.validate(task['kind'], config)
        rules = input_rules(config)
        mode = task.get('videoMode', 'first_frame')
        if mode not in rules['videoModes']:
            raise ProjectError('当前 MiniMax 官方接口不支持该生成模式。')
        prompt = task['prompt']
        content, manifest = [], []
        media = {str(meta['id']): (blob, meta, kind) for blob, meta, kind in references(
            self.store, project_id, task['referenceIds'])}
        roles = task.get('referenceRoles') or [{'assetId': key, 'role': 'reference'} for key in media]
        counts = {'image': 0, 'video': 0, 'audio': 0}
        durations = {'video': 0, 'audio': 0}
        frames = set()
        for reference in roles:
            blob, meta, kind = media[reference['assetId']]
            role = reference['role']
            if kind == 'text':
                prompt += '\n' + reference_text(blob)
                manifest.append({'assetId': str(meta['id']), 'fingerprint': meta['fingerprint'], 'kind': kind, 'transmission': 'content', 'role': 'text'})
                continue
            if kind not in counts:
                raise ProjectError('MiniMax-H3 仅支持图片、视频、音频和文本参考。')
            if role == 'continuation' or mode == 'reference' and role in {'first_frame', 'last_frame'}:
                raise ProjectError('首尾帧与多模态参考不能混用。')
            counts[kind] += 1
            mime = meta['type']
            if kind == 'image':
                mime = image_type(blob)
                if mime not in {'image/png', 'image/jpeg', 'image/webp'} or len(blob) > rules['imageBytes']:
                    raise ProjectError('MiniMax-H3 图片需为不超过 30 MB 的 PNG、JPEG 或 WEBP。')
            elif kind == 'video':
                if mime not in {'video/mp4', 'video/quicktime'} or len(blob) > rules['videoBytes']:
                    raise ProjectError('MiniMax-H3 视频需为不超过 50 MB 的 MP4 或 MOV。')
                facts = probe(blob, '.mp4' if mime == 'video/mp4' else '.mov', 'video', include_codecs=True)
                if facts['codec'] not in {'h264', 'hevc'} or any(codec not in {'aac', 'mp3'} for codec in facts['audioCodecs']):
                    raise ProjectError('MiniMax-H3 参考视频需使用 H.264/H.265，音轨需使用 AAC/MP3。')
                fps = facts['frameRate']
                if fps is None or not 23.976 <= fps <= 60:
                    raise ProjectError('MiniMax-H3 视频参考帧率须为 23.976—60 FPS。')
            elif mime not in {'audio/wav', 'audio/x-wav', 'audio/mpeg'} or len(blob) > 15 * 1024 * 1024:
                raise ProjectError('MiniMax-H3 音频需为不超过 15 MB 的 WAV 或 MP3。')
            if kind in {'image', 'video'}:
                width, height = meta.get('width'), meta.get('height')
                if width is None or height is None or not 256 <= width <= 5760 or not 256 <= height <= 5760 or not .4 <= width / height <= 2.5:
                    raise ProjectError('MiniMax-H3 参考画面边长须为 256—5760 像素，宽高比须为 0.4—2.5。')
            if kind in durations:
                duration = meta.get('durationMs')
                if duration is None or not 2000 <= duration <= REFERENCE_MEDIA_SECONDS * 1000:
                    raise ProjectError(f'MiniMax-H3 视频和音频参考须为 2—{REFERENCE_MEDIA_SECONDS} 秒。')
                durations[kind] += duration
            if mode == 'first_frame':
                if kind != 'image':
                    raise ProjectError('首尾帧模式只能使用图片和文本参考。')
                role = role if role in {'first_frame', 'last_frame'} else 'first_frame'
                if role in frames:
                    raise ProjectError('每个首尾帧槽位只能使用一张图片，请明确指定槽位。')
                frames.add(role)
            else:
                role = f'reference_{kind}'
            wire_kind = f'{kind}_url'
            content.append({'type': wire_kind, wire_kind: {'url': data_uri(blob, 'audio/wav' if mime == 'audio/x-wav' else mime)}, 'role': role})
            manifest.append({'assetId': str(meta['id']), 'fingerprint': meta['fingerprint'], 'kind': kind, 'transmission': 'content', 'role': role})
        if counts['image'] > (FRAME_IMAGE_LIMIT if mode == 'first_frame' else rules['maxImages']) or counts['video'] > rules['maxVideos'] or counts['audio'] > rules['maxAudios'] or any(value > REFERENCE_MEDIA_SECONDS * 1000 for value in durations.values()):
            raise ProjectError('MiniMax-H3 参考数量或总时长超过限制。')
        if complete and not prompt.strip():
            raise ProjectError('MiniMax-H3 需要非空提示词。')
        content.insert(0, {'type': 'text', 'text': prompt})
        request_size({'content': content}, rules['requestBytes'] - 4096)
        return {'profile': rules['profile'], 'references': manifest, 'content': content}

    def payload(self, config, task, prepared):
        parameters = task['parameters']
        duration = parameters.get('duration', 5)
        resolution = parameters.get('resolution', '768P').upper()
        ratio = parameters.get('ratio', '16:9')
        has_frames = any(item.get('role') in {'first_frame', 'last_frame'} for item in prepared['content'])
        if has_frames:
            ratio = 'adaptive'
        resolutions, minimum = MINIMAX_MODELS[config['modelId']]
        if isinstance(duration, bool) or duration not in range(minimum, 16) or resolution not in resolutions or ratio not in set(input_rules(config)['aspectRatios']) | {'adaptive'}:
            raise ProjectError('MiniMax-H3 输出规格无效。')
        if len(prepared['content']) == 1 and ratio == 'adaptive':
            raise ProjectError('文生视频需要明确的画幅比例。')
        payload = {'model': config['modelId'], 'content': prepared['content'], 'duration': int(duration), 'resolution': resolution, 'ratio': ratio}
        request_size(payload, input_rules(config)['requestBytes'])
        return payload

    async def generate(self, config, task, submitted, *, result_ready=None):
        base = config['url'].rstrip('/')
        stage = 'submit'
        secrets = (config.get('apiKey', ''),)
        try:
            async with httpx.AsyncClient(event_hooks=MODEL_HTTP_HOOKS, transport=self.transport,
                    trust_env=trust_environment(base, config=config), timeout=httpx.Timeout(240, connect=15), follow_redirects=False) as client:
                external_id = task.get('externalTaskId')
                if not external_id:
                    prepared = await asyncio.to_thread(self.prepare_inputs, task['projectId'], task, config)
                    payload = self.payload(config, task, prepared)
                    async with client.stream('POST', base + '/video_generation', headers=auth_headers(config), json=payload) as response:
                        value = json.loads(await bounded(response, stage=stage, secrets=secrets, limit=1024 * 1024))
                    external_id = value['task_id']
                    if not isinstance(external_id, str) or not external_id or len(external_id) > 200:
                        raise protocol_failure(stage)
                    await submitted(external_id)
                stage = 'poll'
                while True:
                    async with client.stream('GET', base + '/query/video_generation/' + quote(external_id, safe=''), headers=auth_headers(config)) as response:
                        value = json.loads(await bounded(response, stage=stage, secrets=secrets, limit=1024 * 1024))
                    remote = value['task']
                    if remote['id'] != external_id:
                        raise protocol_failure(stage)
                    if remote['status'] == 'succeeded':
                        stage = 'download'
                        url = remote['content']['url']
                        if result_ready:
                            await result_ready('mp4', url)
                        return 'mp4', await self.download(client, url)
                    if remote['status'] in {'failed', 'cancelled'}:
                        raise provider_failure(remote, stage=stage, secrets=secrets, expected_task_id=external_id, http_status=200)
                    if remote['status'] not in {'queued', 'running'}:
                        raise protocol_failure(stage)
                    await asyncio.sleep(self.poll_interval)
        except httpx.TimeoutException:
            raise transport_failure(stage, timeout=True) from None
        except httpx.HTTPError:
            raise transport_failure(stage) from None
        except (ValueError, TypeError, KeyError, IndexError):
            raise protocol_failure(stage) from None

    async def download(self, client, url):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise protocol_failure('download')
        # CDN requests never receive the provider's Authorization header.
        async with client.stream('GET', url) as response:
            return await bounded(response, stage='download')

    async def resume_result(self, config, suffix, url):
        try:
            async with httpx.AsyncClient(event_hooks=MODEL_HTTP_HOOKS, transport=self.transport,
                    trust_env=trust_environment(url, config=config), timeout=httpx.Timeout(240, connect=15), follow_redirects=False) as client:
                return suffix, await self.download(client, url)
        except httpx.TimeoutException:
            raise transport_failure('download', timeout=True) from None
        except httpx.HTTPError:
            raise transport_failure('download') from None

    async def cancel(self, config, external_id):
        raise ProjectError('MiniMax 官方取消接口尚未接通；远端任务可能继续执行并计费。', 409)
