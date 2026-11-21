"""Conservative speech review candidates and QR scale localization regressions."""
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.services.detect import audio, image


def mocked_transcript(monkeypatch, text, start=1, end=4):
    segment = {'text': text, 'start': start, 'end': end,
               'words': [{'text': text, 'start': start+.1, 'end': end-.1}]}
    monkeypatch.setattr(audio, 'probe', lambda path: {'duration': 6})
    monkeypatch.setattr(audio, '_transcribe', lambda *args: [segment])
    return segment


@pytest.mark.parametrize('number', ['1380013800', '138001380000', '861380013800'])
def test_uncertain_mobile_candidate_preserves_transcribed_digits_and_whole_segment(monkeypatch, number):
    segment = mocked_transcript(monkeypatch, '测试号码'+number)
    hits, detail = audio.detect_audio_detail(Path('mock.wav'), {'pii_types':['phone']})
    assert len(hits) == 1
    hit = hits[0]
    assert hit['rule_name'] == 'phone_candidate'
    assert hit['requires_manual_verification'] is True
    assert hit['value'] == number
    assert (hit['start'], hit['end'], hit['timing_precision']) == (1, 4, 'segment')
    assert detail['transcript'] == [segment]
    assert '需人工核对' in hit['type_label']


def test_spoken_candidate_keeps_raw_character_provenance(monkeypatch):
    raw = '电话幺三八，零零幺三，八零零'
    mocked_transcript(monkeypatch, raw)
    hits, _ = audio.detect_audio_detail(Path('mock.wav'), {'pii_types':['phone']})
    assert len(hits) == 1 and hits[0]['value'] == raw[2:]
    assert hits[0]['transcript_spans'] == [{'segment_index':0, 'start':2, 'end':len(raw)}]
    assert hits[0]['timing_precision'] == 'segment'


@pytest.mark.parametrize('text', ['订单号码1380013800', '验证码1380013800',
                                  '1380013800', '今天有1234567890人', '电话1234567890'])
def test_unrelated_numbers_do_not_become_phone_candidates(monkeypatch, text):
    mocked_transcript(monkeypatch, text)
    hits, _ = audio.detect_audio_detail(Path('mock.wav'), {'pii_types':['phone']})
    assert not hits


def test_phone_disabled_policy_does_not_add_uncertain_phone(monkeypatch):
    mocked_transcript(monkeypatch, '电话1380013800')
    hits, _ = audio.detect_audio_detail(Path('mock.wav'), {'pii_types':['email']})
    assert not hits


def test_blocked_pii_series_does_not_add_uncertain_phone(monkeypatch):
    mocked_transcript(monkeypatch, '电话1380013800')
    hits, _ = audio.detect_audio_detail(Path('mock.wav'), {'blocked_series':['pii']})
    assert not hits


def test_exact_phone_stays_single_word_level_finding(monkeypatch):
    mocked_transcript(monkeypatch, '电话13800138000')
    hits, _ = audio.detect_audio_detail(Path('mock.wav'), {'pii_types':['phone']})
    assert len(hits) == 1 and hits[0]['rule_name'] == 'phone'
    assert hits[0]['timing_precision'] == 'word'
    assert not hits[0].get('requires_manual_verification')


@pytest.mark.parametrize('gap, expected', [(.3,1), (1.1,0)])
def test_nearby_phone_label_context_is_bounded(monkeypatch, gap, expected):
    monkeypatch.setattr(audio, 'probe', lambda path: {'duration': 6})
    segments = [{'text':'请记录测试号码', 'start':0, 'end':1, 'words':[]},
                {'text':'1380013800', 'start':1+gap, 'end':4, 'words':[]}]
    monkeypatch.setattr(audio, '_transcribe', lambda *args: segments)
    hits, _ = audio.detect_audio_detail(Path('mock.wav'), {'pii_types':['phone']})
    assert len(hits) == expected
    if hits:
        assert hits[0]['start'] == 1+gap and hits[0]['timing_precision'] == 'segment'


def test_qr_scaled_points_map_to_original_pixels_and_stop_after_success():
    calls = []
    class Detector:
        def detectAndDecode(self, img):
            calls.append(img.shape[:2])
            if len(calls) == 1:
                return '', None, None
            return 'synthetic-qr', np.array([[[30,15],[60,15],[60,45],[30,45]]],dtype=np.float32), None
    adapter = SimpleNamespace(QRCodeDetector=Detector, resize=cv2.resize, INTER_CUBIC=cv2.INTER_CUBIC)
    qr = image._qr_region(np.full((80,120,3),255,np.uint8), adapter)
    assert calls == [(80,120), (60,90)]
    assert qr == ('synthetic-qr', [38,18,44,44])


def test_blank_image_has_no_qr_at_any_scale():
    assert image._qr_region(np.full((360,640,3),255,np.uint8), cv2) is None


def test_actual_small_qr_detects_and_region_can_remove_payload():
    qr = cv2.QRCodeEncoder_create().encode('https://example.com/synthetic-scale-test')
    qr = cv2.copyMakeBorder(qr,4,4,4,4,cv2.BORDER_CONSTANT,value=255)
    qr = cv2.resize(qr,(136,136),interpolation=cv2.INTER_NEAREST)
    canvas = np.full((360,640,3),255,np.uint8)
    canvas[170:306,25:161] = cv2.cvtColor(qr,cv2.COLOR_GRAY2BGR)
    value, (x,y,w,h) = image._qr_region(canvas, cv2)
    assert value == 'https://example.com/synthetic-scale-test'
    assert 25 <= x < 161 and 170 <= y < 306 and w > 50 and h > 50
    canvas[y:y+h,x:x+w] = 0
    assert image._qr_region(canvas, cv2) is None
