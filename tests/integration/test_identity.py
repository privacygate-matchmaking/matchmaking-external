import json
import time
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from privacygate.errors import GateError

from ieum.identity import Identity


@pytest.fixture
def signing():
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()));jwk['kid']='test-key'
    def handle(req):
        if req.url.path=='/jwks':return httpx.Response(200,json={'keys':[jwk]})
        return httpx.Response(200,json={'issuer':'https://id.test','jwks_uri':'https://id.test/jwks',
          'authorization_endpoint':'https://id.test/auth','token_endpoint':'https://id.test/token','end_session_endpoint':'https://id.test/logout'})
    identity=Identity('https://id.test/discovery',httpx.Client(transport=httpx.MockTransport(handle)))
    claims={'sub':str(uuid4()),'iss':'https://id.test','aud':'transfer-api','azp':'external-web','exp':int(time.time())+300,'nonce':'expected'}
    return key,identity,claims


def test_real_rsa_signature(signing):
    key,identity,claims=signing
    token=jwt.encode(claims,key,algorithm='RS256',headers={'kid':'test-key'})
    assert identity.verify(token,'transfer-api','external-web','expected')['sub']==claims['sub']


@pytest.mark.parametrize('field,value',[('aud','other-api'),('azp','internal-web'),('iss','https://evil.test'),('exp',1),('nonce','wrong')])
def test_signed_wrong_claims_rejected(signing,field,value):
    key,identity,claims=signing;claims[field]=value
    token=jwt.encode(claims,key,algorithm='RS256',headers={'kid':'test-key'})
    with pytest.raises(GateError):identity.verify(token,'transfer-api','external-web','expected')


def test_wrong_signature_rejected(signing):
    key,identity,claims=signing
    other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    token=jwt.encode(claims,other,algorithm='RS256',headers={'kid':'test-key'})
    with pytest.raises(GateError):identity.verify(token,'transfer-api','external-web')
