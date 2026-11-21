"""Small labelled-fixture measurements; never a population accuracy claim."""
import re


def iou(left, right):
    ax, ay, aw, ah = left
    bx, by, bw, bh = right
    intersection = max(0, min(ax+aw,bx+bw)-max(ax,bx)) * max(0,min(ay+ah,by+bh)-max(ay,by))
    union = max(0,aw)*max(0,ah) + max(0,bw)*max(0,bh) - intersection
    return intersection/union if union else 0


def _digits(value):
    from .detect.audio import normalize_spoken
    return re.sub(r'\D', '', normalize_spoken(value)[0])


def compare(predictions, annotations, unit, scope_rules, iou_threshold=.25):
    """One-to-one matching. All unmatched in-scope predictions count as FP.

    Character entities use labelled spans; visual entities use independently
    generated frame/bbox labels; spoken numbers use the known scripted number.
    Multiple frames of one clip are not independent real-world samples.
    """
    evaluated = [p for p in predictions if p.get('rule_name') in scope_rules]
    unused = set(range(len(annotations)))
    matches, false_positives = [], []
    for pi, prediction in enumerate(evaluated):
        candidates = []
        for ai in unused:
            truth = annotations[ai]
            if prediction.get('rule_name') != truth['rule_name']:
                continue
            if unit == 'frame_object' and prediction.get('frame_index') != truth['frame_index']:
                continue
            if unit in ('image_object', 'frame_object'):
                overlap = iou(prediction.get('bbox', [0,0,0,0]),truth['bbox'])
                if overlap < iou_threshold:
                    continue
            elif unit == 'character_entity':
                a,b = prediction.get('index_start',-1), prediction.get('index_end',-1)
                c,d = truth['index_start'], truth['index_end']
                overlap = max(0,min(b,d)-max(a,c)) / max(1,max(b,d)-min(a,c))
                if overlap < .5:
                    continue
            elif unit == 'spoken_entity':
                if _digits(prediction.get('value','')) != _digits(truth['value']):
                    continue
                overlap = 1
            else:
                raise ValueError('Unknown evaluation unit')
            candidates.append((overlap,ai))
        if candidates:
            _, ai = max(candidates)
            unused.remove(ai)
            matches.append({'prediction_index':pi,'annotation_id':annotations[ai]['id']})
        else:
            false_positives.append(pi)
    tp, fp, fn = len(matches), len(false_positives), len(unused)
    return {'unit':unit,'tp':tp,'fp':fp,'fn':fn,'annotations':len(annotations),
            'predictions':len(evaluated),'out_of_scope_predictions':len(predictions)-len(evaluated),
            'precision':tp/(tp+fp) if tp+fp else None,
            'recall':tp/(tp+fn) if tp+fn else None,
            'matches':matches,'false_positive_indices':false_positives,
            'missed_annotation_ids':[annotations[i]['id'] for i in sorted(unused)]}


def opaque_region_result(image, annotation):
    """Independently inspect the known target's inner ROI, not detector boxes.

    This checks the fixture's expected opaque masking action. It is not a
    general measure of recoverability, OCR accuracy or visual privacy.
    """
    import numpy as np
    x,y,w,h = annotation['bbox']
    # Detected QR boxes exclude the scripted quiet border; an inner core is
    # labelled before detection and remains inside the actual QR payload area.
    margin = .28 if annotation['rule_name']=='qr_code' else .25
    left,top = round(x+w*margin),round(y+h*margin)
    right,bottom = round(x+w*(1-margin)),round(y+h*(1-margin))
    roi = image[top:bottom,left:right]
    if not roi.size:
        return {'annotation_id':annotation['id'],'evaluated':False,'reason':'empty labelled ROI'}
    bright_fraction = float(np.mean(np.max(roi,axis=2)>48)) if roi.ndim==3 else float(np.mean(roi>48))
    return {'annotation_id':annotation['id'],'evaluated':True,
            'criterion':'labelled inner ROI, fraction of decoded pixels >48 <=0.01',
            'bright_fraction':bright_fraction,'opaque':bright_fraction<=.01}
