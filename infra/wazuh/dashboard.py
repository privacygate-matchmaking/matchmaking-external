"""PrivacyGate 관제 대시보드 (Wazuh 대시보드 saved objects). scripts/wazuh.py dashboard 가 가져다 쓴다.

한 화면에 PrivacyGate 허용·거부, 거부 사유, 위험도별 주요 경보, 탐지 분야별 경보, 서비스별 마지막 수집 시각(수집 상태),
trace_id 흐름 추적 표를 보여 준다. 모든 표는 wazuh-alerts-* 경보만 읽는다 (원본 감사 로그는 건드리지 않음).
"""
import json

INDEX = "wazuh-alerts-*"
PG = "data.service:(internal-api or transfer-api or external-api)"
# 화면별 쿼리 (KQL)
CATEGORIES = [
    ("역할·담당 관계 위반", "rule.id:(100110 or 100112 or 100150 or 100151 or 100177)"),
    ("승인 파일·수신자 불일치 / 무결성 오류", "rule.id:(100111 or 100120 or 100160 or 100161)"),
    ("철회·만료 동의 사용 / 심사 미완료", "rule.id:(100170 or 100172)"),
    ("다운로드 토큰·반복 다운로드", "rule.id:(100173 or 100174 or 100175)"),
    ("로그인 실패·역할 변경", "rule.id:(100180 or 100181 or 100182 or 100183)"),
    ("설정·정책 파일 변경 (FIM)", "rule.id:(100210 or 100211 or 100212)"),
    ("컨테이너·웹 이상", "rule.id:(100192 or 100194 or 100202 or 100203 or 100204)"),
]


def _source(query):
    return {"searchSourceJSON": json.dumps({"query": {"query": query, "language": "kuery"}, "filter": [],
                                            "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index"})}


def _ref():
    return [{"name": "kibanaSavedObjectMeta.searchSourceJSON.index", "type": "index-pattern", "id": INDEX}]


def _vis(vid, title, vtype, aggs, query, params=None):
    base = {"table": {"perPage": 10, "showPartialRows": False, "showMetricsAtAllLevels": False, "showTotal": False,
                      "totalFunc": "sum", "percentageCol": ""},
            "pie": {"type": "pie", "addTooltip": True, "addLegend": True, "legendPosition": "right", "isDonut": True,
                    "labels": {"show": True, "values": True, "last_level": True, "truncate": 100}},
            "metric": {"addTooltip": True, "addLegend": False, "type": "metric",
                       "metric": {"percentageMode": False, "useRanges": False, "colorSchema": "Green to Red",
                                  "metricColorMode": "None", "colorsRange": [{"from": 0, "to": 10000}],
                                  "labels": {"show": True}, "invertColors": False,
                                  "style": {"bgFill": "#000", "bgColor": False, "labelColor": False, "subText": "", "fontSize": 40}}}}
    vis_state = {"title": title, "type": vtype, "params": params or base[vtype], "aggs": aggs}
    return {"id": vid, "type": "visualization", "attributes": {
        "title": title, "visState": json.dumps(vis_state, ensure_ascii=False), "uiStateJSON": "{}", "description": "",
        "version": 1, "kibanaSavedObjectMeta": _source(query)}, "references": _ref()}


def _count():
    return {"id": "1", "enabled": True, "type": "count", "schema": "metric", "params": {}}


def _terms(aid, field, schema="bucket", size=10, label=None):
    p = {"field": field, "orderBy": "1", "order": "desc", "size": size, "otherBucket": False, "missingBucket": False}
    if label:
        p["customLabel"] = label
    return {"id": aid, "enabled": True, "type": "terms", "schema": schema, "params": p}


def objects():
    vis = [
        _vis("pg-allow-deny", "PG 허용·거부 건수", "pie", [_count(), _terms("2", "data.result", "segment", 5)], PG),
        _vis("pg-deny-reasons", "PG 거부 사유 TOP 15", "table", [_count(), _terms("2", "data.reason", size=15, label="거부 사유")],
             PG + " and data.result:deny"),
        _vis("pg-level", "위험도별 주요 경보", "table",
             [_count(), _terms("2", "rule.level", size=10, label="위험도"), _terms("3", "rule.description", size=20, label="경보")],
             "rule.groups:ieum and rule.level >= 8"),
        _vis("pg-collect", "서비스별 수집 상태 (마지막 수신 시각)", "table",
             [{"id": "1", "enabled": True, "type": "max", "schema": "metric", "params": {"field": "timestamp", "customLabel": "마지막 수신"}},
              _terms("2", "data.service", size=10, label="서비스")],
             "rule.groups:ieum"),
        _vis("pg-trace", "trace_id 흐름 추적 (검색창에 data.trace_id:값)", "table",
             [_count(), _terms("2", "data.trace_id", size=20, label="trace_id"), _terms("3", "data.service", size=5, label="서비스"),
              _terms("4", "data.action", size=10, label="작업"), _terms("5", "data.result", size=3, label="결과"),
              _terms("6", "rule.id", size=5, label="규칙")], PG),
    ]
    for i, (title, query) in enumerate(CATEGORIES):
        vis.append(_vis(f"pg-cat-{i}", title, "table", [_count(), _terms("2", "rule.description", size=10, label="경보")], query))
    panels, refs = [], []
    layout = [(0, 0, 16, 12), (16, 0, 32, 12), (0, 12, 24, 14), (24, 12, 24, 14), (0, 26, 48, 14)]
    for i in range(len(CATEGORIES)):
        layout.append(((i % 3) * 16, 40 + (i // 3) * 12, 16, 12))
    for i, (v, (x, y, w, h)) in enumerate(zip(vis, layout)):
        panels.append({"version": "7.10.2", "gridData": {"x": x, "y": y, "w": w, "h": h, "i": str(i + 1)},
                       "panelIndex": str(i + 1), "embeddableConfig": {}, "panelRefName": f"panel_{i}"})
        refs.append({"name": f"panel_{i}", "type": "visualization", "id": v["id"]})
    dash = {"id": "privacygate-overview", "type": "dashboard", "attributes": {
        "title": "PrivacyGate 관제", "description": "PrivacyGate 허용·거부·위반 유형·수집 상태·trace_id 추적",
        "panelsJSON": json.dumps(panels), "optionsJSON": json.dumps({"hidePanelTitles": False, "useMargins": True}),
        "version": 1, "timeRestore": True, "timeFrom": "now-24h", "timeTo": "now", "refreshInterval": {"pause": False, "value": 30000},
        "kibanaSavedObjectMeta": {"searchSourceJSON": json.dumps({"query": {"query": "", "language": "kuery"}, "filter": []})}},
        "references": refs}
    return vis + [dash]


def index_pattern():
    return {"id": INDEX, "type": "index-pattern", "attributes": {"title": INDEX, "timeFieldName": "timestamp"}, "references": []}


def ndjson(items):
    return "\n".join(json.dumps(o, ensure_ascii=False) for o in items) + "\n"
