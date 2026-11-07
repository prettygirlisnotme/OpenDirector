"""Explicit adapter profiles, not model-name guesses or online capability probes."""
from urllib.parse import urlsplit
from typing import Literal
from opendirector.domain.rule_facts import AGENT_IMAGE_BYTES, DEFAULT_VIDEO_BYTES, MAX_REFERENCES
from opendirector.domain.video_capabilities import capability_fields, seedance_20_variant
VideoMode = Literal['first_frame', 'reference', 'continuation']


def _adapter_rules(config):
    url = urlsplit(config['url'])
    host, path = url.hostname, url.path.rstrip('/')
    kind, model = config['kind'], config['modelId']
    rules = {'profile': 'compatible', 'allowedKinds': ['text', 'image'], 'maxImages': 8,
             'maxAudios': 0, 'imageBytes': 8 * 1024 * 1024, 'requestBytes': 48 * 1024 * 1024,
             'videoModes': ['first_frame'] if kind == 'video' else []}
    rules.update(maxVideos=0, videoBytes=DEFAULT_VIDEO_BYTES)
    if kind == 'vision' or config.get('protocol') == 'vllm_omni_segmentation':
        # Shared image segmentation / video tracking service, never a generator.
        rules.update(profile='vllm-omni-segmentation', allowedKinds=[], maxImages=0, videoModes=[])
        return rules
    if config.get('protocol') == 'vllm_omni_depth':
        # Video depth estimation service (Video Depth Anything), never a generator.
        rules.update(profile='vllm-omni-depth', allowedKinds=[], maxImages=0, videoModes=[])
        return rules
    if config.get('protocol') == 'vllm_omni_score':
        # SheetSage2 transcription service: it turns a reference recording into
        # a score for music generation and never generates audio itself.
        rules.update(profile='vllm-omni-score', allowedKinds=[], maxImages=0)
        return rules
    if kind == 'video' and config.get('protocol') == 'minimax_video_v2':
        rules.update(profile='minimax-h3-official', allowedKinds=['text', 'image', 'video', 'audio'],
                     maxImages=9, maxVideos=3, maxAudios=3, imageBytes=30 * 1024 * 1024,
                     requestBytes=64 * 1024 * 1024, videoModes=['first_frame', 'reference'],
                     frameRoles=['first_frame', 'last_frame'], audioReferenceStandalone=True,
                     aspectRatios=['21:9', '16:9', '4:3', '1:1', '3:4', '9:16'])
        return rules
    adapter = config.get('videoAdapter', 'auto')
    name = model.rsplit('/', 1)[-1].lower()
    if adapter == 'auto':
        adapter = ('minimax_h3' if name in {'minimax-h3', 'minimax-h3-ref2va'} else
                   'fasth3' if name in {'fasth3', 'fasth3-omni'} or name.startswith('fasth3-omni-') else 'standard')
    if kind == 'video' and config.get('protocol') == 'vllm_omni' and adapter == 'minimax_h3':
        # Self-hosted vLLM-Omni has no fixed host; the served model name selects it.
        # continuation continues one earlier H3 clip of the same server from its
        # cached latents: the source video is never uploaded, so maxVideos (video
        # references) stays zero.
        rules.update(profile='vllm-omni-h3', allowedKinds=['text', 'image', 'video', 'audio'], maxImages=9, maxAudios=3,
                     imageBytes=30 * 1024 * 1024, requestBytes=64 * 1024 * 1024, videoModes=['first_frame', 'reference', 'continuation'],
                     outputResolutions=['480P', '768P'])
        return rules
    if kind == 'video' and config.get('protocol') == 'vllm_omni' and adapter == 'fasth3':
        rules.update(profile='fasth3-omni', allowedKinds=['text', 'image', 'video', 'audio'],
                     maxImages=9, maxVideos=3, maxAudios=3, imageBytes=30 * 1024 * 1024,
                     requestBytes=160 * 1024 * 1024, videoModes=['reference'], outputResolutions=['480P', '768P'])
        return rules
    if kind == 'video' and config.get('protocol') == 'vllm_omni':
        rules.update(profile='standard-video', maxImages=1, videoModes=['first_frame'])
        return rules
    if kind == 'agent':
        rules.update(allowedKinds=['text', 'image', 'video', 'audio'], maxImages=MAX_REFERENCES, imageBytes=AGENT_IMAGE_BYTES)
        if host == 'api.deepseek.com' and path in {'', '/v1'} and model == 'deepseek-flash':
            rules['profile'] = 'deepseek-flash'
    elif host == 'ark.cn-beijing.volces.com' and path == '/api/plan/v3':
        seedance = seedance_20_variant(model) if kind == 'video' else None
        if kind == 'image' and model in {'doubao-seedream-5.0-pro', 'doubao-seedream-5-0-pro'}:
            rules.update(profile='seedream-5.0-pro', maxImages=10, imageBytes=30 * 1024 * 1024, requestBytes=64 * 1024 * 1024)
        elif seedance:
            # The 2.0 family shares one adapter: mini and fast serve 480P/720P,
            # the full model adds 1080P and 4K. All accept 4–15 seconds.
            # Omni reference takes up to 9 images, 3 videos and 3 audios; the
            # videos are capped at 15 s in total, which reference_inputs enforces.
            lite = seedance != 'full'
            rules.update(profile='seedance-2.0', allowedKinds=['text', 'image', 'video', 'audio'], maxImages=9, maxVideos=3, maxAudios=3, imageBytes=30 * 1024 * 1024,
                         videoBytes=200 * 1024 * 1024,
                         requestBytes=64 * 1024 * 1024, videoModes=['first_frame', 'reference'],
                         outputResolutions=['480P', '720P'] if lite else ['480P', '720P', '1080P', '4K'],
                         referenceImageResolutions=['480P', '720P'] if lite else ['480P', '720P', '4K'])
    if kind == 'image' and model.rsplit('/', 1)[-1].lower() in {'qwen-image-2.1', 'qwen-image-2.1-turbo'}:
        # Self-hosted vLLM-Omni Qwen-Image-2.1: text-to-image and instruction
        # editing in one model, conditioned on up to four reference images.
        rules.update(profile='vllm-omni-qwen-image', allowedKinds=['text', 'image'], maxImages=4,
                     imageBytes=30 * 1024 * 1024, requestBytes=64 * 1024 * 1024)
    if kind == 'image' and model.rsplit('/', 1)[-1].lower() == 'z-image-turbo':
        # Self-hosted vLLM-Omni text-to-image: it draws from the prompt alone, so
        # documents are inlined as text and reference images are not accepted.
        rules.update(profile='vllm-omni-image', allowedKinds=['text'], maxImages=0)
    if kind == 'image' and config.get('protocol') in {None, 'openai'} and name.startswith('gpt-image'):
        # OpenAI gpt-image: documented text-to-image plus reference editing with at
        # most 16 images, each under 25 MB and under 50 MB for the whole request.
        rules.update(profile='openai-gpt-image', allowedKinds=['text', 'image'], maxImages=16,
                     imageBytes=25 * 1024 * 1024, requestBytes=50 * 1024 * 1024)
    if kind == 'audio':
        # vLLM-Omni music models (MiniMax Music 3, YuE2) share /audio/speech with
        # TTS but read a style caption and lyrics, so the protocol names the role.
        music = config.get('protocol') == 'vllm_omni_music'
        rules.update(profile='vllm-omni-music' if music else 'tts', allowedKinds=[], maxImages=0)
        if music and name.startswith('yue2'):
            # YuE2 can follow one reference recording, transcribed into a score first.
            rules.update(allowedKinds=['audio'], maxAudios=1)
    return rules


def input_rules(config):
    rules = _adapter_rules(config)
    if config['kind'] == 'video':
        rules['generateAudioOptions'] = [True, False] if config.get('protocol') == 'volcengine' else [True]
    if config['kind'] == 'image' and rules['profile'] != 'vllm-omni-segmentation':
        from .image_output import image_outputs
        rules['imageOutputs'] = image_outputs(config, rules['profile'])
    return capability_fields(config, rules) if config['kind'] == 'video' else rules
