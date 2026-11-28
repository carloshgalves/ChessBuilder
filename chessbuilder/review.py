import json
import math
import chess
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import PlainTextResponse
from chessbuilder.db import connect
from chessbuilder.integrity import saved_sql,total_sql,complete_sql,valid_sql
router=APIRouter(prefix="/api")

METRIC_VERSION="win-loss-mean-v2"

def win_percent(score):
    mate=score.get("mate")
    if mate is not None: return 100.0 if mate>0 else 0.0
    cp=score.get("cp")
    if cp is None: return None
    return 100/(1+math.exp(-0.00368208*max(-10000,min(10000,cp))))

def expected_score(score):
    wdl=score.get("wdl")
    if wdl is None or len(wdl)!=3 or sum(wdl)<=0:
        return None
    return (wdl[0]+wdl[1]/2)/sum(wdl)

def metrics(payload):
    best,played=payload["best"],payload["played"]
    before,after=win_percent(best),win_percent(played)
    if before is None or after is None: return None
    loss=max(0,before-after)
    accuracy=100.0 if loss==0 else max(0,min(100,103.1668*math.exp(-0.04354*loss)-3.1669))
    cp=max(0,best["cp"]-played["cp"]) if best.get("cp") is not None and played.get("cp") is not None else None
    e_before,e_after=expected_score(best),expected_score(played)
    expected_loss=max(0,e_before-e_after) if e_before is not None and e_after is not None else None
    return {"accuracy":accuracy,"win_loss":loss,"cp_loss":cp,"expected_score_loss":expected_loss,"classification":"blunder" if loss>=20 else "mistake" if loss>=10 else "inaccuracy" if loss>=5 else "good","before":before,"after":after}

def mean(values):
    return round(sum(values)/len(values),2) if values else None

def summarize(analyses):
    result={}
    for color in ("white","black"):
        vals=[m for a in analyses if a["perspective"]==color and (m:=metrics(a)) is not None]
        result[color]={"accuracy":mean([m["accuracy"] for m in vals]),"acpl":mean([m["cp_loss"] for m in vals if m["cp_loss"] is not None]),"expected_score_loss":mean([m["expected_score_loss"] for m in vals if m["expected_score_loss"] is not None]),"analysed":len(vals)}
    return result

JOB_ORDER="CASE WHEN "+complete_sql()+" THEN 0 ELSE 1 END, "+saved_sql()+" DESC, CASE json_extract(j.settings,'$.level') WHEN 'detailed' THEN 2 WHEN 'deep' THEN 1 ELSE 0 END DESC, j.id DESC"
def select_job(c,gid):
    return c.execute("SELECT j.* FROM jobs j WHERE j.game_id=? AND "+saved_sql()+">0 ORDER BY "+JOB_ORDER+" LIMIT 1",(gid,)).fetchone()

def get_analyses(c,job):
    return {r["ply"]:json.loads(r["payload"]) for r in c.execute("SELECT a.ply,a.payload FROM move_analysis a JOIN jobs j ON j.id=a.job_id JOIN moves m ON m.game_id=j.game_id AND m.ply=a.ply WHERE a.job_id=? AND ("+valid_sql()+") ORDER BY a.ply",(job["id"],))} if job else {}

def filters(source=None,speed=None,color=None,result=None,opening=None,search=None,start=None,end=None,status=None,account_id=None):
    clauses=[];args=[]
    for column,value in (("source",source),("speed",speed),("result",result)):
        if value is not None and value!="": clauses.append("g."+column+"=?");args.append(value)
    if color:
        if account_id is None:clauses.append("g.player_color=?");args.append(color)
        else:clauses.append("EXISTS(SELECT 1 FROM game_accounts gc WHERE gc.game_id=g.id AND gc.account_id=? AND gc.player_color=?)");args.extend([account_id,color])
    if account_id is not None:
        clauses.append("EXISTS(SELECT 1 FROM game_accounts ga WHERE ga.game_id=g.id AND ga.account_id=?)");args.append(account_id)
    if opening:
        clauses.append("(g.opening LIKE ? OR g.eco LIKE ?)");args.extend(["%"+opening+"%"]*2)
    if search:
        clauses.append("(g.white LIKE ? OR g.black LIKE ?)");args.extend(["%"+search+"%"]*2)
    if start: clauses.append("g.played_at>=?");args.append(start)
    if end: clauses.append("substr(g.played_at,1,10)<=?");args.append(end)
    if status=="unanalysed": clauses.append("NOT EXISTS(SELECT 1 FROM jobs j WHERE j.game_id=g.id)")
    elif status: clauses.append("(SELECT status FROM jobs j WHERE j.game_id=g.id ORDER BY "+JOB_ORDER+" LIMIT 1)=?");args.append(status)
    return (" WHERE "+" AND ".join(clauses) if clauses else ""),args

@router.get("/games")
def games(source:str|None=None,speed:str|None=None,color:str|None=None,result:str|None=None,opening:str|None=None,search:str|None=None,start:str|None=None,end:str|None=None,status:str|None=None,account_id:int|None=None,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
    where,args=filters(source,speed,color,result,opening,search,start,end,status,account_id)
    with connect() as c:
        rows=c.execute("SELECT g.id,g.source,g.played_at,g.speed,g.white,g.black,g.white_rating,g.black_rating,g.result,g.player_color,g.opening,g.eco,(SELECT status FROM jobs j WHERE j.game_id=g.id ORDER BY "+JOB_ORDER+" LIMIT 1) AS analysis_status FROM games g"+where+" ORDER BY g.played_at DESC,g.id DESC LIMIT ? OFFSET ?",(*args,limit,offset))
        result=[dict(r) for r in rows]
        if account_id is not None:
            for r in result:
                r["player_color"]=c.execute("SELECT player_color FROM game_accounts WHERE game_id=? AND account_id=?",(r["id"],account_id)).fetchone()[0]
        return result

def line(board,pv):
    b=board.copy()
    result=[]
    for uci in pv:
        try:
            move=chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in b.legal_moves: break
        san=b.san(move);b.push(move)
        result.append({"uci":uci,"san":san,"fen":b.fen()})
    return result

@router.get("/games/{game_id}")
def detail(game_id:int):
    with connect() as c:
        c.execute("BEGIN")
        game=c.execute("SELECT * FROM games WHERE id=?",(game_id,)).fetchone()
        if not game: raise HTTPException(404,"Partida não encontrada")
        moves=[dict(r) for r in c.execute("SELECT * FROM moves WHERE game_id=? ORDER BY ply",(game_id,))]
        job=select_job(c,game_id)
        analyses=get_analyses(c,job)
        active=c.execute("SELECT id,status,error FROM jobs WHERE game_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",(game_id,)).fetchone()
        latest=c.execute("SELECT id,status,error FROM jobs WHERE game_id=? ORDER BY id DESC LIMIT 1",(game_id,)).fetchone()
    from chessbuilder.tactics import diagnose
    from chessbuilder.strategy import diagnose_strategy
    board=chess.Board(game["initial_fen"])
    for move in moves:
        a=analyses.get(move["ply"])
        move["analysis"]=a
        if a:
            move["metrics"]=metrics(a)
            move["diagnosis"]=diagnose(board,chess.Move.from_uci(move["uci"]),a)
            move["strategy"]=diagnose_strategy(board,chess.Move.from_uci(move["uci"]),a)
            move["best_line"]=line(board,a["best"]["pv"])
            move["played_line"]=line(board,a["played"]["pv"])
            move["variations"]=[line(board,v["pv"]) for v in a["alternatives"]]
        board.push_uci(move["uci"])
    return {"game":{k:game[k] for k in game.keys() if k!="pgn"},"moves":moves,"job":dict(job) if job else None,"metrics":summarize(analyses.values()),"metric_version":METRIC_VERSION,"coverage":{"saved":len(analyses),"total":len(moves),"status":"no_moves" if not moves else "complete" if len(analyses)==len(moves) else "partial" if analyses else "unanalysed"},"active_job":dict(active) if active else None,"latest_job":dict(latest) if latest else None}

@router.get("/games/{game_id}/pgn",response_class=PlainTextResponse)
def export(game_id:int):
    with connect() as c:
        row=c.execute("SELECT pgn FROM games WHERE id=?",(game_id,)).fetchone()
    if not row: raise HTTPException(404,"Partida não encontrada")
    return PlainTextResponse(row[0],headers={"Content-Disposition":f'attachment; filename="game-{game_id}.pgn"'})
