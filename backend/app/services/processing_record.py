"""Allowlisted processing snapshots created only after a successful output write.

No source text, replacement value, token, key or absolute path is recorded here.
Text operations must come from the actual masker, not from eligible detections.
"""
from collections import Counter
from pathlib import Path

from .media import kind_of


def _output(path):
    """Read actual output stream descriptions, without preserving metadata tags."""
    import av
    with av.open(str(path)) as container:
        return {
            'container': container.format.name,
            'video_codec': container.streams.video[0].codec_context.name if container.streams.video else None,
            'audio_tracks': [
                {'track_index': index, 'codec': stream.codec_context.name,
                 'channels': stream.codec_context.channels,
                 'sample_rate': stream.codec_context.sample_rate}
                for index, stream in enumerate(container.streams.audio)
            ],
        }


def processing_record(fi, findings, policy, output_path, text_operations=None, media_audit=None):
    """Return an audit snapshot; caller persists it with the immutable artifact.

    media_audit is measured by the actual PCM masker, before video AAC encoding.
    Missing measurements are marked unknown instead of inferred from detections.
    """
    from .mask.code import eligible

    kind = kind_of(fi['path'])
    hits = [f for f in eligible(findings, policy, True) if f['file_id'] == fi['id']]
    all_hits = [f for f in findings if f['file_id'] == fi['id']]
    record = {
        'recorded': True, 'version': 1, 'media': kind,
        'filter': {'required_status': 'accepted', 'min_confidence': float(policy.get('min_confidence', 0) or 0),
                   'eligible_findings': len(hits), 'excluded_findings': len(all_hits) - len(hits)},
        'mapping_reversible_operations': 0,
    }
    if kind in ('code', 'text'):
        # Missing historical operations must not be reconstructed from detections.
        operations = None if text_operations is None else [
            {'mode': op['mode'], 'start': int(op['start']), 'end': int(op['end'])}
            for op in text_operations
        ]
        record.update(method='text_replacement', operations=operations,
                      applied_operations=len(operations) if operations is not None else None,
                      modes=dict(Counter(op['mode'] for op in operations)) if operations is not None else None,
                      output={'format': Path(output_path).suffix.lstrip('.') or 'text'})
        if operations is not None and policy.get('reversible'):
            record['mapping_reversible_operations'] = sum(op['mode'] == 'replace' for op in operations)
        return record

    if kind == 'image':
        from PIL import Image
        with Image.open(output_path) as image:
            output = {'format': image.format, 'width': image.width, 'height': image.height}
        record.update(method='opaque_black_regions' if any(f.get('bbox') for f in hits) else 'unchanged_copy',
                      regions=[{'bbox': list(f['bbox'])} for f in hits if f.get('bbox')], output=output)
        return record

    meta = fi.get('detection', {}).get('metadata', {})
    output = _output(output_path)
    voice = policy.get('voice_change') or ''
    voice_config = {'mode': voice or 'none'}
    if voice == 'pitch':
        voice_config['semitones'] = 4
        voice_config['duration_compensated'] = True
    elif voice == 'robot':
        voice_config['carrier_hz'] = 400
    padding = float(policy.get('mute_padding', .15))
    record.update(method='video_regions_and_audio_tracks' if kind == 'video' else 'audio_tracks',
                  output=output, mute_padding_seconds=padding, voice_change=voice_config,
                  audio_tracks=[])
    tracks = meta.get('audio_tracks', [])
    if kind == 'audio':
        tracks = [{'track_index': 0}]
    for track in tracks:
        index = track['track_index']
        audit = ((media_audit or {}).get('audio_tracks', {}).get(index) if kind == 'video' else media_audit)
        record['audio_tracks'].append({
            'track_index': index, 'method': 'mute_and_voice' if voice else 'mute',
            'duration_seconds': audit.get('duration') if audit else None,
            'sample_rate': audit.get('sample_rate') if audit else None,
            'channels': audit.get('channels') if audit else None,
            'interval_basis': ('processed_pcm_before_aac' if kind == 'video' else 'output_pcm') if audit else 'unknown',
            'mute_intervals_seconds': [list(interval) for interval in audit['mute_intervals']] if audit else None,
            'voice_change': dict(voice_config),
        })
    if kind == 'video':
        record['frames_processed'] = (media_audit or {}).get('frames_processed')
        record['masked_frames'] = (media_audit or {}).get('masked_frames')
        record['regions'] = []
        for finding in hits:
            if not finding.get('bbox'):
                continue
            region = {'bbox': list(finding['bbox']), 'method': 'opaque_black'}
            if finding.get('source') == 'manual':
                region.update(start=finding.get('start'), end=finding.get('end'))
            else:
                region.update(frame_index=finding.get('frame_index'), frame_ts=finding.get('frame_ts'))
            record['regions'].append(region)
        counts = (media_audit or {}).get('region_frame_applications', [])
        for index, region in enumerate(record['regions']):
            region['frames_applied'] = counts[index] if index < len(counts) else None
    return record
