"""Lab-only alert index lifecycle/budget control, never access source audit files."""
import base64
import json
import os
import re
import ssl
import time
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PATTERN='wazuh-alerts-4.x-*'
INDEX=re.compile(r'^wazuh-alerts-4\.x-\d{4}\.\d{2}\.\d{2}$')
POLICY='ieum-alerts-7d'
BUDGET=2*1024**3


def policy():
    return {'policy':{'description':'IEUM lab alerts: delete at 7 days of index age',
        'default_state':'retain','states':[
            {'name':'retain','actions':[],'transitions':[{'state_name':'delete','conditions':{'min_index_age':'7d'}}]},
            {'name':'delete','actions':[{'delete':{}}],'transitions':[]}],
        'ism_template':[{'index_patterns':[PATTERN],'priority':200}]}}


def retirement(indices, today, budget=BUDGET):
    """Delete only completed daily indices when over budget. Never other datasets."""
    rows=[r for r in indices if INDEX.fullmatch(r['index'])]
    total=sum(int(r.get('store.size') or 0) for r in rows)
    remove=[]
    for row in sorted(rows,key=lambda x:x['index']):
        if total<=budget:break
        if row['index'][-10:]>=today:continue
        remove.append(row['index']);total-=int(row.get('store.size') or 0)
    # Active day is not deleted. Pause indexing if it alone exceeds the budget.
    block=[r['index'] for r in rows if r['index'] not in remove] if total>budget else []
    return remove,block


def request(method,path,body=None):
    ctx=ssl.create_default_context(cafile='/certs/root-ca.pem')
    token=base64.b64encode(('admin:'+os.environ['INDEXER_PASSWORD']).encode()).decode()
    req=Request('https://wazuh.indexer:9200/'+path.lstrip('/'),method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Basic '+token,'Content-Type':'application/json'})
    try:
        with urlopen(req,context=ctx,timeout=20) as response:return json.load(response)
    except HTTPError as exc:
        if exc.code==404:return None
        raise RuntimeError('Indexer HTTP '+str(exc.code)) from None


def tick():
    expected=policy()
    old=request('GET','_plugins/_ism/policies/'+POLICY)
    if old is None:request('PUT','_plugins/_ism/policies/'+POLICY,expected)
    else:
        # Never silently keep a different lifecycle policy.
        current=old.get('policy',{})
        if current.get('states')!=expected['policy']['states']:
            raise RuntimeError('Retention policy differs; inspect it before proceeding')
    request('PUT','_template/ieum-alerts-settings',{'index_patterns':[PATTERN],'order':200,
        'settings':{'number_of_shards':1,'number_of_replicas':0}})
    info=request('GET','_plugins/_ism/explain/'+PATTERN) or {}
    for name,details in info.items():
        if INDEX.fullmatch(name) and not details.get('policy_id'):
            request('POST','_plugins/_ism/add/'+name,{'policy_id':POLICY})
    rows=request('GET','_cat/indices/'+PATTERN+'?format=json&bytes=b&h=index,store.size') or []
    remove,block=retirement(rows,datetime.now(timezone.utc).strftime('%Y.%m.%d'))
    for name in remove:request('DELETE',name)
    for name in block:request('PUT',name+'/_settings',{'index.blocks.write':True})
    print(json.dumps({'status':'quota_blocked' if block else 'ok','policy':POLICY,
        'budget_bytes':BUDGET,'removed_indices':remove,'blocked_indices':block}),flush=True)


def probe(expected, attempts=24, pause=5):
    if not expected:
        print('Synthetic probe requires expected events.')
        return False
    found={};last_error=None
    for attempt in range(attempts):
        try:
            result=request('POST',PATTERN+'/_search',{'size':50,'query':{'terms':{'data.trace_id':list(expected)}}}) or {}
            found={h['_source']['data']['trace_id']:str(h['_source']['rule']['id']) for h in result.get('hits',{}).get('hits',[])}
            last_error=None
            if all(found.get(k)==v for k,v in expected.items()):
                print('Synthetic collection -> rules -> index search verified. Check data.lab_fixture:true in Dashboard.')
                return True
        except Exception as exc:
            # Do not print response bodies, credentials or arbitrary exception text.
            last_error=type(exc).__name__
        if attempt+1<attempts:time.sleep(pause)
    print(json.dumps({'expected_events':len(expected),'indexed_events':len(found),
        'missing_events':sum(k not in found for k in expected),
        'rule_mismatches':[{'expected':v,'actual':found[k]} for k,v in expected.items() if k in found and found[k]!=v],
        'last_error_type':last_error}))
    print('Synthetic pipeline check failed or timed out. Inspect manager, filebeat, indexer and retention logs.')
    return False


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='probe':
        sys.exit(0 if probe(json.load(sys.stdin)) else 1)
    while True:
        try:tick()
        except Exception as exc:
            # No credentials, request headers, response bodies or member contents.
            print(json.dumps({'status':'error','type':type(exc).__name__,
                'reason':str(exc) if isinstance(exc,RuntimeError) else 'request_or_parse_failed'}),flush=True)
        time.sleep(300)
