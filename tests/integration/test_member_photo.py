"""내 프로필 사진 미리보기: 본인 세션으로만, 이미지 형식만 브라우저로 전달."""
from test_services import env, login


def test_photo_preview_relay(env):
    login(env);e=env['e']
    r=e.get('/api/me/profile/photo?which=latest')
    assert r.status_code==200 and r.headers['content-type']=='image/jpeg' and r.content.startswith(b'\xff\xd8\xff')
    assert env['state']['photo_which']=='latest'
    assert r.headers['cache-control']=='no-store' and r.headers['x-content-type-options']=='nosniff'
    assert e.get('/api/me/profile/photo?which=original').status_code==422


def test_photo_preview_rejects_non_image_and_passes_missing(env):
    login(env);e=env['e']
    env['state']['photo_kind']='html'
    assert e.get('/api/me/profile/photo').status_code==500
    env['state']['photo_kind']='missing'
    r=e.get('/api/me/profile/photo')
    assert r.status_code==404 and r.json()['error']['message']=='표시할 사진이 없습니다.'


def test_photo_preview_requires_session_and_member_token(env):
    assert env['e'].get('/api/me/profile/photo').status_code==401
    assert env['t'].get('/api/internal/member-photo',headers={'Authorization':'Bearer SVC_EXTERNAL'}).status_code==401
