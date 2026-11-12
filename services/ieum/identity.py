import base64
import hashlib
import secrets
import threading
import time
from urllib.parse import urlencode

import httpx
import jwt
from privacygate.policy import role_from_claims

from .core import deny


class Identity:
    def __init__(self, discovery_url, client=None):
        self.url = discovery_url
        self.client = client or httpx.Client(timeout=10, trust_env=False)
        self._doc = None
        self._at = 0
        self._keys = {}
        self._key_at = 0
        self.lock = threading.RLock()
        self.service_cache = {}

    def discovery(self):
        with self.lock:
            if self._doc is None or time.time() - self._at > 300:
                try:
                    response = self.client.get(self.url)
                    response.raise_for_status()
                    doc = response.json()
                    for field in ['issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri', 'end_session_endpoint']:
                        if not isinstance(doc.get(field), str):
                            raise ValueError()
                    self._doc, self._at = doc, time.time()
                except (httpx.HTTPError, ValueError):
                    deny('INTERNAL_ERROR', 'identity_unavailable')
            return self._doc

    def verify(self, token, audience, azp, nonce=None):
        try:
            header = jwt.get_unverified_header(token)
            if header.get('alg') != 'RS256' or not header.get('kid'):
                raise ValueError()
            doc = self.discovery()
            with self.lock:
                if header['kid'] not in self._keys or time.time() - self._key_at > 300:
                    response = self.client.get(doc['jwks_uri'])
                    response.raise_for_status()
                    # Keycloak also publishes encryption keys (e.g. RSA-OAEP).
                    # Only parse keys eligible for the explicitly allowed RS256 verifier.
                    self._keys = {
                        j['kid']: jwt.PyJWK.from_dict(j, algorithm='RS256').key
                        for j in response.json()['keys']
                        if isinstance(j, dict)
                        and isinstance(j.get('kid'), str) and j['kid']
                        and j.get('kty') == 'RSA'
                        and j.get('use') in (None, 'sig')
                        and j.get('alg') in (None, 'RS256')
                        and ('key_ops' not in j or 'verify' in j['key_ops'])
                    }
                    self._key_at = time.time()
            if header['kid'] not in self._keys:
                deny('AUTH_REQUIRED', 'token_signing_key_missing')
            claims = jwt.decode(token, self._keys[header['kid']], algorithms=['RS256'],
                                audience=audience, issuer=doc['issuer'], leeway=5,
                                options={'require': ['exp', 'iss', 'aud', 'sub']})
            if claims.get('azp') != azp:
                deny('AUTH_REQUIRED', 'token_client_mismatch')
            if nonce is not None and not secrets.compare_digest(str(claims.get('nonce', '')), nonce):
                deny('AUTH_REQUIRED', 'token_nonce_mismatch')
            return claims
        except jwt.ExpiredSignatureError:
            deny('AUTH_REQUIRED', 'token_expired')
        except jwt.ImmatureSignatureError:
            deny('AUTH_REQUIRED', 'token_not_yet_valid')
        except jwt.InvalidAudienceError:
            deny('AUTH_REQUIRED', 'token_audience_mismatch')
        except jwt.InvalidIssuerError:
            deny('AUTH_REQUIRED', 'token_issuer_mismatch')
        except jwt.InvalidSignatureError:
            deny('AUTH_REQUIRED', 'token_signature_invalid')
        except jwt.MissingRequiredClaimError:
            deny('AUTH_REQUIRED', 'token_missing_claim')
        except httpx.HTTPError:
            deny('AUTH_REQUIRED', 'token_key_fetch_failed')
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            deny('AUTH_REQUIRED', 'invalid_token')

    def token(self, data, client_id, secret):
        try:
            response = self.client.post(self.discovery()['token_endpoint'], data=data, auth=(client_id, secret))
            if response.status_code in (400, 401):
                deny('AUTH_REQUIRED', 'token_rejected')
            response.raise_for_status()
            value = response.json()
            if not isinstance(value.get('access_token'), str) or not 1 <= int(value['expires_in']) <= 86400:
                raise ValueError()
            return value
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            deny('INTERNAL_ERROR', 'identity_unavailable')

    def service(self, client_id, secret):
        with self.lock:
            cached = self.service_cache.get(client_id)
            if cached and cached[1] > time.time() + 30:
                return cached[0]
            body = self.token({'grant_type': 'client_credentials'}, client_id, secret)
            self.service_cache[client_id] = (body['access_token'], time.time() + int(body['expires_in']))
            return body['access_token']


def bearer(request):
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer ') or not header[7:]:
        deny('AUTH_REQUIRED', 'token_missing')
    return header[7:]


def principal(claims):
    role = role_from_claims(claims)
    if role is None:
        deny('ACCESS_DENIED', 'ambiguous_role')
    return {'user_id': claims['sub'], 'role': role,
            'pseudo_id': claims.get('pseudo_id') or 'U-' + hashlib.sha256(claims['sub'].encode()).hexdigest()[:12]}
