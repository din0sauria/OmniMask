"""Real codecs, measured flash/pulse sync, no mocked media timing."""
import os
import shutil

import pytest

from scripts.accept_special import sync_case

pytestmark = pytest.mark.skipif(not (os.environ.get('OMNIMASK_FFMPEG') or shutil.which('ffmpeg')),
                                reason='FFmpeg required')


@pytest.mark.parametrize('mode',['ntsc','vfr'])
def test_flash_pulse_sync_nonzero_origin_and_delayed_audio(tmp_path,mode):
    result = sync_case(tmp_path,mode)
    assert result['source_measurement']['video_origin'] > 1.9
    assert all(abs(x)<=.1 for x in result['processed_measurement']['av_offset_seconds_by_track'])
