from types import SimpleNamespace

import numpy as np
import pytest

from app.services.detect.audio import _recheck_numbers


@pytest.mark.parametrize('alternatives,accepted', [
    (['13800138000', '一三八零零一三八零零零'], True),
    (['13800138000', '13800138001'], False),
    (['13800138000', ''], False),
    (['1380013800', '1380013800'], False),
    (['13912345678', '13912345678'], False),
    (['13800138000 13912345678', '13800138000'], False),
])
def test_recheck_requires_agreement_and_single_edit(monkeypatch, alternatives, accepted):
    monkeypatch.setattr('faster_whisper.audio.decode_audio', lambda *a, **kw: np.zeros(160000))
    monkeypatch.setattr('app.services.detect.audio._pad_recheck', lambda a: a)
    class Model:
        def transcribe(self, audio, **kw):
            text = alternatives.pop(0) if alternatives else ''
            return iter([SimpleNamespace(text=text, end=4)]), None
    segment = {'text':'电话1380013800','start':0,'end':5,
               'words':[{'text':'1380013800','start':1.5,'end':5}]}
    result = _recheck_numbers(Model(), 'fake.wav', [segment])[0]
    assert result['recognition_recheck']['accepted'] == accepted
    if accepted:
        assert result['raw_text'] == '电话1380013800'
        assert result['text'] == '电话13800138000'
        assert result['words'] == []  # no false precise alignment
    else:
        assert result['text'] == '电话1380013800'


@pytest.mark.parametrize('text', ['电话13912345678', '订单号码1380013800', '没有号码', '电话1380013800'])
def test_no_retry_for_complete_unrelated_or_unaligned(monkeypatch, text):
    class Model:
        def transcribe(self, *a, **kw):
            pytest.fail('must not decode')
    segment = {'text':text,'start':0,'end':5,'words':[]}
    assert _recheck_numbers(Model(), 'fake.wav', [segment]) == [segment]


@pytest.mark.parametrize('texts,accepted', [
    (['', '13911112222', '13911112222', '13911112222'], True),
    (['', '13911112222', '', '13911112222'], True),
    (['', '13911112222', '13911112223', '13911112222'], False),
    (['', '', '13911112222', '13911112222'], True),
])
def test_padded_views_require_agreement_and_no_competitor(monkeypatch, texts, accepted):
    monkeypatch.setattr('faster_whisper.audio.decode_audio', lambda *a, **kw: np.zeros(160000))
    monkeypatch.setattr('app.services.detect.audio._pad_recheck', lambda a: a)
    class Model:
        def transcribe(self, *a, **kw):
            return iter([SimpleNamespace(text=texts.pop(0), end=3)]), None
    segment = {'text':'电话1391111222','start':0,'end':5,
        'words':[{'text':'1391111222','start':1,'end':4}]}
    result = _recheck_numbers(Model(), 'fake.wav', [segment])[0]
    assert result['recognition_recheck']['accepted'] == accepted
    if accepted:
        assert result['raw_text'] == '电话1391111222'
        assert result['text'] == '电话13911112222'
        assert result['words'] == []
        assert result['end'] >= 4.6  # slowed-back retry time maps to original units


@pytest.mark.parametrize('count,number', [('十','1380013800'),('十二','138001380000'),('10','1380013800')])
def test_explicit_non_eleven_length_is_never_promoted(monkeypatch, count, number):
    class Model:
        def transcribe(self, *a, **kw):
            pytest.fail('explicit non-mobile length must not be rewritten')
    segment = {'text':f'这串号码只有{count}位：{number}','start':0,'end':5,
        'words':[{'text':number,'start':1,'end':4}]}
    result = _recheck_numbers(Model(), 'fake.wav', [segment])[0]
    assert result == segment
    assert 'raw_text' not in result


def test_following_count_declaration_is_respected():
    class Model:
        def transcribe(self, *args, **kwargs):
            pytest.fail('following explicit count must prevent rewriting')
    segments = [{'text':'电话1380013800','start':0,'end':4,
        'words':[{'text':'1380013800','start':1,'end':4}]},
        {'text':'这串号码只有十位','start':4.1,'end':6,'words':[]}]
    assert _recheck_numbers(Model(), 'fake.wav', segments) == segments
    assert 'raw_text' not in segments[0]


def test_retry_timestamps_are_clamped_to_actual_crop(monkeypatch):
    monkeypatch.setattr('faster_whisper.audio.decode_audio', lambda *a, **kw: np.zeros(160000))
    class Model:
        def transcribe(self, *args, **kwargs):
            return iter([SimpleNamespace(text='13800138000', end=999)]), None
    segment = {'text':'电话1380013800','start':0,'end':4,
        'words':[{'text':'1380013800','start':1,'end':4}]}
    result = _recheck_numbers(Model(), 'fake.wav', [segment])[0]
    assert result['recognition_recheck']['accepted']
    assert result['end'] == pytest.approx(4.6)


def test_rechecked_phone_remains_explicit_review_candidate(monkeypatch):
    from app.services.detect import audio
    monkeypatch.setattr(audio, 'probe', lambda *a: {'duration':6})
    segment = {'text':'电话13800138000','start':0,'end':5,'words':[],
        'raw_text':'电话1380013800','recognition_recheck':{'accepted':True}}
    monkeypatch.setattr(audio, '_transcribe', lambda *a: [segment])
    findings, _ = audio.detect_audio_detail('fake.wav', {})
    assert len(findings) == 1
    assert findings[0]['confidence'] <= .65
    assert findings[0]['requires_manual_verification'] is True
    assert findings[0]['timing_precision'] == 'segment'


