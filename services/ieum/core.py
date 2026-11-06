import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from privacygate.errors import ErrorCode, GateError, error_body

UTC = timezone.utc
MAX_FILE = 2 * 1024 * 1024


def now():
    return datetime.now(UTC)


def iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value


def uid():
    return str(uuid4())


def plain(row):
    from uuid import UUID
    return {k: iso(v) if isinstance(v, datetime) else str(v) if isinstance(v, UUID) else v for k, v in dict(row).items()}


def deny(code='ACCESS_DENIED', reason='access_denied'):
    raise GateError(ErrorCode(code), reason)


def required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f'Required environment variable: {name}')
    return value


class Audit:
    def __init__(self, service, path, insert=None):
        from privacygate.audit import AuditWriter
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.writer = AuditWriter(service, str(path), db_insert=insert)

    def record(self, request, action, result='allow', reason=None, target=None, target_type='delivery'):
        principal = getattr(request.state, 'principal', {})
        self.writer.record(trace_id=request.state.trace_id, action=action, result=result,
                           actor=principal.get('pseudo_id'), role=principal.get('role'),
                           target_type=target_type if target else None, target_id=target, reason=reason)


def application(title, audit):
    app = FastAPI(title=title, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)

    @app.middleware('http')
    async def headers(request, call_next):
        request.state.trace_id = uuid4().hex
        response = await call_next(request)
        response.headers.update({'X-Trace-Id': request.state.trace_id, 'Cache-Control': 'no-store',
                                 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(GateError)
    async def error(request, exc):
        route = request.scope.get('route')
        action = f'{request.method} {getattr(route, "path", "unknown")}'
        audit.record(request, action, 'deny', exc.reason, target=getattr(request.state,'delivery_id',None))
        for kind,value in getattr(request.state,'audit_references',[]):
            audit.record(request,'delivery.reference','deny',kind+'_reference',target=value,target_type=kind)
        return JSONResponse(error_body(exc.code, request.state.trace_id), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(error_body(ErrorCode.INVALID_REQUEST, request.state.trace_id), status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        audit.record(request, 'unhandled', 'error', 'internal_error')
        return JSONResponse(error_body(ErrorCode.INTERNAL_ERROR, request.state.trace_id), status_code=500)

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    return app


class BodyLimit:
    """Bound total JSON/multipart input, including requests without Content-Length."""
    def __init__(self, app):
        self.app=app

    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope['method'] in ('GET','HEAD','OPTIONS'):
            return await self.app(scope,receive,send)
        limit=3*1024*1024
        parts=[];total=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':
                return
            data=message.get('body',b'');total+=len(data)
            if total>limit:
                response=JSONResponse(error_body(ErrorCode.INVALID_REQUEST,uuid4().hex),status_code=422)
                return await response(scope,receive,send)
            parts.append(data)
            if not message.get('more_body',False):break
        consumed=False
        async def replay():
            nonlocal consumed
            if not consumed:
                consumed=True
                return {'type':'http.request','body':b''.join(parts),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)


class Database:
    """PostgreSQL in deployment; explicit SQLite fixture only for isolated tests."""
    def __init__(self, url, test=False):
        self.url = url
        self.test = test
        if not test and not url.startswith(('postgresql://', 'postgres://')):
            raise RuntimeError('Deployment requires PostgreSQL DATABASE_URL')

    @contextmanager
    def connection(self):
        if self.test:
            conn = sqlite3.connect(self.url, timeout=20)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
        else:
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(self.url, row_factory=dict_row)
        try:
            yield Queries(conn, self.test)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class Queries:
    def __init__(self, conn, sqlite):
        self.conn, self.sqlite = conn, sqlite

    def execute(self, query, args=()):
        if self.sqlite:
            query = query.replace('%s', '?').replace(' FOR UPDATE', '')
            args = tuple(iso(a) if isinstance(a, datetime) else str(a) if type(a).__name__ == 'UUID' else a for a in args)
        return self.conn.execute(query, args)

    def lock(self):
        if self.sqlite:
            self.conn.execute('BEGIN IMMEDIATE')
