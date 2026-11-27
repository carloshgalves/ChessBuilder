"""Versioned deterministic review scheduling and fixed Woodpecker collections."""
import json
import random
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.training import now,state

router=APIRouter(prefix="/api/repetition")
VERSION="interval-v1"

def next_review(previous,correct,hints,elapsed_ms,finished,source):
    """A transparent heuristic; not FSRS or a calibrated forgetting model."""
    d=float(previous["difficulty"]) if previous else 5.0
    interval=float(previous["interval_days"]) if previous else 0.0
    reps=int(previous["repetitions"]) if previous else 0
    target=480000 if source=="endgame" else 180000 if source=="personal" else 90000
    if not correct:
        d=min(10,d+1.2);interval=0.25
    elif hints:
        d=min(10,d+0.5);interval=max(0.5,min(2,interval*0.7))
    else:
        slow=elapsed_ms is None or elapsed_ms>target
        d=max(1,d+(0.2 if slow else -0.5))
        interval=max(1,interval*(1.3 if slow else 1.5+(10-d)/10))
    interval=round(min(180,interval),3)
    return {"version":VERSION,"due_at":(finished+timedelta(days=interval)).isoformat(),"interval_days":interval,"difficulty":round(d,3),"repetitions":reps+1}

def sync_reviews(c):
    # Replay only previously unapplied completions in chronological order.
    rows=c.execute("""SELECT a.*,t.source FROM training_attempts a JOIN training_items t ON t.id=a.item_id
        LEFT JOIN review_events e ON e.attempt_id=a.id
        WHERE a.finished_at IS NOT NULL AND e.attempt_id IS NULL ORDER BY a.finished_at,a.id""").fetchall()
    for a in rows:
        prev=c.execute("SELECT * FROM review_schedule WHERE item_id=?",(a["item_id"],)).fetchone()
        # Older historical records can appear after an import. Rebuild the
        # affected item deterministically rather than applying out of order.
        if prev:
            last=c.execute("SELECT finished_at,id FROM training_attempts WHERE id=?",(prev["last_attempt_id"],)).fetchone()
            if (a["finished_at"],a["id"])<(last["finished_at"],last["id"]):
                c.execute("DELETE FROM review_events WHERE item_id=?",(a["item_id"],))
                c.execute("DELETE FROM review_schedule WHERE item_id=?",(a["item_id"],))
                return sync_reviews(c)
        x=next_review(prev,a["correct"],a["hints"],a["elapsed_ms"],datetime.fromisoformat(a["finished_at"]),a["source"])
        c.execute("""INSERT INTO review_schedule(item_id,version,due_at,interval_days,difficulty,repetitions,last_attempt_id)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET version=excluded.version,due_at=excluded.due_at,
            interval_days=excluded.interval_days,difficulty=excluded.difficulty,repetitions=excluded.repetitions,last_attempt_id=excluded.last_attempt_id""",
            (a["item_id"],x["version"],x["due_at"],x["interval_days"],x["difficulty"],x["repetitions"],a["id"]))
        c.execute("INSERT INTO review_events VALUES(?,?)",(a["id"],a["item_id"]))

@router.get("/due")
def due(source:str|None=None,limit:int=Query(100,ge=1,le=500)):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");sync_reviews(c)
        rows=c.execute("""SELECT t.id,t.source,t.kind,t.themes,s.due_at,s.interval_days,s.difficulty,s.repetitions
            FROM training_items t LEFT JOIN review_schedule s ON s.item_id=t.id
            WHERE t.active=1 AND (? IS NULL OR t.source=?) AND (s.due_at IS NULL OR s.due_at<=?)
            ORDER BY s.due_at IS NULL,s.due_at,t.id LIMIT ?""",(source,source,now(),limit)).fetchall()
        return [{**dict(r),"themes":json.loads(r["themes"]),"new":r["due_at"] is None} for r in rows]

class Collection(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    item_ids:list[int]=Field(min_length=1,max_length=1000)
    order_mode:str=Field(default="fixed",pattern="^(fixed|shuffle)$")

@router.post("/collections")
def create_collection(body:Collection):
    if len(set(body.item_ids))!=len(body.item_ids):raise HTTPException(422,"Exercícios duplicados")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for item in body.item_ids:
            if not c.execute("SELECT 1 FROM training_items WHERE id=? AND active=1",(item,)).fetchone():raise HTTPException(422,"Exercício indisponível: "+str(item))
        cid=c.execute("INSERT INTO wood_collections(name,created_at,order_mode,item_ids) VALUES(?,?,?,?)",(body.name,now(),body.order_mode,json.dumps(body.item_ids))).lastrowid
        return {"id":cid}

@router.get("/collections")
def collections():
    with connect() as c:
        return [{**dict(r),"item_ids":json.loads(r["item_ids"])} for r in c.execute("SELECT * FROM wood_collections ORDER BY id")]

def cycle_state(c,cycle_id):
    s=c.execute("SELECT * FROM wood_cycles WHERE id=?",(cycle_id,)).fetchone()
    if not s:raise HTTPException(404,"Ciclo não encontrado")
    rows=c.execute("""SELECT w.*,a.status,a.correct,a.hints,a.elapsed_ms,t.source,t.kind,t.active
        FROM wood_cycle_items w JOIN training_items t ON t.id=w.item_id
        LEFT JOIN training_attempts a ON a.id=w.attempt_id WHERE w.cycle_id=? ORDER BY w.ordinal""",(cycle_id,)).fetchall()
    finished=[r for r in rows if r["status"] in ("passed","failed","abandoned")]
    correct=sum(r["correct"]==1 and r["hints"]==0 for r in finished)
    elapsed=sum(r["elapsed_ms"] or 0 for r in finished)
    metrics={"total":len(rows),"finished":len(finished),"unassisted_correct":correct,"accuracy":correct/len(finished) if finished else None,"elapsed_ms":elapsed,"target_ms":s["target_minutes"]*60000}
    return {**dict(s),"items":[dict(r) for r in rows],"metrics":metrics}

class Cycle(BaseModel):
    target_minutes:int=Field(default=30,ge=1,le=1440)
@router.post("/collections/{cid}/cycles")
def new_cycle(cid:int,body:Cycle):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        col=c.execute("SELECT * FROM wood_collections WHERE id=?",(cid,)).fetchone()
        if not col:raise HTTPException(404,"Coleção inexistente")
        last=c.execute("SELECT * FROM wood_cycles WHERE collection_id=? ORDER BY number DESC LIMIT 1",(cid,)).fetchone()
        if last and not last["completed_at"]:return cycle_state(c,last["id"])
        number=last["number"]+1 if last else 1
        ids=json.loads(col["item_ids"])
        # Historical collections are immutable. If a position is retired,
        # preserve it in old cycles but require an explicit replacement set.
        if any(not c.execute("SELECT 1 FROM training_items WHERE id=? AND active=1",(i,)).fetchone() for i in ids):
            raise HTTPException(409,"Coleção contém posição retirada; crie outra coleção para novos ciclos.")
        if col["order_mode"]=="shuffle":random.Random(f"{cid}:{number}").shuffle(ids)
        cycle_id=c.execute("INSERT INTO wood_cycles(collection_id,number,target_minutes,started_at) VALUES(?,?,?,?)",(cid,number,body.target_minutes,now())).lastrowid
        for i,item in enumerate(ids):c.execute("INSERT INTO wood_cycle_items(cycle_id,ordinal,item_id) VALUES(?,?,?)",(cycle_id,i,item))
        return cycle_state(c,cycle_id)

@router.get("/collections/{cid}/cycles")
def cycles(cid:int):
    with connect() as c:return [cycle_state(c,r[0]) for r in c.execute("SELECT id FROM wood_cycles WHERE collection_id=? ORDER BY number",(cid,)).fetchall()]
@router.get("/cycles/{cycle_id}")
def get_cycle(cycle_id:int):
    with connect() as c:return cycle_state(c,cycle_id)
@router.post("/cycles/{cycle_id}/items/{item_id}/start")
def start(cycle_id:int,item_id:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT w.*,s.completed_at FROM wood_cycle_items w JOIN wood_cycles s ON s.id=w.cycle_id WHERE w.cycle_id=? AND w.item_id=?",(cycle_id,item_id)).fetchone()
        if not r:raise HTTPException(404,"Posição fora do ciclo")
        t=c.execute("SELECT * FROM training_items WHERE id=?",(item_id,)).fetchone()
        aid=r["attempt_id"]
        if not aid:
            if r["completed_at"]:raise HTTPException(409,"Ciclo encerrado")
            aid=c.execute("INSERT INTO training_attempts(item_id,started_at) VALUES(?,?)",(item_id,now())).lastrowid
            c.execute("UPDATE wood_cycle_items SET attempt_id=? WHERE cycle_id=? AND item_id=?",(aid,cycle_id,item_id))
        a=c.execute("SELECT * FROM training_attempts WHERE id=?",(aid,)).fetchone()
        return state(a,t)

@router.post("/cycles/{cycle_id}/complete")
def complete(cycle_id:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        s=cycle_state(c,cycle_id)
        if s["metrics"]["finished"]!=s["metrics"]["total"]:raise HTTPException(409,"Conclua todas as posições")
        c.execute("UPDATE wood_cycles SET completed_at=COALESCE(completed_at,?) WHERE id=?",(now(),cycle_id))
        sync_reviews(c)
        return cycle_state(c,cycle_id)
