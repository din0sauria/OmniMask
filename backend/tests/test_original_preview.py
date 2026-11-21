"""Real codec tests for read-only browser original-preview derivatives."""
import hashlib
import json
import os
import shutil
import subprocess
from fractions import Fraction

import av
import pytest

from app.services.media_runtime import MediaError, MediaCancelled, executable, probe, run_ffmpeg
from app.services.original_preview import prepare_original

pytestmark = pytest.mark.skipif(not(os.environ.get('OMNIMASK_FFMPEG') or shutil.which('ffmpeg')),
                                reason='FFmpeg required')


def streams(path):
    result=subprocess.run([executable('ffprobe'),'-v','error','-show_streams','-of','json',str(path)],
                          capture_output=True,check=True)
    return json.loads(result.stdout)['streams']


def test_wma_to_browser_pcm_preserves_channels_duration_and_source(tmp_path):
    source=tmp_path/'private.wma'
    run_ffmpeg(['-f','lavfi','-i','sine=frequency=440:sample_rate=32000:duration=1',
                '-ac','2','-c:a','wmav2',source])
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    dest=tmp_path/'preview.wav'
    assert prepare_original(source,'audio',dest)==dest
    audio=streams(dest)[0]
    assert audio['codec_name']=='pcm_s16le' and audio['channels']==2
    assert abs(probe(dest)['duration']-probe(source)['duration'])<.1
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


def test_avi_video_h264_aac_primary_track_only(tmp_path):
    source=tmp_path/'legacy.avi'
    run_ffmpeg(['-f','lavfi','-i','color=white:size=64x48:rate=25:duration=0.8',
                '-f','lavfi','-i','sine=frequency=440:duration=0.8',
                '-f','lavfi','-i','sine=frequency=880:duration=0.8',
                '-map','0:v','-map','1:a','-map','2:a','-c:v','mpeg4','-c:a','mp3',source])
    before=hashlib.sha256(source.read_bytes()).digest()
    dest=tmp_path/'browser.mp4';prepare_original(source,'video',dest)
    info=streams(dest)
    assert info[0]['codec_name']=='h264' and info[0]['pix_fmt']=='yuv420p'
    assert [s['codec_name'] for s in info if s['codec_type']=='audio']==['aac']
    assert hashlib.sha256(source.read_bytes()).digest()==before


def test_silent_vfr_mkv_stays_variable_timestamps(tmp_path):
    import numpy as np
    source=tmp_path/'vfr.mkv'
    ts=[0,.04,.16,.20,.40]
    with av.open(str(source),'w') as c:
        stream=c.add_stream('libx264',rate=10)
        stream.width=64;stream.height=48;stream.pix_fmt='yuv420p'
        stream.codec_context.time_base=Fraction(1,90000);stream.codec_context.max_b_frames=0
        for t in ts:
            frame=av.VideoFrame.from_ndarray(np.ones((48,64,3),dtype=np.uint8)*128,format='bgr24')
            frame.pts=round(t*90000);frame.time_base=Fraction(1,90000)
            for p in stream.encode(frame):c.mux(p)
        for p in stream.encode():c.mux(p)
    dest=tmp_path/'preview.mp4';prepare_original(source,'video',dest)
    with av.open(str(dest)) as c:
        actual=[float(f.pts*f.time_base) for f in c.decode(video=0)]
    assert actual==pytest.approx(ts,abs=.001)
    assert not probe(dest)['audio_tracks']


def test_original_preview_rotation_is_applied_once(tmp_path):
    plain=tmp_path/'plain.mp4';source=tmp_path/'rotated.mp4';dest=tmp_path/'preview.mp4'
    run_ffmpeg(['-f','lavfi','-i','color=white:size=64x48:rate=25:duration=0.4','-c:v','libx264',plain])
    run_ffmpeg(['-display_rotation:v:0','90','-i',plain,'-c','copy',source])
    prepare_original(source,'video',dest)
    meta=probe(dest)
    assert (meta['width'],meta['height'],meta['rotation'])==(48,64,0)


def test_invalid_or_cancelled_original_not_published(tmp_path):
    source=tmp_path/'broken.avi';source.write_bytes(b'not media')
    dest=tmp_path/'original.mp4'
    with pytest.raises(MediaError):prepare_original(source,'video',dest)
    assert not dest.exists()
    class Cancel:
        def check_cancelled(self):raise MediaCancelled('cancelled')
    with pytest.raises(MediaCancelled):prepare_original(source,'video',dest,Cancel())
    assert not dest.exists()
