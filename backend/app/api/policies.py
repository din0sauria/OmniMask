from fastapi import APIRouter

from ..models.schemas import PolicyIn
from ..services.policy import create_policy, delete_policy, get_policy, list_policies, update_policy

router = APIRouter(prefix="/api/policies", tags=["policies"])


@router.get("")
def all_policies():
    return {"code": 0, "msg": "ok", "data": list_policies()}


@router.get("/{policy_id}")
def one_policy(policy_id: str):
    return {"code": 0, "msg": "ok", "data": get_policy(policy_id)}


@router.post("")
def add_policy(body: PolicyIn):
    return {"code": 0, "msg": "ok", "data": create_policy(body)}


@router.put("/{policy_id}")
def edit_policy(policy_id: str, body: PolicyIn):
    return {"code": 0, "msg": "ok", "data": update_policy(policy_id, body)}


@router.delete("/{policy_id}")
def remove_policy(policy_id: str):
    delete_policy(policy_id)
    return {"code": 0, "msg": "ok", "data": None}
