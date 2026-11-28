import hashlib
from datetime import date
from typing import Literal
import json
import math
import os
import shutil
import threading
from pathlib import Path
import chess
import chess.engine
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from chessbuilder.db import connect
from chessbuilder.importer import context_key

router=APIRouter(prefix="/api")
LEVELS={"quick":12000,"deep":120000,"detailed":350000}
ANALYSIS_PROTOCOL=2
class Settings(BaseModel):
    level:str="quick"
    threads:int=Field(default=2,ge=1,le=8)
    hash_mb:int=Field(default=128,ge=16,le=1024)
    multipv:int=Field(default=2,ge=1,le=5)
    auto_deepen:bool=True

def settings_json(s):
    if s.level not in LEVELS: raise HTTPException(422,"Nível inválido")
    return json.dumps(s.model_dump(),sort_keys=True)

def executable():
    return os.environ.get("CHESSBUILDER_STOCKFISH") or shutil.which("stockfish") or ("/usr/games/stockfish" if Path("/usr/games/stockfish").is_file() else None)

def temperatures():
    values=[]
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            value=float(path.read_text())/1000
            if 0<value<150: values.append(value)
        except (OSError,ValueError): pass
    return values

def identity(engine,path):
    with open(path,"rb") as binary:
        digest=hashlib.file_digest(binary,"sha256").hexdigest()
    return {"name":engine.id.get("name","Stockfish"),"binary_sha256":digest,"analysis_protocol":ANALYSIS_PROTOCOL}

def serialize(info,turn):
    score=info["score"].pov(turn)
    wdl=info.get("wdl")
    return {"cp":score.score(),"mate":score.mate(),"pv":[m.uci() for m in info.get("pv",[])],"depth":info.get("depth"),"nodes":info.get("nodes"),"wdl":list(wdl.pov(turn)) if wdl else None}

def search(engine,board,settings,engine_id,root=None,multipv=1):
    key=hashlib.sha256(json.dumps([ANALYSIS_PROTOCOL,context_key(board),board.fen(),engine_id,settings.model_dump(),root.uci() if root else None,multipv],sort_keys=True).encode()).hexdigest()
    with connect() as c:
        row=c.execute("SELECT payload FROM evaluations WHERE cache_key=?",(key,)).fetchone()
        if row: return json.loads(row[0])
    infos=engine.analyse(board,chess.engine.Limit(nodes=LEVELS[settings.level]),multipv=multipv,root_moves=[root] if root else None)
    result=[serialize(i,board.turn) for i in infos]
    with connect() as c: c.execute("INSERT OR REPLACE INTO evaluations VALUES(?,?)",(key,json.dumps(result)))
    return result

def search_move(engine,board,move,settings,engine_id):
    best=search(engine,board,settings,engine_id)[0]
    played=best if best["pv"] and best["pv"][0]==move.uci() else search(engine,board,settings,engine_id,root=move)[0]
    alternatives=search(engine,board,settings,engine_id,multipv=min(settings.multipv,board.legal_moves.count())) if settings.multipv>1 else [best]
    return {"engine":engine_id,"settings":settings.model_dump(),"perspective":"white" if board.turn else "black","best":best,"played":played,"alternatives":alternatives}

def estimated_chance(score):
    mate=score.get("mate")
    if mate is not None: return 1.0 if mate>0 else 0.0
    cp=score.get("cp")
    return None if cp is None else 1/(1+math.exp(-0.00368208*max(-10000,min(10000,cp))))

def analyse_move(engine,board,move,settings,engine_id):
    quick=search_move(engine,board,move,settings,engine_id)
    if settings.level!="quick" or not settings.auto_deepen: return quick
    best,played=quick["best"],quick["played"]
    before,after=estimated_chance(best),estimated_chance(played)
    if before is not None and after is not None and before-after>=0.10:
        result=search_move(engine,board,move,settings.model_copy(update={"level":"deep"}),engine_id)
        result["refined_from"]="quick"
        return result
    return quick

def consistent_checkpoint(job,engine_id):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        rows=c.execute("SELECT payload FROM move_analysis WHERE job_id=?",(job["id"],)).fetchall()
        if any(json.loads(r[0]).get("engine")!=engine_id for r in rows):
            c.execute("DELETE FROM move_analysis WHERE job_id=?",(job["id"],))
            c.execute("UPDATE jobs SET next_ply=1 WHERE id=?",(job["id"],))
            return 1
        return job["next_ply"]

class Worker:
    def __init__(self):
        self.shutdown=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True)
    def start(self):
        with connect() as c: c.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        self.thread.start()
    def stop(self):
        self.shutdown.set()
        self.thread.join(timeout=20)
    def claim(self):
        with connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT j.* FROM jobs j JOIN games g ON g.id=j.game_id WHERE j.status='queued' ORDER BY j.priority DESC,g.played_at DESC,j.id LIMIT 1").fetchone()
            if row: c.execute("UPDATE jobs SET status='running',error=NULL WHERE id=?",(row["id"],))
            return dict(row) if row else None
    def run(self):
        while not self.shutdown.is_set():
            path=executable()
            if not path:
                self.shutdown.wait(2);continue
            job=self.claim()
            if not job:
                self.shutdown.wait(0.5);continue
            try:
                self.process(job,path)
            except Exception as exc:
                with connect() as c: c.execute("UPDATE jobs SET status='failed',error=? WHERE id=? AND status='running'",(str(exc)[:1000],job["id"]))
    def process(self,job,path):
        s=Settings.model_validate_json(job["settings"])
        with connect() as c:
            game=c.execute("SELECT initial_fen FROM games WHERE id=?",(job["game_id"],)).fetchone()
            moves=c.execute("SELECT ply,uci FROM moves WHERE game_id=? ORDER BY ply",(job["game_id"],)).fetchall()
        board=chess.Board(game["initial_fen"])
        engine=chess.engine.SimpleEngine.popen_uci(path,timeout=15)
        try:
            engine.configure({"Threads":s.threads,"Hash":s.hash_mb})
            if "UCI_ShowWDL" in engine.options: engine.configure({"UCI_ShowWDL":True})
            eid=identity(engine,path)
            next_ply=consistent_checkpoint(job,eid)
            for row in moves:
                move=chess.Move.from_uci(row["uci"])
                if row["ply"]>=next_ply:
                    with connect() as c: status=c.execute("SELECT status FROM jobs WHERE id=?",(job["id"],)).fetchone()[0]
                    if self.shutdown.is_set() or status!="running": return
                    result=analyse_move(engine,board,move,s,eid)
                    with connect() as c:
                        c.execute("BEGIN IMMEDIATE")
                        if c.execute("SELECT status FROM jobs WHERE id=?",(job["id"],)).fetchone()[0]!="running": return
                        c.execute("INSERT OR REPLACE INTO move_analysis VALUES(?,?,?)",(job["id"],row["ply"],json.dumps(result)))
                        c.execute("UPDATE jobs SET next_ply=? WHERE id=?",(row["ply"]+1,job["id"]))
                board.push(move)
            with connect() as c: c.execute("UPDATE jobs SET status='done' WHERE id=? AND status='running'",(job["id"],))
        finally:
            try: engine.quit()
            except Exception: engine.close()
            if self.shutdown.is_set():
                with connect() as c: c.execute("UPDATE jobs SET status='queued' WHERE id=? AND status='running'",(job["id"],))

class Scope(BaseModel):
    account_id:int|None=Field(default=None,ge=1)
    source:str|None=Field(default=None,max_length=32)
    speed:str|None=Field(default=None,max_length=32)
    start:date|None=None
    end:date|None=None
    @model_validator(mode="after")
    def dates(self):
        if self.start and self.end and self.start>self.end:raise ValueError("Período inválido")
        return self

class QueueRequest(Settings,Scope):
    game_id:int|None=Field(default=None,ge=1)
    limit:int=Field(default=100,ge=1,le=10000)
    order:Literal["recent","oldest"]="recent"
    include_interrupted:bool=True
    skip_completed:bool=True

def scope_where(body,alias="g"):
    clauses=[];args=[]
    for key in ("account_id","source","speed"):
        value=getattr(body,key)
        if value is not None:
            clauses.append(f"EXISTS(SELECT 1 FROM game_accounts ga WHERE ga.game_id={alias}.id AND ga.account_id=?)" if key=="account_id" else f"{alias}.{key}=?");args.append(value)
    if body.start:
        clauses.append(f"{alias}.played_at>=?");args.append(body.start.isoformat())
    if body.end:
        clauses.append(f"substr({alias}.played_at,1,10)<=?");args.append(body.end.isoformat())
    return clauses,args

def selection(c,body,settings):
    clauses,args=scope_where(body)
    if body.game_id is not None:
        if not c.execute("SELECT 1 FROM games WHERE id=?",(body.game_id,)).fetchone():raise HTTPException(404,"Partida não encontrada")
        clauses.append("g.id=?");args.append(body.game_id)
    elif body.source is None:
        clauses.append("g.source!='historical'")
    where=" AND ".join(clauses) or "1=1"
    matched=c.execute("SELECT COUNT(*) FROM games g WHERE "+where,args).fetchone()[0]
    # A game is eligible only if the exact configuration has no job or an
    # interrupted job can be resumed. A different active job is never duplicated.
    eligible=where+""" AND EXISTS(SELECT 1 FROM moves m WHERE m.game_id=g.id)
      AND (j.id IS NULL OR (j.status IN ('paused','failed','cancelled') AND ?))
      AND NOT EXISTS(SELECT 1 FROM jobs active WHERE active.game_id=g.id AND active.status IN ('queued','running'))"""
    parameters=[settings,*args,int(body.include_interrupted)]
    if body.skip_completed and body.game_id is None:
        eligible+=" AND NOT EXISTS(SELECT 1 FROM jobs complete WHERE complete.game_id=g.id AND complete.status='done')"
    join=" FROM games g LEFT JOIN jobs j ON j.game_id=g.id AND j.settings=? WHERE "+eligible
    count=c.execute("SELECT COUNT(*)"+join,parameters).fetchone()[0]
    direction="DESC" if body.order=="recent" else "ASC"
    rows=c.execute("SELECT g.id,g.white,g.black,g.played_at,g.speed,g.source,j.id AS job_id,j.status AS job_status"+join+
        f" ORDER BY g.played_at IS NULL,g.played_at {direction},g.id {direction} LIMIT ?",(*parameters,1 if body.game_id is not None else body.limit)).fetchall()
    return matched,count,rows

def selection_summary(matched,count,rows):
    resumed=sum(r["job_id"] is not None for r in rows)
    return {"matched":matched,"eligible":count,"selected":len(rows),"new":len(rows)-resumed,"resumable":resumed,
            "remaining":max(0,count-len(rows)),"sample":[{k:r[k] for k in ("id","white","black","played_at","speed","source","job_status")} for r in rows[:10]]}

@router.post("/analysis/preview")
def preview(body:QueueRequest):
    settings=settings_json(Settings.model_validate(body.model_dump()))
    with connect() as c:
        c.execute("BEGIN")
        return selection_summary(*selection(c,body,settings))

@router.post("/analysis/queue")
def enqueue(body:QueueRequest):
    settings=settings_json(Settings.model_validate(body.model_dump()))
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        matched,count,rows=selection(c,body,settings)
        summary=selection_summary(matched,count,rows)
        queued=resumed=0
        for r in rows:
            if r["job_id"] is None:
                c.execute("INSERT INTO jobs(game_id,settings,status,priority) VALUES(?,?,'queued',?)",(r["id"],settings,1 if body.game_id is not None else 0))
                queued+=1
            else:
                c.execute("UPDATE jobs SET status='queued',error=NULL,priority=? WHERE id=?",(1 if body.game_id is not None else 0,r["job_id"]))
                resumed+=1
        # Keep an already queued explicit per-game request prioritized.
        if body.game_id is not None:
            c.execute("UPDATE jobs SET priority=1 WHERE game_id=? AND settings=? AND status='queued'",(body.game_id,settings))
        return {**summary,"queued":queued,"resumed":resumed}

class BulkRequest(Scope):
    action:Literal["pause","cancel"]
    all_jobs:bool=False
@router.post("/analysis/bulk")
def bulk_control(body:BulkRequest):
    clauses,args=scope_where(body)
    if not clauses and not body.all_jobs:raise HTTPException(422,"Confirme o controle global da fila")
    if body.all_jobs and clauses:raise HTTPException(422,"Escolha escopo global ou filtros")
    target="paused" if body.action=="pause" else "cancelled"
    statuses="('queued','running')" if body.action=="pause" else "('queued','running','paused','failed')"
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(f"UPDATE jobs SET status=?,error=NULL WHERE status IN {statuses} AND game_id IN (SELECT g.id FROM games g WHERE "+(" AND ".join(clauses) or "1=1")+")",(target,*args))
        return {"changed":c.execute("SELECT changes()").fetchone()[0],"status":target}

@router.get("/analysis/jobs")
def jobs(status:Literal["queued","running","paused","done","failed","cancelled"]|None=None,account_id:int|None=None,
         limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0)):
    clauses,args=scope_where(Scope(account_id=account_id))
    if status:clauses.append("j.status=?");args.append(status)
    where=" WHERE "+" AND ".join(clauses) if clauses else ""
    join=" FROM jobs j JOIN games g ON g.id=j.game_id"
    with connect() as c:
        c.execute("BEGIN")
        total=c.execute("SELECT COUNT(*)"+join+where,args).fetchone()[0]
        rows=c.execute("SELECT j.*,g.white,g.black,g.account_id,g.source,g.speed,g.played_at,(SELECT COUNT(*) FROM moves m WHERE m.game_id=j.game_id) AS total_plies"+join+where+
            " ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'paused' THEN 2 WHEN 'failed' THEN 3 ELSE 4 END,j.priority DESC,g.played_at DESC,j.id DESC LIMIT ? OFFSET ?",(*args,limit,offset)).fetchall()
        counts={r[0]:r[1] for r in c.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status")}
        return {"engine_available":bool(executable()),"temperatures_c":temperatures(),"counts":counts,"total":total,"offset":offset,"limit":limit,"jobs":[dict(r) for r in rows]}

@router.post("/analysis/jobs/{job_id}/{action}")
def control(job_id:int,action:str):
    transitions={"pause":("paused",("queued","running")),"resume":("queued",("paused","failed","cancelled")),"cancel":("cancelled",("queued","running","paused","failed"))}
    if action not in transitions: raise HTTPException(422,"Ação inválida")
    target,allowed=transitions[action]
    with connect() as c:
        row=c.execute("SELECT status FROM jobs WHERE id=?",(job_id,)).fetchone()
        if not row: raise HTTPException(404,"Job não encontrado")
        if row[0] not in allowed: raise HTTPException(409,"Transição inválida")
        c.execute("UPDATE jobs SET status=?,error=NULL WHERE id=? AND status=?",(target,job_id,row[0]))
    return {"status":target}
