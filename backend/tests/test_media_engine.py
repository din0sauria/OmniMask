"""Synthetic engine checks; fake ASR tests are explicitly not real recognition."""
import io
import os
import shutil
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from app.services.detect import audio, video
from app.services.mask import media
from app.services.media_runtime import MediaError, MediaCancelled, probe, run_ffmpeg


def accepted(**kw):
    return {'status':'accepted', 'confidence':1, **kw}


def tools():
    if not (os.environ.get('OMNIMASK_FFMPEG') or shutil.which('ffmpeg')):
        pytest.skip('real FFmpeg not installed')


def test_spoken_digits_mapping_and_non_number_prose():
    raw='电话：幺三八，零零幺三，八零零零。今天三个人。'
    normalized, mapping=audio.normalize_spoken(raw)
    assert '13800138000' in normalized
    start=normalized.index('13800138000')
    assert raw[mapping[start]:mapping[start+10]+1]=='幺三八，零零幺三，八零零零'
    assert '三个人' in normalized


def test_asr_missing_is_explicit_and_never_downloads(monkeypatch,tmp_path):
    monkeypatch.delenv('OMNIMASK_ASR_MODEL',raising=False)
    with pytest.raises(MediaError,match='不会自动下载'):
        audio._model()


def test_mock_asr_word_time_alignment_and_segment_fallback(monkeypatch):
    """Deterministic mapping test, not evidence of ASR recognition quality."""
    monkeypatch.setattr(audio,'probe',lambda p:{'duration':4})
    segment={'start':0,'end':4,'text':'电话13800138000',
             'words':[{'text':'电话','start':0,'end':.4}, {'text':'13800138000','start':1,'end':2}]}
    monkeypatch.setattr(audio,'_transcribe',lambda *a:[segment])
    hits,detail=audio.detect_audio_detail(Path('mock.wav'),{'pii_types':['phone']})
    assert len(hits)==1
    assert (hits[0]['start'],hits[0]['end'],hits[0]['timing_precision'])==(1,2,'word')
    assert detail['transcript']==[segment]
    segment['words']=[]
    hits,_=audio.detect_audio_detail(Path('mock.wav'),{'pii_types':['phone']})
    assert (hits[0]['start'],hits[0]['end'],hits[0]['timing_precision'])==(0,4,'segment')


def test_mute_clip_merge_all_channels_and_rejected(tmp_path):
    sr=16000
    data=np.ones((sr*2,2),dtype=np.float32)*.2
    src=tmp_path/'stereo.wav';sf.write(src,data,sr)
    hits=[accepted(start=-.1,end=.2),accepted(start=.25,end=.5),
          accepted(start=1.8,end=3),{'status':'rejected','start':1,'end':1.2}]
    out,rate=sf.read(io.BytesIO(media.mask_audio_bytes(src,hits,{})))
    assert rate==sr and out.shape==data.shape
    assert np.max(abs(out[:int(.65*sr)]))==0
    assert np.max(abs(out[int(1.65*sr):]))==0
    assert np.min(out[int(.8*sr):int(1.2*sr)])>.19


def test_pitch_changes_frequency_and_keeps_duration(tmp_path):
    tools();sr=16000;t=np.arange(sr*2)/sr
    src=tmp_path/'tone.wav';sf.write(src,np.sin(2*np.pi*440*t)*.2,sr)
    out,rate=sf.read(io.BytesIO(media.mask_audio_bytes(src,[],{'voice_change':'pitch'})))
    peak=np.argmax(abs(np.fft.rfft(out[sr//2:sr*3//2])))*rate/sr
    assert abs(peak-554.37)<4
    assert abs(len(out)/rate-2)<.05


def test_compute_voice_applies_without_pii(tmp_path):
    tools();src=tmp_path/'tone.wav';sf.write(src,np.ones(16000)*.1,16000)
    result=media.compute_media({'files':[{'id':'a','path':'tone.wav','abs':str(src)}],
                                'policy':{'voice_change':'pitch'}},[])
    assert 'a' in result['outputs'] and result['stats']['media_changed']==1


def test_legacy_frame_time_rejected():
    with pytest.raises(MediaError,match='重新检测'):
        media._visual_boxes([accepted(bbox=[0,0,10,10],frame_ts=1)],1,1)


def make_video(path,timestamps=(0,.1,.2,.3,.4),rotation=0):
    import av
    with av.open(str(path),'w') as out:
        stream=out.add_stream('libx264',rate=10)
        stream.width=64;stream.height=48;stream.pix_fmt='yuv420p'
        stream.codec_context.time_base=Fraction(1,90000)
        stream.codec_context.max_b_frames=0
        for i,ts in enumerate(timestamps):
            # textured moving patch; opaque processing is observable per frame
            img=np.full((48,64,3),180,dtype=np.uint8)
            img[8:24,8+i:24+i]=255
            frame=av.VideoFrame.from_ndarray(img,format='bgr24')
            frame.pts=round(ts*90000);frame.time_base=Fraction(1,90000)
            for packet in stream.encode(frame):out.mux(packet)
        for packet in stream.encode():out.mux(packet)


def test_video_all_frames_including_between_second_boundaries(tmp_path):
    tools();import av
    src=tmp_path/'input.mp4';make_video(src)
    hits=[accepted(bbox=[8+i,8,16,16],frame_index=i,media='video') for i in range(5)]
    dest=tmp_path/'masked.mp4';dest.write_bytes(media.mask_video_bytes(src,hits,{}))
    with av.open(str(dest)) as decoded:
        frames=list(decoded.decode(video=0))
    assert len(frames)==5
    for i,f in enumerate(frames):
        img=f.to_ndarray(format='bgr24')
        assert img[12:20,12+i:20+i].max()<12
    assert not probe(dest)['audio_tracks']


def test_video_vfr_timestamps_preserved(tmp_path):
    tools();import av
    ts=(0,.04,.16,.2,.4)
    src=tmp_path/'vfr.mp4';make_video(src,ts)
    dest=tmp_path/'masked.mp4';dest.write_bytes(media.mask_video_bytes(src,[],{}))
    with av.open(str(src)) as c:
        original=[float(f.pts*f.time_base) for f in c.decode(video=0)]
    with av.open(str(dest)) as c:
        actual=[float(f.pts*f.time_base) for f in c.decode(video=0)]
    assert actual==pytest.approx(original,abs=.001)


def test_video_manual_interval(tmp_path):
    tools();import av
    src=tmp_path/'source.mp4';make_video(src)
    hit=accepted(bbox=[8,8,24,24],source='manual',start=.15,end=.35)
    dest=tmp_path/'out.mp4';dest.write_bytes(media.mask_video_bytes(src,[hit],{}))
    with av.open(str(dest)) as c:
        frames=[f.to_ndarray(format='bgr24') for f in c.decode(video=0)]
    assert frames[0][12:20,12:20].mean()>100
    assert frames[2][12:20,12:20].max()<12 and frames[3][12:20,12:20].max()<12
    assert frames[4][12:20,12:20].mean()>100


def test_ffmpeg_failure_and_cancel_are_explicit(tmp_path):
    tools()
    with pytest.raises(MediaError):
        run_ffmpeg(['-i',tmp_path/'missing.mp4',tmp_path/'out.mp4'])
    class Cancel:
        def check_cancelled(self):raise MediaCancelled('cancelled')
    with pytest.raises(MediaCancelled):
        run_ffmpeg(['-version'],Cancel())

def test_detection_visits_all_frames_and_reports_no_audio_normally(monkeypatch,tmp_path):
    tools();src=tmp_path/'source.mp4';make_video(src)
    monkeypatch.setattr(video,'_ocr_engine',lambda:object())
    calls=[]
    def fake_image(path,policy):
        calls.append(path)
        return [{'bbox':[1,1,4,4],'status':'pending'}],[]
    monkeypatch.setattr(video,'detect_image',fake_image)
    hits,detail=video.detect_video_detail(src,{})
    assert len(calls)==5 and [h['frame_index'] for h in hits]==list(range(5))
    assert detail['state']=='complete' and detail['metadata']['frames_scanned']==5
    assert detail['metadata']['audio_tracks']==[]


def test_rotation_and_multiple_audio_tracks(tmp_path):
    tools();import av
    original=tmp_path/'original.mp4';make_video(original)
    rotated=tmp_path/'rotated.mp4'
    # Remux display matrix and add two independent tracks, without fake ASR.
    run_ffmpeg(['-display_rotation:v:0','90','-i',original,'-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=0.5',
                '-f','lavfi','-i','sine=frequency=880:sample_rate=16000:duration=0.5',
                '-map','0:v:0','-map','1:a:0','-map','2:a:0','-c:v','copy','-c:a','aac',
                rotated])
    assert probe(rotated)['rotation']==90
    out=tmp_path/'out.mp4'
    out.write_bytes(media.mask_video_bytes(rotated,[accepted(start=0,end=.5,track_index=1)],{}))
    meta=probe(out)
    assert meta['width']==48 and meta['height']==64 and meta['rotation']==0
    assert len(meta['audio_tracks'])==2
    for i in (0,1):
        wav=tmp_path/f'track{i}.wav'
        run_ffmpeg(['-i',out,'-map',f'0:a:{i}',wav])
        data,sr=sf.read(wav)
        if i==1:assert abs(data).max()<.0001
        else:assert abs(data).max()>.03

@pytest.mark.parametrize('gap,with_words,precision',[(.3,True,'word'),(.3,False,'segment'),(1.,True,'word')])
def test_cross_segment_phone_mapping(monkeypatch,gap,with_words,precision):
    monkeypatch.setattr(audio,'probe',lambda p:{'duration':6})
    segments=[{'start':0,'end':2,'text':'我的电话是1380013',
               'words':[{'text':'我的电话是','start':0,'end':.8},{'text':'1380013','start':1,'end':1.8}]},
              {'start':2+gap,'end':4+gap,'text':'8000，请联系我',
               'words':[{'text':'8000，','start':2.1+gap,'end':2.8+gap},
                        {'text':'请联系我','start':3+gap,'end':4+gap}]}]
    if not with_words:
        segments[1]['words']=[]
    monkeypatch.setattr(audio,'_transcribe',lambda *a:segments)
    hits,detail=audio.detect_audio_detail(Path('mock.wav'),{'pii_types':['phone']})
    assert len(hits)==1 and hits[0]['timing_precision']==precision
    assert hits[0]['value']=='1380013 8000'
    assert hits[0]['transcript_spans']==[{'segment_index':0,'start':5,'end':12},
                                        {'segment_index':1,'start':0,'end':4}]
    assert detail['transcript']==segments
    if with_words:
        assert hits[0]['start']==1 and hits[0]['end']==2.8+gap
    else:
        assert hits[0]['start']==0 and hits[0]['end']==4+gap


@pytest.mark.parametrize('left,right,gap',[('订单1380013','验证码8000',.2),
                                          ('1380013','8000',1.01),
                                          ('1380013。','8000',.2),
                                          ('13800138000','13900139000',.2),
                                          ('1234567','8000',.2)])
def test_unrelated_segment_numbers_are_not_concatenated(monkeypatch,left,right,gap):
    monkeypatch.setattr(audio,'probe',lambda p:{'duration':5})
    segments=[{'start':0,'end':1,'text':left,'words':[]},
              {'start':1+gap,'end':2+gap,'text':right,'words':[]}]
    monkeypatch.setattr(audio,'_transcribe',lambda *a:segments)
    hits,_=audio.detect_audio_detail(Path('mock.wav'),{'pii_types':['phone']})
    assert len(hits)==(2 if len(left)==11 and len(right)==11 else 0)


def test_spoken_phone_continues_across_three_segments(monkeypatch):
    monkeypatch.setattr(audio,'probe',lambda p:{'duration':5})
    segments=[{'start':i,'end':i+.7,'text':t,'words':[]} for i,t in
              enumerate(['电话幺三八','零零幺三','八零零零'])]
    monkeypatch.setattr(audio,'_transcribe',lambda *a:segments)
    hits,_=audio.detect_audio_detail(Path('mock.wav'),{'pii_types':['phone']})
    assert len(hits)==1 and hits[0]['start']==0 and hits[0]['end']==2.7
    assert len(hits[0]['transcript_spans'])==3 and hits[0]['timing_precision']=='segment'
