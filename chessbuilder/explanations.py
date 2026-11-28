"""Optional evidence ordering adapter. No model-generated factual chess claims."""
import json
import os
from datetime import datetime,timezone
from urllib.parse import urlparse
import httpx
import chess
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.training import digest,now
from chessbuilder.review import select_job,get_analyses,line,metrics
from chessbuilder.tactics import diagnose
from chessbuilder.strategy import diagnose_strategy

router=APIRouter(prefix="/api/explanations")
VERSION="evidence-order-v1"
MODES={"observe":"Observe a evidência: ","compare":"Compare as continuações: ","calculate":"Calcule a partir deste ponto: "}
def configuration():
    if os.getenv("CHESSBUILDER_EXPLAIN_ENABLED")!="1":return None
    key=os.getenv("CHESSBUILDER_EXPLAIN_API_KEY");model=os.getenv("CHESSBUILDER_EXPLAIN_MODEL")
    url=os.getenv("CHESSBUILDER_EXPLAIN_URL","https://api.openai.com/v1/chat/completions")
    p=urlparse(url)
    if not key or not model or p.scheme!="https" or not p.hostname or p.username or p.password or p.query or p.fragment:return None
    try:limit=max(1,min(100,int(os.getenv("CHESSBUILDER_EXPLAIN_DAILY_LIMIT","10"))))
    except ValueError:limit=10
    return {"key":key,"model":model,"url":url,"limit":limit}

def local_payload(game_id,ply):
    with connect() as c:
        game=c.execute("SELECT initial_fen FROM games WHERE id=?",(game_id,)).fetchone()
        if not game:raise HTTPException(404,"Partida inexistente")
        moves=c.execute("SELECT ply,uci FROM moves WHERE game_id=? AND ply<=? ORDER BY ply",(game_id,ply)).fetchall()
        a=get_analyses(c,select_job(c,game_id)).get(ply)
    if len(moves)!=ply or not a:raise HTTPException(409,"Analise a posição antes de explicar")
    b=chess.Board(game["initial_fen"])
    for m in moves[:-1]:b.push_uci(m["uci"])
    played=chess.Move.from_uci(moves[-1]["uci"])
    if played not in b.legal_moves:raise HTTPException(409,"Posição inconsistente")
    facts=[]
    def add(kind,text,evidence):facts.append({"id":len(facts)+1,"kind":kind,"text":text,"evidence":evidence})
    score=metrics(a)
    if score:
        add("evaluation",f"Perda estimada: {score['win_loss']:.1f} pontos percentuais, na métrica local.",{"loss_pp":round(score["win_loss"],2)})
    for side,label in (("best","Linha de referência"),("played","Linha do lance jogado")):
        pv=a[side].get("pv",[]);legal=line(b,pv)
        if legal and len(legal)==len(pv):add(side,label+": "+" ".join(x["san"] for x in legal),[{"uci":x["uci"],"san":x["san"]} for x in legal])
    for f in diagnose(b,played,a)["findings"]:
        if f["confidence"]=="line_witness":add("tactical",f["explanation"],f["evidence"])
    for f in diagnose_strategy(b,played,a)["findings"]:
        add("strategic",f["explanation"],f["evidence"])
    # No names, account identifiers, PGN, original document, chat history or API key.
    return {"version":VERSION,"fen":b.fen(),"played_uci":played.uci(),"facts":facts[:20]}

class Preview(BaseModel):
    game_id:int
    ply:int=Field(ge=1)
@router.post("/preview")
def preview(body:Preview):
    payload=local_payload(body.game_id,body.ply)
    return {"payload":payload,"digest":digest(payload),"enabled":configuration() is not None}

class Assist(Preview):
    payload_digest:str
    fact_ids:list[int]=Field(min_length=1,max_length=20)
    consent:bool=False

def validate(raw,ids):
    if not isinstance(raw,dict) or set(raw)!={"steps"} or not isinstance(raw["steps"],list):raise ValueError("Invalid output")
    steps=raw["steps"]
    if not steps or len(steps)>len(ids):raise ValueError("Invalid step count")
    seen=set()
    for s in steps:
        if not isinstance(s,dict) or set(s)!={"id","mode"} or type(s["id"]) is not int or s["id"] not in ids or s["id"] in seen or s["mode"] not in MODES:raise ValueError("Unverified evidence selection")
        seen.add(s["id"])
    return steps

def provider(config,payload):
    # Provider may only select existing evidence and one local pedagogical mode.
    prompt="Return a JSON object with steps [{id: integer, mode: observe|compare|calculate}]. Use only provided IDs; never produce chess claims or new text. Order steps for learning."
    body={"model":config["model"],"messages":[{"role":"system","content":prompt},{"role":"user","content":json.dumps(payload,ensure_ascii=False)}],"response_format":{"type":"json_object"},"max_completion_tokens":350}
    with httpx.Client(timeout=httpx.Timeout(12,connect=5),follow_redirects=False) as client:
        with client.stream("POST",config["url"],headers={"Authorization":"Bearer "+config["key"],"Content-Type":"application/json"},json=body) as r:
            r.raise_for_status();chunks=[];size=0
            for chunk in r.iter_bytes():
                size+=len(chunk)
                if size>100000:raise ValueError("Provider response too large")
                chunks.append(chunk)
    data=json.loads(b"".join(chunks))
    return json.loads(data["choices"][0]["message"]["content"])

@router.post("/assist")
def assist(body:Assist):
    payload=local_payload(body.game_id,body.ply)
    if digest(payload)!=body.payload_digest:raise HTTPException(409,"Análise mudou; confira novamente a prévia")
    ids=set(body.fact_ids);facts={f["id"]:f for f in payload["facts"]}
    if not ids.issubset(facts) or len(ids)!=len(body.fact_ids):raise HTTPException(422,"Evidência inválida")
    selected=[facts[i] for i in body.fact_ids]
    def result(steps,source,reason=None):
        return {"source":source,"reason":reason,"steps":[{"id":s["id"],"mode":s["mode"],"text":MODES[s["mode"]]+facts[s["id"]]["text"]} for s in steps]}
    fallback=[{"id":f["id"],"mode":"compare" if f["kind"] in ("best","played") else "observe"} for f in selected]
    cfg=configuration()
    if not body.consent or not cfg:return result(fallback,"local","external_disabled")
    outgoing={**payload,"facts":selected}
    cache_key=digest([VERSION,cfg["url"],cfg["model"],outgoing])
    day=datetime.now(timezone.utc).date().isoformat()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        cached=c.execute("SELECT payload FROM explanation_cache WHERE cache_key=?",(cache_key,)).fetchone()
        if cached:
            try:return result(validate(json.loads(cached[0]),ids),"cache")
            except (ValueError,TypeError):c.execute("DELETE FROM explanation_cache WHERE cache_key=?",(cache_key,))
        used=c.execute("SELECT calls FROM explanation_usage WHERE day=?",(day,)).fetchone()
        if used and used[0]>=cfg["limit"]:return result(fallback,"local","quota")
        c.execute("INSERT INTO explanation_usage VALUES(?,1) ON CONFLICT(day) DO UPDATE SET calls=calls+1",(day,))
    try:
        raw=provider(cfg,outgoing);steps=validate(raw,ids)
    except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError,AttributeError):
        return result(fallback,"local","provider_unavailable_or_invalid")
    with connect() as c:c.execute("INSERT OR REPLACE INTO explanation_cache VALUES(?,?,?)",(cache_key,json.dumps(raw),now()))
    return result(steps,"external")
