"""30-case reproducible evaluation. Live calls require TWO explicit CLI flags."""
import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--live',action='store_true',help='Run real configured model, up to 30 requests')
parser.add_argument('--allow-model-calls',action='store_true',help='Explicitly authorize model requests and potential cost')
args=parser.parse_args()
if args.live and not args.allow_model_calls:
    parser.error('--live requires --allow-model-calls; review provider and cost first')

ROOT=Path(__file__).parent
os.environ['ASSISTANT_DB']=str(ROOT/'data'/f'evaluation-{uuid.uuid4().hex}.db')
os.environ.pop('ASSISTANT_TOKEN',None)
if not args.live:os.environ['LLM_ENABLED']='0'

from fastapi.testclient import TestClient
from app import app
from model import model_status

if args.live and not model_status()['ready']:
    parser.error(model_status()['message'])
cases=json.loads((ROOT/'eval_cases.json').read_text(encoding='utf-8'))
results=[]
with TestClient(app,headers={'X-Assistant-Request':'1'}) as client:
    project_id=client.post('/api/demo').json()['id']
    for case in cases:
        response=client.post(f'/api/projects/{project_id}/ask',json={'question':case['question'],'mode':'llm' if args.live else 'evidence'})
        output=response.json()
        citations=output.get('citations',[])
        retrieval_hit=case['expected'] in '\n'.join(s['text'] for s in output.get('candidates',[])) if case['answerable'] else None
        citation_valid=all(c.get('quote') and c['quote'] in c['text'] for c in citations) if citations else None
        results.append({**case,'http_status':response.status_code,'retrieval_hit_at_5':retrieval_hit,
                        'quote_verbatim_check':citation_valid,'human_semantic_verdict':'not_reviewed','output':output})
answerable=[r for r in results if r['answerable']]
unanswerable=[r for r in results if not r['answerable']]
summary={'created_at':datetime.now(timezone.utc).isoformat(),'mode':'real_model' if args.live else 'evidence_only',
         'real_model_evaluation':'executed' if args.live else 'NOT RUN — no model credentials/cost authorization used',
         'case_count':len(results),'http_successes':sum(r['http_status']==200 for r in results),
         'retrieval_hit_at_5':f"{sum(bool(r['retrieval_hit_at_5']) for r in answerable)}/{len(answerable)}",
         'unanswerable_questions_with_no_evidence':sum(r['output'].get('abstained') is True for r in unanswerable) if not args.live else None,
         'model_refusals_on_unanswerable':sum(r['output'].get('abstained') is True for r in unanswerable) if args.live else None,
         'model_semantic_accuracy':'NOT SCORED — requires human review of each claim',
         'pipeline_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['core.py','model.py','app.py','eval_cases.json']},
         'sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'examples').glob('*.md'))}}
folder=ROOT/'reports';folder.mkdir(exist_ok=True)
stem='live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') if args.live else 'baseline'
(folder/(stem+'.json')).write_text(json.dumps({'summary':summary,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 30 题评测记录','',f"模式：{summary['mode']}；时间：{summary['created_at']}",
       f"HTTP 成功：{summary['http_successes']}/30；可回答题的原文命中@5：{summary['retrieval_hit_at_5']}。",
       f"真实模型评测：{summary['real_model_evaluation']}。",'',
       '原文检索不生成答案，也不能识别所有缺资料或错误前提；有相关片段不代表可回答。',
       '命中用预设关键词检查；逐字引用检查不等于语义正确。完整输入、输出和引用见同名 JSON。',
       '真实模型的回答正确性、引用支持程度、拒答是否合理，需要逐题人工评分；未给出的指标没有被假定为通过。','',
       '|题号|类型|问题|应可回答|原文命中@5|返回情况|','|---|---|---|---|---|---|']
for r in results:
    outcome='调用失败' if r['http_status']!=200 else '拒答/无证据' if r['output'].get('abstained') else '模型回答' if args.live else '仅返回原文'
    lines.append(f"|{r['id']}|{r['category']}|{r['question']}|{'是' if r['answerable'] else '否'}|{r['retrieval_hit_at_5']}|{outcome}|")
(folder/(stem+'.md')).write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
