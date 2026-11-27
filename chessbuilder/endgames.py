"""Local-first theoretical endgames and resumable play against exact or engine opposition."""
import json
import os
import threading
from pathlib import Path
import chess
import chess.engine
import chess.syzygy
import httpx
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.engine import executable,serialize
from chessbuilder.training import insert_item,load_attempt,state,finish,reconstruct

router=APIRouter(prefix="/api/endgames")
LOCK=threading.Lock()
VERSION="endgame-v1"
# Original illustrative positions; no third-party book text or diagram is reproduced.
# Targets are training objectives, not asserted tablebase verdicts.
CATALOG=[
 ("queen","Mate de dama","7k/8/8/8/8/4K3/3Q4/8 w - - 0 1","win",1,"Restrinja o rei adversário e aproxime o seu rei; evite afogamento."),
 ("rook","Mate de torre","7k/8/8/8/8/4K3/3R4/8 w - - 0 1","win",1,"Use a torre para cortar o rei e o próprio rei para reduzir a área."),
 ("square","Regra do quadrado","7k/8/8/2P5/8/8/8/K7 w - - 0 1","win",1,"Compare a distância do rei defensor à casa de promoção."),
 ("opposition","Oposição e casas-chave","8/8/4k3/8/4K3/4P3/8/8 w - - 0 1","draw",2,"Identifique a oposição e as casas de entrada antes de escolher o lance."),
 ("zugzwang","Zugzwang de peões","8/8/4k3/8/4K3/4P3/8/8 b - - 0 1","draw",2,"Observe como a obrigação de mover muda as casas disponíveis."),
 ("lucena","Estrutura de Lucena","1K6/1P1k4/8/8/8/8/r7/7R w - - 0 1","win",4,"Procure cortar o rei defensor e construir uma ponte contra os xeques laterais."),
 ("philidor","Estrutura de Philidor","4k3/8/7r/4K3/4P3/8/8/R7 b - - 0 1","draw",4,"A torre defensora controla a sexta fileira; depois, considere xeques por trás."),
 ("bishops","Bispos de cores opostas","4k3/8/3b4/4P3/3B4/4K3/8/8 b - - 0 1","draw",3,"Bloqueie o avanço do peão e evite entregar casas de entrada."),
 ("knight","Cavalo e peão","7k/8/4K3/4P3/8/3N4/8/8 w - - 0 1","win",3,"Coordene rei e cavalo para apoiar a promoção."),
 ("mixed","Torre contra peão","8/4k3/8/8/4p3/8/4K3/R7 w - - 0 1","win",3,"Avalie se a torre pode conter o peão sem abandonar o rei."),
]
def catalog_board(entry,variant):
    b=chess.Board(entry[2])
    if variant%2:b=b.transform(chess.flip_horizontal)
    if variant>=2:b=b.mirror()
    if not b.is_valid():raise RuntimeError("Invalid curated position: "+entry[0])
    return b

def exact_value(board,allow_remote=False):
    # Outcome is always from the side to move. A claimable draw is
    # automatically accepted by the training opponent.
    terminal=board.outcome(claim_draw=True)
    if terminal:return {"wdl":0 if terminal.winner is None else 2 if terminal.winner==board.turn else -2,"dtz":0,"effective":0 if terminal.winner is None else 1 if terminal.winner==board.turn else -1,"source":"rules"}
    if len(board.piece_map())>7 or board.castling_rights:return None
    wdl=dtz=None;source=None
    folder=os.environ.get("CHESSBUILDER_SYZYGY_DIR")
    if folder and Path(folder).is_dir():
        try:
            with chess.syzygy.open_tablebase(folder) as tb:
                wdl=tb.probe_wdl(board);source="syzygy"
                try:dtz=tb.probe_dtz(board)
                except KeyError:pass
        except (KeyError,OSError,ValueError):pass
    if wdl is None and allow_remote:
        try:
            r=httpx.get("https://tablebase.lichess.ovh/standard",params={"fen":board.fen()},timeout=5)
            r.raise_for_status();data=r.json()
            wdl={"win":2,"cursed-win":1,"draw":0,"blessed-loss":-1,"loss":-2}.get(data.get("category"))
            dtz=data.get("dtz");source="lichess"
        except (httpx.HTTPError,ValueError,TypeError):pass
    if wdl is None:return None
    # WDL ±1 is a 50-move draw. Without sufficient DTZ evidence at a
    # nonzero clock, do not certify a win/loss near the claim boundary.
    effective=0 if abs(wdl)<2 else (1 if wdl>0 else -1)
    if abs(wdl)==2 and board.halfmove_clock and (dtz is None or board.halfmove_clock+abs(dtz)>100):effective=None
    return {"wdl":wdl,"dtz":dtz,"effective":effective,"source":source}

def engine_move(board):
    path=executable()
    if not path:raise HTTPException(409,"Instale Stockfish ou configure Syzygy para continuar.")
    e=None
    try:
        e=chess.engine.SimpleEngine.popen_uci(path,timeout=10)
        e.configure({"Threads":1,"Hash":32})
        result=e.play(board,chess.engine.Limit(nodes=80000,time=1.5))
        if result.move not in board.legal_moves:raise HTTPException(503,"Motor não retornou lance legal.")
        return result.move
    except (OSError,chess.engine.EngineError,TimeoutError) as exc:raise HTTPException(503,"Stockfish indisponível.") from exc
    finally:
        if e:
            try:e.quit()
            except Exception:e.close()

def opponent_move(board,remote=False):
    # Only use tablebase ranking if every legal child has a known result.
    # Remote lookup is bounded to a single root request for the opponent.
    if remote and len(board.piece_map())<=7 and not board.castling_rights:
        try:
            r=httpx.get("https://tablebase.lichess.ovh/standard",params={"fen":board.fen()},timeout=5);r.raise_for_status()
            for row in r.json().get("moves",[]):
                m=chess.Move.from_uci(row["uci"])
                if m in board.legal_moves:return m
        except (httpx.HTTPError,ValueError,KeyError,TypeError):pass
    moves=[]
    if os.environ.get("CHESSBUILDER_SYZYGY_DIR"):
        for m in board.legal_moves:
            child=board.copy();child.push(m);v=exact_value(child)
            if not v or v["effective"] is None:break
            rank=-v["effective"]
            dtz=abs(v["dtz"]) if v["dtz"] is not None else 1000
            moves.append((rank,-dtz if rank>0 else dtz if rank<0 else -dtz,m.uci(),m))
        else:
            if moves:return max(moves,key=lambda x:x[:3])[3]
    return engine_move(board)

@router.get("/catalog")
def catalog():
    return [{"key":e[0],"title":e[1],"fen":e[2],"goal":e[3],"difficulty":e[4],"theory":e[5],"variants":4,"provenance":"Original illustrative position; objective checked by tablebase when available."} for e in CATALOG]

class Start(BaseModel):
    key:str
    variant:int=Field(default=0,ge=0,le=3)
    remote:bool=False

@router.post("/prepare")
def prepare(body:Start):
    e=next((x for x in CATALOG if x[0]==body.key),None)
    if not e:raise HTTPException(404,"Final não encontrado")
    b=catalog_board(e,body.variant);v=exact_value(b,body.remote)
    goal=e[3]
    if v and v["effective"] is not None:
        goal="win" if v["effective"]==1 else "draw" if v["effective"]==0 else "survive"
    key=f"{VERSION}:{e[0]}:{body.variant}:{int(body.remote)}:{goal}"
    evidence={"title":e[1],"theory":e[5],"goal":goal,"remote":body.remote,"initial_value":v,"catalog_key":e[0]}
    with connect() as c:
        insert_item(c,"endgame",None,None,None,b,"endgame_"+goal,[e[0]],[],evidence,e[4],key)
        row=c.execute("SELECT id FROM training_items WHERE item_key=?",(key,)).fetchone()
    return {"item_id":row[0],"objective":goal,"evaluation":v}

def play_answer(aid,body):
    # Read and perform potentially slow searches outside the database write lock.
    with connect() as c:
        a,t=load_attempt(c,aid);a,t=dict(a),dict(t)
    if t["source"]!="endgame" or a["status"]!="active":raise HTTPException(409,"Tentativa encerrada ou tipo incorreto")
    answers=json.loads(a["answers"])
    if len(answers)!=body.expected_ply:raise HTTPException(409,"Posição mudou")
    ev=json.loads(t["evidence"]);b=reconstruct(t["initial_fen"],json.loads(t["history"])+answers);user=b.turn
    try:m=chess.Move.from_uci(body.uci)
    except ValueError:raise HTTPException(422,"Lance inválido")
    if m not in b.legal_moves:raise HTTPException(422,"Lance ilegal")
    if not LOCK.acquire(blocking=False):raise HTTPException(409,"Outra análise em andamento")
    try:
        b.push(m);answers.append(m.uci())
        v=exact_value(b,ev["remote"])
        if not b.outcome(claim_draw=True):
            reply=opponent_move(b,ev["remote"]);b.push(reply);answers.append(reply.uci())
        outcome=b.outcome(claim_draw=True)
    finally:LOCK.release()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");current,t=load_attempt(c,aid)
        if current["status"]!="active" or current["answers"]!=a["answers"]:raise HTTPException(409,"Posição mudou")
        if outcome:
            win=outcome.winner==user;draw=outcome.winner is None
            passed=win or (draw and ev["goal"]!="win")
            finish(c,current,"passed" if passed else "failed",answers)
        else:c.execute("UPDATE training_attempts SET answers=? WHERE id=?",(json.dumps(answers),aid))
        current,t=load_attempt(c,aid);result=state(current,t)
    result["tablebase_after_move"]=v
    if outcome:result["result"]=outcome.result()
    return result

def endgame_hint(aid):
    with connect() as c:
        a,t=load_attempt(c,aid);a,t=dict(a),dict(t)
    if a["status"]!="active":raise HTTPException(409,"Tentativa encerrada")
    b=reconstruct(t["initial_fen"],json.loads(t["history"])+json.loads(a["answers"]))
    if not LOCK.acquire(blocking=False):raise HTTPException(409,"Outra análise em andamento")
    try:m=opponent_move(b,json.loads(t["evidence"])["remote"])
    finally:LOCK.release()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");current,_=load_attempt(c,aid)
        if current["status"]!="active" or current["answers"]!=a["answers"]:raise HTTPException(409,"Posição mudou")
        n=current["hints"]+1;c.execute("UPDATE training_attempts SET hints=? WHERE id=?",(n,aid))
    return {"hint":"Considere "+b.san(m) if n>1 else "Considere a peça em "+chess.square_name(m.from_square),"hints":n}
