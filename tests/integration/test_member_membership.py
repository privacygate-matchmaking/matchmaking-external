"""내부 직원 화면의 회원 이용권 정보: 상담 직원·총관리자만, internal-api 범위 확인 후 정회원 여부·남은 매칭권·구매일."""
from test_matching import lab, purchase          # noqa: F401
from test_services import env, B, headers          # noqa: F401


def test_staff_sees_membership_after_scope_check(lab):
    t=lab['t']
    r=t.get(f'/api/matching/members/{B}/membership',headers=headers())
    assert r.status_code==200 and r.json()['paid'] is False and r.json()['last_paid_at'] is None
    purchase(lab['transfer'].state.matching)
    data=t.get(f'/api/matching/members/{B}/membership',headers=headers('ADMIN')).json()
    assert data['plan']=={'name':'이음 정회원','credits':5,'months':6}
    assert data['paid'] is True and data['wallet']['available']>=1 and data['last_paid_at']
    assert data['orders'][0]['status']=='paid' and 'order_id' not in data['orders'][0]
    assert lab['state']['scope_checks'][-1]==({'user_id':B},'ADMIN')


def test_membership_denied_when_not_in_scope_or_wrong_role(lab):
    t=lab['t']
    lab['state']['scope_allowed_tokens']=()
    assert t.get(f'/api/matching/members/{B}/membership',headers=headers()).status_code==403
    assert t.get(f'/api/matching/members/{B}/membership',headers=headers('APPROVER')).status_code==403
    assert t.get(f'/api/matching/members/{B}/membership',headers={'Authorization':'Bearer MEMBER'}).status_code in (401,403)
