"""Offline ASR, source-character alignment and explicit per-file coverage."""
import functools
import os
import re
from pathlib import Path

from ..media_runtime import MediaError, MediaCancelled, check, progress, probe
from .text import scan_pii


@functools.lru_cache(maxsize=2)
def _load_model(local_path):
    from faster_whisper import WhisperModel
    return WhisperModel(local_path, device='cpu', compute_type='int8', local_files_only=True)


def _model():
    path = Path(os.environ.get('OMNIMASK_ASR_MODEL', '')).expanduser()
    if not os.environ.get('OMNIMASK_ASR_MODEL') or not (path / 'model.bin').is_file():
        raise MediaError('未配置本地 ASR 模型：请设置 OMNIMASK_ASR_MODEL；不会自动下载')
    try:
        return _load_model(str(path.resolve()))
    except Exception as exc:
        raise MediaError('本地 ASR 模型加载失败，请检查模型文件和 faster-whisper 依赖') from exc


_DIGITS = dict(zip('零〇一幺二两三四五六七八九', '0011223456789'))


def normalize_spoken(text):
    """Normalize only digit runs, keeping every output character's original index."""
    chars = list(text)
    for match in re.finditer(r'[零〇一幺二两三四五六七八九\d][零〇一幺二两三四五六七八九\d\s，,、\-－]*', text):
        indices = [i for i in range(match.start(), match.end()) if text[i].isdigit() or text[i] in _DIGITS]
        if len(indices) >= 7:  # ordinary prose / small quantities are not rewritten
            for i in indices:
                chars[i] = _DIGITS.get(text[i], text[i])
            for i in range(match.start(), match.end()):
                if i not in indices:
                    chars[i] = ''
    mapping = [i for i, c in enumerate(chars) if c]
    return ''.join(chars), mapping


def _transcribe(path, context=None):
    model = _model()
    try:
        segments, info = model.transcribe(str(path), word_timestamps=True, vad_filter=True,
                                          language=None, condition_on_previous_text=False)
        out = []
        for seg in segments:
            check(context)
            words = [{'start': float(w.start), 'end': float(w.end), 'text': w.word}
                     for w in (seg.words or [])]
            out.append({'start': float(seg.start), 'end': float(seg.end), 'text': seg.text, 'words': words})
            progress(context, '语音转写', seg.end, getattr(info, 'duration', seg.end))
        return _recheck_numbers(model, path, out, context)
    except (MediaError, MediaCancelled):
        raise
    except Exception as exc:
        raise MediaError('语音转写失败，未完成敏感信息检查') from exc


def _aligned_words(segment):
    text = segment['text']
    cursor = 0
    out = []
    for word in segment.get('words', []):
        token = word['text']
        at = text.find(token, cursor)
        if at < 0:
            token = token.strip()
            at = text.find(token, cursor)
        if at >= 0 and token:
            out.append((at, at + len(token), word['start'], word['end']))
            cursor = at + len(token)
    return out


def _digit_continuation(left, right):
    """Only join a short, unfinished mobile-number run across a brief pause.

    Full numbers, sentence boundaries, labels at the right edge and runs longer
    than a mobile number remain separate. Ambiguous spoken numbers still need
    human review; this is not general semantic coreference.
    """
    alphabet = '零〇一幺二两三四五六七八九0-9'
    tail = re.search(rf'([{alphabet}][{alphabet}\s，,、\-－]*)$', left)
    head = re.match(rf'^[ \t，,、\-－]*([{alphabet}][{alphabet}\s，,、\-－]*)', right)
    if not tail or not head:
        return False
    digits = lambda text: ''.join(_DIGITS.get(c, c) for c in text if c.isdigit() or c in _DIGITS)
    before, after = digits(tail.group(1)), digits(head.group(1))
    size = 13 if before.startswith('86') else 11
    prefix = before[2:] if size == 13 else before
    return (len(before) < size and len(before + after) <= size
            and bool(re.match(r'^1(?:[3-9]\d*)?$', prefix)))


def _transcript_groups(segments):
    """Yield text and exact original (segment, character) provenance per char."""
    text, provenance, members = '', [], []
    previous = None
    for index, segment in enumerate(segments):
        join = (previous is not None and 0 <= segment['start'] - previous['end'] <= 1.0
                and _digit_continuation(text, segment['text']))
        if text and not join:
            yield text, provenance, members
            text, provenance, members = '', [], []
        if text:
            text += ' '
            provenance.append(None)  # synthetic separation has no claimed speech time
        members.append((index, len(text)))
        text += segment['text']
        provenance.extend((index, i) for i in range(len(segment['text'])))
        previous = segment
    if text:
        yield text, provenance, members


_PHONE_CONTEXT = re.compile(r'电话|電話|手机|手機|号码|號碼|(?i:telephone|mobile|phone)')
_NON_PHONE_CONTEXT = re.compile(r'订单|訂單|流水|运单|運單|发票|發票|账号|帳號|帐号|学号|學號|工号|工號|验证码|驗證碼|序列|合同|条码|條碼')


def _pad_recheck(samples):
    """Fixed silence context, with no alteration to spoken digit durations."""
    import numpy as np
    return np.pad(samples, (8000, 8000))  # 500 ms at 16 kHz at either edge


def _declared_other_length(text):
    # An explicit count is evidence against inventing an eleven-digit mobile.
    counts = re.findall(r'([零〇一二两三四五六七八九十\d]+)\s*位', text)
    return any(count not in ('11', '十一') for count in counts)


def _recheck_numbers(model, path, segments, context=None):
    """Bounded local re-decoding; two agreeing crops, never guessed digits.

    The original transcript remains in raw_text. Crop boundaries are derived
    from the first pass words. A changed transcript has no claimed word timing.
    """
    from faster_whisper.audio import decode_audio
    audio = None
    attempts = 0
    for index, segment in enumerate(segments):
        normalized, mapping = normalize_spoken(segment['text'])
        previous = segments[index-1] if index else None
        nearby = previous['text'] if previous and 0 <= segment['start']-previous['end'] <= 1 else ''
        following = segments[index+1] if index+1 < len(segments) else None
        after = following['text'] if following and 0 <= following['start']-segment['end'] <= 1 else ''
        hits = _uncertain_phone_hits(normalized, {}, nearby)
        if not hits or attempts >= 3 or _declared_other_length(nearby + segment['text'] + after):
            continue
        # Process a single suspect run per segment; multiple numbers need review.
        if len(hits) != 1:
            continue
        hit = hits[0]
        s, e = mapping[hit['index_start']], mapping[hit['index_end']-1]+1
        words = [w for w in _aligned_words(segment) if w[0] < e and w[1] > s]
        if not words:
            continue
        begin, end = min(w[2] for w in words), max(w[3] for w in words)
        if end-begin > 8:
            continue
        check(context)
        if audio is None:
            audio = decode_audio(str(path), sampling_rate=16000)
        attempts += 1
        evidence = []
        for padding in (.3, .4):
            check(context)
            left, right = max(0, begin-padding), min(len(audio)/16000, end+.6)
            retry, _ = model.transcribe(audio[int(left*16000):int(right*16000)],
                word_timestamps=False, vad_filter=False, language=None,
                temperature=0, condition_on_previous_text=False)
            retry = list(retry)
            text = ''.join(item.text for item in retry)
            digits, _ = normalize_spoken(text)
            numbers = re.findall(r'(?<!\d)1[3-9]\d{9}(?!\d)', digits)
            evidence.append({'text': text, 'numbers': numbers, 'crop_start': left,
                'crop_end': right, 'speed': 1.0,
                'speech_end': max((item.end+left for item in retry), default=end)})
        record = {'method': 'two_crop_consensus', 'alternatives': evidence, 'accepted': False}
        segment['recognition_recheck'] = record
        if len(evidence[0]['numbers']) != 1 or evidence[0]['numbers'] != evidence[1]['numbers']:
            # One fixed silence-context view; do not search decoding parameters.
            valid = [x['numbers'][0] for x in evidence if len(x['numbers']) == 1]
            if len(set(valid)) > 1 or any(len(x['numbers']) > 1 for x in evidence):
                continue
            for view in list(evidence):
                check(context)
                left, right = view['crop_start'], view['crop_end']
                samples = _pad_recheck(audio[int(left*16000):int(right*16000)])
                retry, _ = model.transcribe(samples, word_timestamps=False,
                    vad_filter=False, language=None, temperature=0, condition_on_previous_text=False)
                retry = list(retry)
                text = ''.join(item.text for item in retry)
                digits, _ = normalize_spoken(text)
                numbers = re.findall(r'(?<!\d)1[3-9]\d{9}(?!\d)', digits)
                evidence.append({'text':text,'numbers':numbers,'crop_start':left,
                    'crop_end':right,'speed':1.0,'silence_padding':.5,
                    'speech_end':max((max(0,item.end-.5)+left for item in retry),default=end)})
            record['method'] = 'silence_context_consensus'
            # At least two distinct waveform views, and no competing complete
            # number. These correlated same-model views still require review.
            candidates = [x['numbers'][0] for x in evidence if len(x['numbers']) == 1]
            if len(candidates) < 2 or len(set(candidates)) != 1:
                continue
            if any(len(x['numbers']) > 1 for x in evidence):
                continue
        number = next(x['numbers'][0] for x in evidence if len(x['numbers']) == 1)
        original = hit['value']
        # One insertion/deletion only; the acoustic results must agree independently.
        short, long = sorted((original, number), key=len)
        if len(long)-len(short) != 1 or not any(long[:i]+long[i+1:] == short for i in range(len(long))):
            continue
        record.update(accepted=True, original_value=original, value=number,
            requires_manual_verification=True,
            supporting_views=sum(x['numbers'] == [number] for x in evidence),
            supporting_boundaries=len({(x['crop_start'],x['crop_end']) for x in evidence if x['numbers'] == [number]}))
        segment['raw_text'] = segment['text']
        segment['text'] = segment['text'][:s]+number+segment['text'][e:]
        support = [min(x['speech_end'], x['crop_end']) for x in evidence if x['numbers'] == [number]]
        segment['end'] = min(len(audio)/16000, max(segment['end'], *support))
        segment['words'] = []  # old character/time alignment no longer applies
    return segments


def _uncertain_phone_hits(text, policy, context_text=''):
    """Flag plausible ASR digit loss/addition without inventing a complete phone.

    Only a mobile-like 10/12-digit run with nearby telephone context is flagged.
    These are review candidates, not valid phone entities; callers must use
    segment timing even if Whisper provided word timestamps for the transcript.
    """
    enabled = policy.get('pii_types') or []
    if 'pii' in (policy.get('blocked_series') or []) or (enabled and 'phone' not in enabled):
        return []
    hits = []
    for match in re.finditer(r'(?<!\d)(?:\+?86)?1[3-9]\d{8,10}(?!\d)', text):
        value = match.group(0)
        digits = value.lstrip('+')
        mobile = digits[2:] if digits.startswith('86') else digits
        if len(mobile) not in (10, 12):
            continue
        nearby = (context_text[-24:] + text[max(0, match.start()-24):match.end()+12])
        if not _PHONE_CONTEXT.search(nearby) or _NON_PHONE_CONTEXT.search(nearby):
            continue
        hits.append({'rule_name': 'phone_candidate', 'series': 'pii',
                     'is_mechanical': 'machine', 'value': value, 'match': value,
                     'index_start': match.start(), 'index_end': match.end(),
                     'confidence': .65, 'risk': 'medium',
                     'type_label': '疑似号码（转写位数异常，需人工核对）',
                     'requires_manual_verification': True})
    return hits


def detect_audio_detail(path, policy, context=None):
    metadata = probe(path)
    segments = _transcribe(path, context)
    findings = []
    for text, provenance, members in _transcript_groups(segments):
        check(context)
        normalized, mapping = normalize_spoken(text)
        aligned = []
        for index, offset in members:
            aligned.extend((a+offset, b+offset, start, end)
                           for a,b,start,end in _aligned_words(segments[index]))
        previous_context = ''
        first_index = members[0][0]
        if first_index and 0 <= segments[first_index]['start'] - segments[first_index-1]['end'] <= 1.0:
            previous_context = segments[first_index-1]['text']
        hits = scan_pii(normalized, policy) + _uncertain_phone_hits(normalized, policy, previous_context)
        for hit in hits:
            begin, end = hit['index_start'], hit['index_end']
            s, e = mapping[begin], mapping[end - 1] + 1
            covering = [w for w in aligned if w[0] < e and w[1] > s]
            complete = covering and all(provenance[i] is None or text[i].isspace()
                                        or any(w[0] <= i < w[1] for w in covering)
                                        for i in range(s, e))
            if hit.get('requires_manual_verification'):
                complete = False  # ASR may have omitted speech; do not claim precise timing.
            rechecked = any(segments[i].get('recognition_recheck', {}).get('accepted')
                for i, offset in members if offset < e and offset+len(segments[i]['text']) > s)
            if rechecked:
                hit = dict(hit, confidence=min(hit['confidence'], .65),
                    requires_manual_verification=True,
                    type_label=hit['type_label']+'（片段复核，需人工核对）')
                complete = False
            spans = []
            for index, offset in members:
                a, b = max(s, offset), min(e, offset+len(segments[index]['text']))
                if a < b:
                    spans.append({'segment_index':index,'start':a-offset,'end':b-offset})
            if not spans:
                continue
            first, last = spans[0], spans[-1]
            f = dict(hit)
            f.update(start=min(w[2] for w in covering) if complete else segments[first['segment_index']]['start'],
                     end=max(w[3] for w in covering) if complete else segments[last['segment_index']]['end'],
                     media='audio', timing_precision='word' if complete else 'segment', track_index=0,
                     transcript_start=first['start'], transcript_end=last['end'], transcript_spans=spans,
                     source='automatic', value=text[s:e], match=text[s:e],
                     type_label='语音' + hit['type_label'])
            for key in ('line_start','line_end','col_start','col_end','index_start','index_end'):
                f[key] = 0
            findings.append(f)
    return findings, {'state': 'complete', 'warnings': [], 'metadata': metadata, 'transcript': segments}


def detect_audio(path, policy, context=None):
    hits, detail = detect_audio_detail(path, policy, context)
    return hits, detail['warnings']


def detect_audio_files(files, policy, context=None):
    findings, warnings, states = [], [], {}
    scanned = skipped = 0
    for fi in files:
        check(context)
        path = Path(fi['abs'])
        try:
            hits, detail = detect_audio_detail(path, policy, context)
            scanned += 1
            for h in hits:
                h.update(file_id=fi['id'], file_path=fi['path'])
            findings.extend(hits)
            states[fi['id']] = detail
        except MediaCancelled:
            raise
        except Exception as exc:
            msg = str(exc) if isinstance(exc, MediaError) else '音频检查失败'
            warnings.append(fi['path'] + '：' + msg)
            states[fi['id']] = {'state':'failed','warnings':[msg],'metadata':{},'transcript':[]}
    return dict(findings=findings, scanned=scanned, skipped=skipped, warnings=warnings, files=states)
