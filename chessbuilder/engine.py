import hashlib
import json
import math
import os
import shutil
import threading
from pathlib import Path
import chess
import chess.engine
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
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

class QueueRequest(Settings):
    game_id:int|None=None

@router.post("/analysis/queue")
def enqueue(body:QueueRequest):
    settings=settings_json(Settings.model_validate(body.model_dump()))
    with connect() as c:
        if body.game_id is not None and not c.execute("SELECT 1 FROM games WHERE id=?",(body.game_id,)).fetchone(): raise HTTPException(404,"Partida não encontrada")
        c.execute("INSERT OR IGNORE INTO jobs(game_id,settings,status,priority) SELECT id,?,'queued',? FROM games WHERE (? IS NULL OR id=?) AND (? IS NOT NULL OR source!='historical')",(settings,int(body.game_id is not None),body.game_id,body.game_id,body.game_id))
        queued=c.execute("SELECT changes()").fetchone()[0]
        if body.game_id is not None:
            c.execute("UPDATE jobs SET priority=1 WHERE game_id=? AND settings=? AND status='queued'",(body.game_id,settings))
        return {"queued":queued}

@router.get("/analysis/jobs")
def jobs():
    with connect() as c:
        rows=c.execute("SELECT j.*,g.white,g.black,(SELECT COUNT(*) FROM moves m WHERE m.game_id=j.game_id) AS total_plies FROM jobs j JOIN games g ON g.id=j.game_id ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,j.id DESC LIMIT 200").fetchall()
        counts={r[0]:r[1] for r in c.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status")}
        return {"engine_available":bool(executable()),"temperatures_c":temperatures(),"counts":counts,"jobs":[dict(r) for r in rows]}

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
