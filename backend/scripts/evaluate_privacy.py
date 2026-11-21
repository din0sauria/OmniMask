"""Labelled local functional evaluation with real detectors and independent checks.

No downloads or training. Generated/SAPI cases are never called natural data.
Exit 0 means the measurements ran, not that recognition had no false negatives.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend'))
for tool in ('ffmpeg','ffprobe'):
    binary = ROOT/'.tools/ffmpeg'/(tool+'.exe')
    if binary.exists(): os.environ.setdefault('OMNIMASK_'+tool.upper(), str(binary))
os.environ.setdefault('OMNIMASK_ASR_MODEL', str(ROOT/'models/faster-whisper-base'))

import av
import cv2
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

from app.services.evaluation import compare, opaque_region_result
from app.services.ingest import read_text
from app.services.media_runtime import probe, video_frames
from app.services.verification import _candidate
from app.services.workflow import fingerprint


def _font(size):
    path = Path('C:/Windows/Fonts/arial.ttf')
    return ImageFont.truetype(str(path),size) if path.exists() else ImageFont.load_default(size=size)


def _span(text, value, rule, identifier):
    start = text.index(value)
    return {'id':identifier,'rule_name':rule,'value':value,'index_start':start,'index_end':start+len(value)}


def generate(definition, directory):
    """Labels originate from fixture construction, never detector output."""
    generator = definition['generator']
    is_sapi = generator in ('audio_digits','audio_repeated_zero')
    if definition['source_kind'] != ('sapi' if is_sapi else 'synthetic'):
        raise ValueError('Generated fixtures cannot be labelled as real natural material')
    directory.mkdir(parents=True,exist_ok=True)
    if generator.startswith(('text_','code_')):
        text = {'text_positive':'合成测试手机号：13912345678；邮箱 demo@example.com。',
                'text_negative':'本段是合成功能测试。项目年份2026，样本编号12345。',
                'code_positive':'demo_api_key = "OMNI_DEMO_1234567890ABCDEFG"\n',
                'code_negative':'retry_count = 3\nmessage = "functional sample"\n'}[generator]
        suffix = '.py' if generator.startswith('code_') else '.txt'
        path = directory/(definition['id']+suffix)
        path.write_text(text,encoding='utf-8')
        annotations = []
        if generator=='text_positive':
            annotations = [_span(text,'13912345678','phone','phone'),_span(text,'demo@example.com','email','email')]
        elif generator=='code_positive':
            annotations = [_span(text,'OMNI_DEMO_1234567890ABCDEFG','generic-api-key','key')]
        return path,annotations
    if generator in ('image_positive','image_negative'):
        image = Image.new('RGB',(640,360),'white')
        annotations = []
        if generator=='image_positive':
            draw=ImageDraw.Draw(image)
            font=_font(40)
            draw.text((22,25),'13912345678',font=font,fill='black')
            x,y,right,bottom=draw.textbbox((22,25),'13912345678',font=font)
            annotations.append({'id':'phone','rule_name':'phone','bbox':[x,y,right-x,bottom-y]})
            qr=cv2.QRCodeEncoder_create().encode('https://example.com/labelled-fixture')
            qr=cv2.copyMakeBorder(qr,4,4,4,4,cv2.BORDER_CONSTANT,value=255)
            qr=cv2.resize(qr,(152,152),interpolation=cv2.INTER_NEAREST)
            image.paste(Image.fromarray(qr).convert('RGB'),(28,150))
            annotations.append({'id':'qr','rule_name':'qr_code','bbox':[28,150,152,152]})
        path=directory/(definition['id']+'.png')
        image.save(path)
        return path,annotations
    if generator=='video_transient':
        from accept_special import make_visual_fixture
        path, expected=make_visual_fixture(directory)
        annotations=[]
        canvas=ImageDraw.Draw(Image.new('RGB',(640,360),'white'))
        font=_font(36)
        for frame,rules in expected.items():
            for rule in rules:
                if rule=='phone':
                    x,y,right,bottom=canvas.textbbox((20+frame*8,25),'13912345678',font=font)
                    box=[x,y,right-x,bottom-y]
                else:
                    box=[25+frame*35,170,136,136]
                annotations.append({'id':f'{rule}-frame-{frame}','rule_name':rule,
                                    'frame_index':frame,'bbox':box})
        return path,annotations
    if is_sapi:
        name='speech_zh_digits.wav' if generator=='audio_digits' else 'speech_zh.wav'
        source=ROOT/'.validation/samples'/name
        if not source.is_file(): raise FileNotFoundError('Prepare SAPI fixtures with make_speech_samples.ps1')
        path=directory/(definition['id']+'.wav')
        shutil.copy2(source,path)
        expected='13912345678' if generator=='audio_digits' else '13800138000'
        return path,[{'id':'spoken-phone','rule_name':'phone','value':expected,
                      'temporal_ground_truth':'not_manually_aligned'}]
    if generator=='audio_silence':
        path=directory/'audio_silence.wav'
        sf.write(path,np.zeros(48000),16000,subtype='PCM_16')
        return path,[]
    raise ValueError('Unknown fixture generator')


def detect(path, definition):
    from app.services.detect.audio import detect_audio_detail
    from app.services.detect.video import detect_video_detail
    from app.services.detect.image import detect_image
    from app.services.detect.text import detect_text_files
    from app.services.detect.code import detect_files
    policy={'mode':'delete','pii_types':['phone','email'],'use_model':False}
    fi={'id':'f','path':path.name,'abs':str(path),'is_text':definition['kind'] in ('text','code')}
    kind=definition['kind']
    if kind=='audio': return (*detect_audio_detail(path,policy),policy)
    if kind=='video': return (*detect_video_detail(path,policy),policy)
    if kind=='image':
        hits,warnings=detect_image(path,policy)
        return hits,{'state':'partial' if warnings else 'complete','warnings':warnings},policy
    result=(detect_text_files if kind=='text' else detect_files)([fi],policy)
    detail=result.get('files',{}).get('f') or {'state':'partial' if result['warnings'] else 'complete'}
    return result['findings'],detail,policy


def mask(path,hits,policy,definition,directory):
    from app.services.mask.code import compute
    from app.services.mask.media import compute_media
    fi={'id':'f','path':path.name,'abs':str(path),'is_text':definition['kind'] in ('code','text')}
    accepted=[{**hit,'id':f'e{i}','file_id':'f','file_path':path.name,'status':'accepted'} for i,hit in enumerate(hits)]
    task={'id':'eval','policy':policy,'files':[fi]}
    if definition['kind'] in ('code','text'):
        result=compute(task,accepted,True)
        output=directory/(definition['id']+'-masked'+path.suffix)
        if 'f' in result['outputs']:
            text,encoding=result['outputs']['f']
            output.write_bytes(text.encode(encoding))
        else: output.write_bytes(path.read_bytes())
        return output,{}
    result=compute_media(task,accepted,True)
    data,suffix=result['outputs']['f']
    output=directory/(definition['id']+'-masked'+suffix)
    output.write_bytes(data)
    return output,result['audits'].get('f',{})


def residual(source,output,annotations,kind,audit):
    if kind in ('code','text'):
        content,_=read_text(output)
        retained=[a['id'] for a in annotations if a['value'] in content]
        return {'basis':'independent exact annotated payload presence','residual_annotation_ids':retained,
                'limitations':'Absence of the full payload does not measure information remaining in partial masks.'}
    if kind=='image':
        image=cv2.imread(str(output))
        records=[opaque_region_result(image,a) for a in annotations]
    elif kind=='video':
        images={i:image for i,frame,ts,image in video_frames(output)}
        records=[opaque_region_result(images[a['frame_index']],a) for a in annotations]
    else:
        original,sr=sf.read(source)
        changed,rate=sf.read(output)
        identical=sr==rate and original.shape==changed.shape and np.array_equal(original,changed)
        checks=[]
        for start,end in audit.get('mute_intervals',[]):
            segment=changed[round(start*rate):round(end*rate)]
            checks.append(bool(len(segment) and np.max(np.abs(segment))==0))
        return {'basis':'decoded waveform equality plus requested PCM interval execution',
                'known_scripted_entity_signal_retained':bool(annotations and identical),
                'decoded_source_signal_identical':identical,
                'requested_intervals_all_zero':all(checks) if checks else None,
                'requested_intervals_checked':len(checks),
                'temporal_privacy_evaluation':'not_available_without_independently_aligned_target_times'}
    return {'basis':'independently labelled target inner ROI opacity after actual decoding',
            'regions':records,'residual_annotation_ids':[r['annotation_id'] for r in records if not r.get('opaque')],
            'limitations':'Opaque-mask fixture compliance is not general visual privacy or recoverability.'}


def run_case(definition,out):
    directory=out/'samples'/definition['id']
    source,truth=generate(definition,directory)
    before=fingerprint(source)
    hits,detail,policy=detect(source,definition)
    metrics=compare(hits,truth,definition['unit'],definition['scope_rules'])
    output,audit=mask(source,hits,policy,definition,directory)
    independent=residual(source,output,truth,definition['kind'],audit)
    recheck,recheck_detail,_=detect(output,definition)
    result={**definition,'status':detail['state'],'source':source.relative_to(out).as_posix(),
            'source_fingerprint':before,'output':output.relative_to(out).as_posix(),
            'output_fingerprint':fingerprint(output),'source_unchanged':fingerprint(source)==before,
            'annotations':[{k:v for k,v in a.items() if k!='value'} for a in truth],
            'metrics':metrics,'predictions':[_candidate(hit,definition['kind']) for hit in hits],
            'independent_residual_check':independent,
            'same_detector_recheck':{'candidate_count':len(recheck),'status':recheck_detail['state'],
                                     'note':'Not independent ground truth; may repeat the same misses.'}}
    if definition['kind']=='video' and metrics['fn']:
        from app.services.mask.media import mask_video_bytes
        missed=set(metrics['missed_annotation_ids'])
        supplement=[{'source':'manual','status':'accepted','confidence':1,'media':'video',
                     'bbox':a['bbox'],'start':a['frame_index']/5,'end':(a['frame_index']+1)/5}
                    for a in truth if a['id'] in missed]
        corrected=directory/'manual-corrected.mp4'
        corrected.write_bytes(mask_video_bytes(source,[*[{**h,'status':'accepted'} for h in hits],*supplement],policy))
        result['manual_recovery']={'basis':'scripted ground-truth coordinates simulate explicit reviewer additions',
                                   'not_an_automatic_recall_improvement':True,
                                   'independent_residual_check':residual(source,corrected,truth,'video',{}),
                                   'output':corrected.relative_to(out).as_posix()}
    if definition['kind']=='audio' and metrics['fn']:
        from app.services.mask.media import mask_audio_bytes
        duration=len(sf.read(source)[0])/sf.info(source).samplerate
        corrected=directory/'manual-full-utterance.wav'
        corrected.write_bytes(mask_audio_bytes(source,[{'status':'accepted','start':0,'end':duration}],{}))
        changed,_=sf.read(corrected)
        result['manual_recovery']={'basis':'explicit full-utterance mute; no independently labelled fine timing',
                                   'not_an_automatic_recall_improvement':True,
                                   'all_samples_zero':bool(np.max(np.abs(changed))==0),
                                   'utility_limitation':'All speech in the file is removed.',
                                   'output':corrected.relative_to(out).as_posix()}
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,default=ROOT/'backend/tests/fixtures/privacy_evaluation/manifest.json')
    parser.add_argument('--output',type=Path,default=ROOT/'.validation/privacy-evaluation')
    args=parser.parse_args()
    dataset=json.loads(args.manifest.read_text(encoding='utf-8'))
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    shutil.copy2(args.manifest,out/'manifest.json')
    started=time.monotonic()
    try: commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    except Exception: commit=None
    report={'dataset_id':dataset['dataset_id'],'schema_version':1,'code_commit':commit,
            'checked_at':datetime.now(timezone.utc).isoformat(),
            'git_worktree_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
            'seed':dataset.get('seed'),'cases':[],'engineering_failures':[],'quality_findings':[],
            'limitations':dataset['limitations'],'versions':{name:version(name) for name in ('av','faster-whisper','opencv-python','rapidocr-onnxruntime')}}
    report['protocol']={'visual_matching_iou':.25,'character_matching_iou':.5,
                        'audio_matching':'exact normalized scripted number; no independent timing labels',
                        'policy':{'mode':'delete','pii_types':['phone','email'],'use_model':False},
                        'manual_acceptance':'All automatic predictions explicitly accepted for fixture execution.',
                        'randomness':'Fixtures deterministic; seed recorded but no stochastic sampling performed.'}
    files=[*sorted((ROOT/'backend/app/services').rglob('*.py')),
           ROOT/'backend/scripts/evaluate_privacy.py', ROOT/'backend/scripts/accept_special.py',args.manifest]
    report['code_snapshot']=[{'path':p.resolve().relative_to(ROOT).as_posix(),
                              'sha256':fingerprint(p)['sha256']} for p in files]
    manifest=Path(os.environ['OMNIMASK_ASR_MODEL'])/'download-manifest.json'
    if manifest.is_file():report['asr_model']=json.loads(manifest.read_text(encoding='utf-8'))
    for definition in dataset['cases']:
        print('RUN',definition['id'],flush=True)
        try:
            case=run_case(definition,out)
            report['cases'].append(case)
            if case['status']!='complete' or not case['source_unchanged']:
                report['engineering_failures'].append({'case':definition['id'],'reason':'Incomplete check or source changed'})
            r=case['independent_residual_check']
            if case['metrics']['fp'] or case['metrics']['fn'] or r.get('residual_annotation_ids') or r.get('known_scripted_entity_signal_retained'):
                report['quality_findings'].append({'case':definition['id'],'fp':case['metrics']['fp'],'fn':case['metrics']['fn'],
                                                   'independent_residual_check':r})
            print('MEASURED',definition['id'],{k:case['metrics'][k] for k in ['tp','fp','fn']},flush=True)
        except Exception as exc:
            report['engineering_failures'].append({'case':definition['id'],'type':type(exc).__name__,
                                                   'reason':'Measurement did not complete; see local setup and rerun.'})
            print('FAILED',definition['id'],type(exc).__name__,flush=True)
    report['source_kinds']=dict(Counter(case['source_kind'] for case in report['cases']))
    report['natural_real_case_count']=sum(case['source_kind']=='public-real' for case in report['cases'])
    report['case_count']=len(report['cases'])
    report['requested_case_count']=len(dataset['cases'])
    report['elapsed_seconds']=time.monotonic()-started
    target=out/'evaluation-results.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT',target,'engineering_failures',len(report['engineering_failures']),'quality_findings',len(report['quality_findings']),flush=True)
    return 1 if report['engineering_failures'] else 0


if __name__=='__main__':raise SystemExit(main())
