"""Metric protocol tests; actual detector scores are measured by the script."""
import numpy as np

from app.services.evaluation import compare, opaque_region_result


def test_duplicate_predictions_are_false_positive_not_multiple_true_positives():
    truth=[{'id':'a','rule_name':'phone','index_start':0,'index_end':11}]
    p={'rule_name':'phone','index_start':0,'index_end':11}
    result=compare([p,p],truth,'character_entity',['phone'])
    assert (result['tp'],result['fp'],result['fn'])==(1,1,0)


def test_visual_match_requires_frame_and_ground_truth_box_overlap():
    truth=[{'id':'a','rule_name':'qr_code','frame_index':0,'bbox':[10,10,100,100]}]
    p=[{'rule_name':'qr_code','frame_index':1,'bbox':[10,10,100,100]},
       {'rule_name':'qr_code','frame_index':0,'bbox':[200,200,100,100]}]
    result=compare(p,truth,'frame_object',['qr_code'])
    assert (result['tp'],result['fp'],result['fn'])==(0,2,1)


def test_empty_negative_precision_recall_are_undefined_not_perfect():
    result=compare([],[],'spoken_entity',['phone'])
    assert result['precision'] is result['recall'] is None


def test_spoken_matching_uses_scripted_digits_not_model_transcript_as_truth():
    truth=[{'id':'a','rule_name':'phone','value':'13800138000'}]
    result=compare([{'rule_name':'phone','value':'1380013800'}],truth,'spoken_entity',['phone'])
    assert (result['tp'],result['fp'],result['fn'])==(0,1,1)


def test_residual_region_check_uses_independent_labelled_roi():
    truth={'id':'a','rule_name':'phone','bbox':[0,0,100,100]}
    image=np.full((100,100,3),255,np.uint8)
    assert opaque_region_result(image,truth)['opaque'] is False
    image[:]=0
    assert opaque_region_result(image,truth)['opaque'] is True
