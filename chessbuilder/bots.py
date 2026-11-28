"""Resumable local bot matches with bounded UCI candidate sampling."""
import io
import json
import math
import random
import threading
from datetime import datetime,timezone
import chess
import chess.engine
import chess.pgn
from fastapi import APIRouter,HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.engine import executable,serialize
from chessbuilder.strategy import features
from chessbuilder.training import now
from chessbuilder.importer import store

router=APIRouter(prefix="/api/bots")
LOCK=threading.Lock()
PROFILES=("universal","aggressive","solid","prophylactic","mirror","nemesis","explorer")
VERSION="bot-policy-v1"
class Settings(BaseModel):
    profile:str=Field(default="universal",pattern="^(universal|aggressive|solid|prophylactic|mirror|nemesis|explorer)$")
    strength:int=Field(default=1600,ge=800,le=2400)
    human_color:str=Field(default="white",pattern="^(white|black)$")
    minutes:int=Field(default=10,ge=1,le=120)
    increment:int=Field(default=5,ge=0,le=60)
    seed:int=Field(default=42,ge=0,le=2147483647)
    fen:str=chess.STARTING_FEN
def timestamp():return datetime.now(timezone.utc)
def human(s):return s["human_color"]=="white"
def color_name(color):return "white" if color else "black"
def board_for(g):
    b=chess.Board(g["initial_fen"])
    for m in json.loads(g["moves"]):b.push_uci(m["uci"])
    return b
def get_row(c,gid):
    g=c.execute("SELECT * FROM bot_games WHERE id=?",(gid,)).fetchone()
    if not g:raise HTTPException(404,"Partida inexistente")
    return dict(g)
def time_left(g,b,at):
    clocks=json.loads(g["clocks"])
    if g["status"]=="running":
        side=color_name(b.turn);elapsed=max(0,(at-datetime.fromisoformat(g["turn_started"])).total_seconds())
        clocks[side]=max(0,clocks[side]-elapsed)
    return clocks
def finish_if_flag(c,g,b,at):
    clocks=time_left(g,b,at)
    if g["status"]=="running" and clocks[color_name(b.turn)]<=0:
        winner=not b.turn;result="1/2-1/2" if b.has_insufficient_material(winner) else "1-0" if winner else "0-1"
        c.execute("UPDATE bot_games SET clocks=?,status='finished',result=?,reason='timeout',revision=revision+1 WHERE id=?",(json.dumps(clocks),result,g["id"]))
        return get_row(c,g["id"])
    return g
def view(g):
    b=board_for(g);s=json.loads(g["settings"]);clock=time_left(g,b,timestamp())
    return {"id":g["id"],"settings":s,"fen":b.fen(),"moves":json.loads(g["moves"]),"clocks":clock,"status":g["status"],"result":g["result"],"reason":g["reason"],"revision":g["revision"],"turn":color_name(b.turn),"human_turn":b.turn==human(s),"legal_moves":[m.uci() for m in b.legal_moves] if g["status"]=="running" and b.turn==human(s) else [],"linked_game_id":g["linked_game_id"],"strength_calibrated":False}

def position_key(b):return " ".join(b.fen(en_passant="legal").split()[:4])
def observed_book(board,profile):
    # Bounded sample of personal opening games. Avoid assuming style from a
    # single match or leaking an unrelated player's data.
    if profile not in ("mirror","nemesis","explorer") or board.fullmove_number>18:return {},0
    with connect() as c:
        games=c.execute("SELECT id,player_color,initial_fen FROM games WHERE player_color IS NOT NULL AND source IN ('chess.com','lichess','pgn') ORDER BY played_at DESC,id DESC LIMIT 200").fetchall()
        counts={};support=0;target=position_key(board)
        for g in games:
            b=chess.Board(g["initial_fen"])
            for row in c.execute("SELECT uci FROM moves WHERE game_id=? AND ply<=36 ORDER BY ply",(g["id"],)):
                ours=(color_name(b.turn)==g["player_color"])
                if position_key(b)==target and ((profile=="mirror" and ours) or (profile!="mirror" and not ours)):
                    u=row["uci"];counts[u]=counts.get(u,0)+1;support+=1;break
                b.push_uci(row["uci"])
    return (counts,support) if support>=3 else ({},support)

def score_cp(score):
    if score.get("mate") is not None:return (10000-abs(score["mate"])*20)*(1 if score["mate"]>0 else -1)
    return score.get("cp") or 0

def choose_candidate(board,scores,settings,seed,book=None):
    """Seeded policy given a fixed set of legal UCI candidates."""
    valid=[x for x in scores if x.get("pv") and chess.Move.from_uci(x["pv"][0]) in board.legal_moves]
    if not valid:raise HTTPException(503,"Nenhum candidato legal")
    valid.sort(key=lambda x:(-score_cp(x),x["pv"][0]))
    best=score_cp(valid[0]);strength=settings["strength"];profile=settings["profile"]
    allowance=40+(2400-strength)*.23
    pool=[x for x in valid if best-score_cp(x)<=allowance]
    rng=random.Random(seed)
    weights=[];before=features(board,board.turn);book=book or {}
    for x in pool:
        m=chess.Move.from_uci(x["pv"][0]);after=board.copy();after.push(m);f=features(after,board.turn)
        style=0.0
        if profile=="aggressive":style=0.45*board.gives_check(m)+0.25*board.is_capture(m)+0.04*(f["activity"]-before["activity"])
        elif profile=="solid":style=0.15*(f["pawn_structure"]-before["pawn_structure"])+0.2*(f["king_safety"]-before["king_safety"])
        elif profile=="prophylactic":style=.06*(f["prophylaxis"]-before["prophylaxis"])+.12*(f["king_safety"]-before["king_safety"])
        elif profile=="explorer":style=.08*(f["activity"]-before["activity"])+.12*(f["weak_squares"]-before["weak_squares"])
        if book:style+=math.log1p(book.get(m.uci(),0))
        weights.append(math.exp(max(-30,min(8,-(best-score_cp(x))/max(15,allowance/2)+style))))
    return chess.Move.from_uci(rng.choices(pool,weights=weights,k=1)[0]["pv"][0])
def motor_candidates(board,settings,remaining):
    path=executable()
    if not path:raise HTTPException(409,"Stockfish não configurado")
    e=None
    try:
        e=chess.engine.SimpleEngine.popen_uci(path,timeout=8);e.configure({"Threads":1,"Hash":32})
        nodes=int(4000+((settings["strength"]-800)/1600)**2*160000)
        result=e.analyse(board,chess.engine.Limit(nodes=nodes,time=max(.02,min(1.5,remaining))),multipv=min(5,board.legal_moves.count()))
        return [serialize(x,board.turn) for x in result]
    except (OSError,chess.engine.EngineError,TimeoutError) as exc:raise HTTPException(503,"Stockfish indisponível") from exc
    finally:
        if e:
            try:e.quit()
            except Exception:e.close()

@router.get("/profiles")
def profiles():return {"version":VERSION,"profiles":list(PROFILES),"calibrated":False,"strength_note":"Parâmetro nominal do motor; não é rating FIDE ou Chess.com validado."}
@router.post("/games")
def create(body:Settings):
    try:b=chess.Board(body.fen)
    except ValueError:raise HTTPException(422,"FEN inválida")
    if not b.is_valid() or b.is_game_over(claim_draw=True):raise HTTPException(422,"Posição inválida ou encerrada")
    stamp=now()
    with connect() as c:
        gid=c.execute("INSERT INTO bot_games(settings,initial_fen,clocks,started_at,turn_started) VALUES(?,?,?,?,?)",(body.model_dump_json(),b.fen(),json.dumps({"white":body.minutes*60,"black":body.minutes*60}),stamp,stamp)).lastrowid
        return view(get_row(c,gid))
@router.get("/games")
def listing():
    with connect() as c:return [view(dict(r)) for r in c.execute("SELECT * FROM bot_games ORDER BY id DESC LIMIT 100")]
@router.get("/games/{gid}")
def detail(gid:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");g=get_row(c,gid);return view(finish_if_flag(c,g,board_for(g),timestamp()))

class Command(BaseModel):
    revision:int=Field(ge=0)
    uci:str|None=None

def commit_move(gid,revision,move,metadata=None):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");g=get_row(c,gid);b=board_for(g);at=timestamp()
        g=finish_if_flag(c,g,b,at)
        if g["status"]!="running":return view(g)
        if g["revision"]!=revision:raise HTTPException(409,"Posição mudou")
        if move not in b.legal_moves:raise HTTPException(422,"Lance ilegal")
        s=json.loads(g["settings"]);clocks=time_left(g,b,at);side=color_name(b.turn);clocks[side]+=s["increment"]
        san=b.san(move);b.push(move);moves=json.loads(g["moves"]);moves.append({"uci":move.uci(),"san":san,"clock":clocks[side],"side":side,"policy":metadata})
        outcome=b.outcome(claim_draw=True)
        c.execute("UPDATE bot_games SET moves=?,clocks=?,turn_started=?,revision=revision+1,status=?,result=?,reason=? WHERE id=?",(json.dumps(moves),json.dumps(clocks),at.isoformat(),"finished" if outcome else "running",outcome.result() if outcome else "*",outcome.termination.name if outcome else None,gid))
        return view(get_row(c,gid))
@router.post("/games/{gid}/move")
def move(gid:int,body:Command):
    with connect() as c:g=get_row(c,gid)
    b=board_for(g);s=json.loads(g["settings"])
    if g["revision"]!=body.revision or g["status"]!="running" or b.turn!=human(s):raise HTTPException(409,"Não é a vez do jogador")
    try:m=chess.Move.from_uci(body.uci or "")
    except ValueError:raise HTTPException(422,"Lance inválido")
    return commit_move(gid,body.revision,m)
@router.post("/games/{gid}/advance")
def advance(gid:int,body:Command):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");g=get_row(c,gid);b=board_for(g);g=finish_if_flag(c,g,b,timestamp())
    if g["status"]!="running":return view(g)
    s=json.loads(g["settings"])
    if g["revision"]!=body.revision or b.turn==human(s):raise HTTPException(409,"Não é a vez do motor")
    if not LOCK.acquire(blocking=False):raise HTTPException(409,"Motor ocupado")
    try:
        book,support=observed_book(b,s["profile"])
        scores=motor_candidates(b,s,time_left(g,b,timestamp())[color_name(b.turn)])
        policy=s["profile"] if s["profile"] not in ("mirror","nemesis") or book else "universal"
        chosen=choose_candidate(b,scores,{**s,"profile":policy},s["seed"]+g["revision"],book)
        return commit_move(gid,body.revision,chosen,{"policy":policy,"book_support":support,"candidate_count":len(scores)})
    finally:LOCK.release()
@router.post("/games/{gid}/resign")
def resign(gid:int,body:Command):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");g=get_row(c,gid)
        if g["revision"]!=body.revision:raise HTTPException(409,"Posição mudou")
        b=board_for(g);g=finish_if_flag(c,g,b,timestamp())
        if g["status"]=="running":
            result="0-1" if human(json.loads(g["settings"])) else "1-0"
            c.execute("UPDATE bot_games SET status='finished',result=?,reason='resignation',clocks=?,revision=revision+1 WHERE id=?",(result,json.dumps(time_left(g,b,timestamp())),gid))
        return view(get_row(c,gid))

def pgn_text(g):
    s=json.loads(g["settings"]);game=chess.pgn.Game();game.setup(chess.Board(g["initial_fen"]))
    bot="Chess Builder "+s["profile"];game.headers.update({"Event":"Local bot practice","White":"Você" if human(s) else bot,"Black":bot if human(s) else "Você","Result":g["result"],"Date":g["started_at"][:10].replace("-","."),"TimeControl":str(s["minutes"]*60)+"+"+str(s["increment"])})
    node=game
    for row in json.loads(g["moves"]):node=node.add_variation(chess.Move.from_uci(row["uci"]));node.set_clock(row["clock"])
    return str(game)+"\n"
@router.get("/games/{gid}/pgn",response_class=PlainTextResponse)
def export(gid:int):
    with connect() as c:return pgn_text(get_row(c,gid))
@router.post("/games/{gid}/save")
def save(gid:int):
    with connect() as c:g=get_row(c,gid)
    if g["status"]!="finished":raise HTTPException(409,"Finalize a partida")
    raw=pgn_text(g);game=chess.pgn.read_game(io.StringIO(raw));external="bot:"+str(gid)
    store(game,raw,"bot",external,username="Você")
    with connect() as c:
        rid=c.execute("SELECT id FROM games WHERE source='bot' AND external_id=?",(external,)).fetchone()[0]
        c.execute("UPDATE bot_games SET linked_game_id=? WHERE id=?",(rid,gid))
    return {"game_id":rid}
@router.get("/calibration")
def calibration():
    with connect() as c:return {"calibrated":False,"reports":[dict(r) for r in c.execute("SELECT * FROM bot_calibration ORDER BY id DESC LIMIT 50")],"note":"Não existem faixas empiricamente certificadas até executar o benchmark de referência."}
