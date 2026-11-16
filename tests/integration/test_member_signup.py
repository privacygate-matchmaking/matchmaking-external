"""회원가입: 가입 폼 → external-api → transfer-api → internal-api. 비밀번호는 어디에도 남지 않는다."""
from pathlib import Path

from ieum.member_signup import Limiter
from test_services import env

ORIGIN={'Origin':'https://testserver'}
PASSWORD='Synthetic-Pass-2026!'


def body(**extra):
    data={'username':'newmember1','password':PASSWORD,'name':'가상 회원','gender':'여','birth_date':'1995-04-01',
          'contact_phone':'010-1234-5678','contact_email':'newmember1@example.test',
          'privacy_consent':True,'consent_version':'2026-11','education_level':'4년제 대졸','job_group':'IT',
          'height_cm':170,'weight_kg':60,'introduction':'합성 소개'}
    data.update(extra)
    return data


def test_signup_relays_without_user_token_and_hides_password(env):
    result=env['e'].post('/api/signup',json=body(),headers=ORIGIN)
    assert result.status_code==201 and result.json()=={'status':'registered'}
    sent=env['state']['signups'][-1]
    assert sent['user_token'] is None
    assert sent['body']['password']==PASSWORD and sent['body']['height_cm']==170
    assert 'set-cookie' not in result.headers
    for log in ('external.jsonl','transfer.jsonl'):
        text=Path(env['root']/log).read_text(encoding='utf-8')
        assert 'member.signup' in text and PASSWORD not in text and '010-1234-5678' not in text


def test_signup_requires_same_origin(env):
    assert env['e'].post('/api/signup',json=body()).status_code==403
    assert env['e'].post('/api/signup',json=body(),headers={'Origin':'https://evil.test'}).status_code==403
    assert 'signups' not in env['state']


def test_signup_requires_consent_and_valid_shape(env):
    for i,bad in enumerate((body(privacy_consent=False),body(username='A'),body(password='short'),body(gender='x'),
                body(extra_field=1),body(height_cm=500),body(consent_version='old'),
                body(education_level=None),body(job_group=''),body(weight_kg=None),body(introduction=''))):
        result=env['e'].post('/api/signup',json=bad,headers={**ORIGIN,'X-Forwarded-For':f'10.0.1.{i}'})
        assert result.status_code==422,bad
        assert PASSWORD not in result.text
    assert 'signups' not in env['state']


def test_transfer_signup_accepts_only_external_service(env):
    assert env['t'].post('/api/internal/member-signups',json=body()).status_code==401
    assert env['t'].post('/api/internal/member-signups',json=body(),headers={'Authorization':'Bearer MEMBER'}).status_code==401
    assert env['t'].post('/api/internal/member-signups',json=body(),headers={'Authorization':'Bearer SVC_EXTERNAL'}).status_code==201


def test_signup_conflict_message_passes_through(env):
    env['state']['signup_error']=(409,{'code':'INVALID_STATE','message':'이미 사용 중인 아이디 또는 이메일입니다.'})
    result=env['e'].post('/api/signup',json=body(),headers=ORIGIN)
    assert result.status_code==409 and result.json()['error']['message']=='이미 사용 중인 아이디 또는 이메일입니다.'


def test_signup_rate_limit():
    clock=[0.0]
    limiter=Limiter(per_client=2,overall=3,window=600,clock=lambda:clock[0])
    assert limiter.allow('a') and limiter.allow('a') and not limiter.allow('a')
    assert limiter.allow('b') and not limiter.allow('c')
    clock[0]=601
    assert limiter.allow('a')


def test_signup_rate_limited_in_app(env):
    # 형식이 틀린 요청도 횟수에 포함된다 (제한은 Origin 확인 뒤, 본문 검사와 함께 적용)
    for _ in range(5):
        assert env['e'].post('/api/signup',json=body(),headers={**ORIGIN,'X-Forwarded-For':'10.0.0.9'}).status_code==201
    result=env['e'].post('/api/signup',json=body(),headers={**ORIGIN,'X-Forwarded-For':'10.0.0.9'})
    assert result.status_code==409 and '잠시 후' in result.json()['error']['message']
    assert env['e'].post('/api/signup',json=body(),headers={**ORIGIN,'X-Forwarded-For':'10.0.0.10'}).status_code==201
