"""Read-only exploration of a game prefix and legal hypothetical branches."""
import chess
import chess.engine
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from typing import Literal
from chessbuilder.db import connect
from chessbuilder.engine import ENGINE_SEARCH_LOCK,Settings,executable,identity,search
from chessbuilder.review import line

router=APIRouter(prefix="/api/explore")

class Position(BaseModel):
    game_id:int=Field(ge=1)
    ply:int=Field(ge=0)
    moves:list[str]=Field(default_factory=list,max_length=128)

class Evaluate(Position):
    level:Literal["quick","deep"]="quick"
    multipv:int=Field(default=3,ge=1,le=3)

def reconstruct(body):
    with connect() as c:
        g=c.execute("SELECT initial_fen FROM games WHERE id=?",(body.game_id,)).fetchone()
        if not g:raise HTTPException(404,"Partida não encontrada")
        prefix=c.execute("SELECT ply,uci FROM moves WHERE game_id=? AND ply<=? ORDER BY ply",(body.game_id,body.ply)).fetchall()
    if len(prefix)!=body.ply or any(r["ply"]!=i for i,r in enumerate(prefix,1)):raise HTTPException(422,"Lance fora do histórico")
    b=chess.Board(g["initial_fen"])
    try:
        for m in prefix:b.push_uci(m["uci"])
    except ValueError as exc:raise HTTPException(409,"Histórico inválido") from exc
    root=b.fen()
    played=[]
    for u in body.moves:
        if b.is_game_over():raise HTTPException(422,"A posição já terminou")
        try:m=b.parse_uci(u)
        except ValueError as exc:raise HTTPException(422,"Lance ilegal: "+u[:16]) from exc
        if not m or m not in b.legal_moves:raise HTTPException(422,"Lance ilegal")
        san=b.san(m);number=b.fullmove_number;color="white" if b.turn else "black"
        b.push(m);played.append({"uci":m.uci(),"san":san,"fen":b.fen(),"move_number":number,"color":color})
    return b,root,played

def view(b,root,played):
    outcome=b.outcome()
    return {"fen":b.fen(),"root_fen":root,"line":played,"turn":"white" if b.turn else "black",
        "legal_moves":[] if outcome else [m.uci() for m in b.legal_moves],
        "check":b.is_check(),"result":outcome.result() if outcome else None,
        "termination":outcome.termination.name if outcome else None,
        "can_claim_draw":b.can_claim_draw() if not outcome else False}

@router.post("/position")
def position(body:Position):
    return view(*reconstruct(body))

@router.post("/evaluate")
def evaluate(body:Evaluate):
    b,root,played=reconstruct(body)
    state=view(b,root,played)
    if state["result"]:return {**state,"candidates":[],"engine":None,"level":body.level}
    path=executable()
    if not path:raise HTTPException(409,"Stockfish não localizado")
    if not ENGINE_SEARCH_LOCK.acquire(blocking=False):raise HTTPException(409,"Motor ocupado com análise em lote. Pause a fila ou tente novamente.")
    e=None
    try:
        e=chess.engine.SimpleEngine.popen_uci(path,timeout=10)
        s=Settings(level=body.level,threads=1,hash_mb=64,multipv=body.multipv,auto_deepen=False)
        e.configure({"Threads":s.threads,"Hash":s.hash_mb})
        if "UCI_ShowWDL" in e.options:e.configure({"UCI_ShowWDL":True})
        eid=identity(e,path)
        candidates=[]
        for score in search(e,b,s,eid,multipv=min(body.multipv,b.legal_moves.count())):
            pv=line(b,score["pv"])
            if not pv or len(pv)!=len(score["pv"]):continue
            candidates.append({**score,"line":pv})
        if not candidates:raise HTTPException(503,"Motor não retornou variante legal")
        return {**state,"engine":eid,"level":body.level,"candidates":candidates}
    except (OSError,chess.engine.EngineError,TimeoutError) as exc:raise HTTPException(503,"Motor indisponível") from exc
    finally:
        try:
            if e:
                try:e.quit()
                except Exception:e.close()
        finally:ENGINE_SEARCH_LOCK.release()
