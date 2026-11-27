from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from statistics import mean
import chess
from fastapi import APIRouter,HTTPException
from chessbuilder.db import connect
from chessbuilder.review import filters,select_job,get_analyses,metrics,METRIC_VERSION

router=APIRouter(prefix="/api")

def outcome(g):
    if not g["player_color"]: return None
    if g["result"]=="1/2-1/2": return "draw"
    if g["result"] not in ("1-0","0-1"): return None
    return "win" if (g["result"]=="1-0")== (g["player_color"]=="white") else "loss"

def rating(g,opponent=False):
    color=g["player_color"]
    if not color: return None
    if opponent: color="black" if color=="white" else "white"
    return g[color+"_rating"]

def opponent(g):
    return g["black" if g["player_color"]=="white" else "white"] if g["player_color"] else None

def avg(seq):
    return round(mean(seq),2) if seq else None

def grouped(games,key):
    groups=defaultdict(list)
    for g in games:
        k=key(g)
        if k is not None: groups[k].append(g)
    return sorted([{"name":k,"games":len(v),"wins":sum(outcome(x)=="win" for x in v),"losses":sum(outcome(x)=="loss" for x in v),"draws":sum(outcome(x)=="draw" for x in v),"game_id":v[-1]["id"]} for k,v in groups.items()],key=lambda x:(-x["games"],x["name"]))

def band(g):
    own,opp=rating(g),rating(g,True)
    if own is None or opp is None:return None
    return "Superior" if opp-own>100 else "Inferior" if own-opp>100 else "Equivalente"

def date(g):
    try: return datetime.fromisoformat(g["played_at"]).astimezone(timezone.utc) if g["played_at"] else None
    except ValueError: return None

def trajectory(games):
    points=[{"date":g["played_at"],"rating":rating(g),"game_id":g["id"]} for g in games if rating(g) is not None and date(g)]
    if not points:return {"points":[],"peak":None,"delta":None,"drawdown":None,"plateau":None}
    peak=points[0];best_drop=0;best_point=peak;drop_peak=peak
    for p in points:
        if p["rating"]>peak["rating"]:peak=p
        if peak["rating"]-p["rating"]>best_drop:best_drop=peak["rating"]-p["rating"];best_point=p;drop_peak=peak
    plateau=None
    for i in range(len(points)):
        if i+20<=len(points):
            segment=points[i:i+20]
            if max(x["rating"] for x in segment)-min(x["rating"] for x in segment)<=50:
                plateau={"start":segment[0]["date"],"end":segment[-1]["date"],"games":20,"range":50}
    return {"points":points,"peak":max(points,key=lambda p:p["rating"]),"delta":points[-1]["rating"]-points[0]["rating"],"drawdown":{"points":best_drop,"from":drop_peak,"to":best_point},"plateau":plateau}

def streaks(games):
    current=None;n=0;result={"win":0,"loss":0}
    for g in games:
        o=outcome(g)
        n=n+1 if o==current else 1
        current=o
        if o in result:result[o]=max(result[o],n)
    return result

def summarize_history(games,c):
    games=[dict(g) for g in games]
    own=[g for g in games if g["player_color"]]
    completed=[g for g in own if outcome(g) is not None]
    outcomes=Counter(outcome(g) for g in own)
    streams=defaultdict(list)
    for g in own: streams[(g["source"],g["speed"] or "unknown",g["account_id"])].append(g)
    ratings=[{"source":s,"speed":speed,"account_id":aid,**trajectory(gs)} for (s,speed,aid),gs in streams.items()]
    gaps=[]
    dated=[g for g in own if date(g)]
    for a,b in zip(dated,dated[1:]):
        days=(date(b)-date(a)).days
        if days>=14:gaps.append({"days":days,"from":a["played_at"],"to":b["played_at"],"game_id":b["id"]})
    acc=defaultdict(list);worst_move=None;captures=Counter();pieces=Counter();clock_values=[];mates=Counter();converted=opportunities=0;recovered=losing=0;timed_errors=timed_positions=0
    best_game=None
    for g in own:
        job=select_job(c,g["id"])
        analyses=get_analyses(c,job)
        game_acc=[];adv=False;disadv=False
        moves=c.execute("SELECT ply,san,clock,fen_after FROM moves WHERE game_id=? ORDER BY ply",(g["id"],)).fetchall()
        own_ply=1 if chess.Board(g["initial_fen"]).turn==(g["player_color"]=="white") else 0
        for row in moves:
            if row["ply"]%2!=own_ply:continue
            san=row["san"];piece=san[0] if san[0] in "KQRBN" else ("K" if san.startswith("O-O") else "P")
            pieces[piece]+=1
            if "x" in san:captures[piece]+=1
            if row["clock"] is not None:clock_values.append(row["clock"])
            a=analyses.get(row["ply"])
            if not a:continue
            m=metrics(a)
            if m is None:continue
            if a["perspective"]!=g["player_color"]:continue
            game_acc.append(m["accuracy"])
            if m["win_loss"]>0 and (worst_move is None or m["win_loss"]>worst_move["loss"]):
                worst_move={"game_id":g["id"],"ply":row["ply"],"san":san,"loss":m["win_loss"]}
            if m["before"]>=75:adv=True
            if m["before"]<=25:disadv=True
            if row["clock"] is not None and row["clock"]<=30:
                timed_positions+=1;timed_errors+=int(m["win_loss"]>=10)
        if moves:
            board=chess.Board(moves[-1]["fen_after"])
            if board.is_checkmate():mates["received" if board.turn==(g["player_color"]=="white") else "delivered"]+=1
        if adv and outcome(g):opportunities+=1;converted+=int(outcome(g)=="win")
        if disadv and outcome(g):losing+=1;recovered+=int(outcome(g)!="loss")
        if game_acc:
            acc[g["player_color"]].extend(game_acc);acc["all"].extend(game_acc)
            expected=sum(row["ply"]%2==own_ply for row in moves)
            if len(game_acc)==expected and outcome(g) is not None:
                candidate={"game_id":g["id"],"accuracy":avg(game_acc),"opponent":opponent(g)}
                if best_game is None or candidate["accuracy"]>best_game["accuracy"]:best_game=candidate
    best_win=max((g for g in own if outcome(g)=="win" and rating(g,True) is not None),key=lambda g:rating(g,True),default=None)
    if worst_move: worst_move["loss"]=round(worst_move["loss"],2)
    return {"total":len(games),"personal_games":len(own),"completed_personal_games":len(completed),"outcomes":{k:outcomes[k] for k in ("win","loss","draw")},"ratings":ratings,"gaps":sorted(gaps,key=lambda x:-x["days"])[:10],"streaks":streaks([g for g in completed if date(g)]),
        "colors":grouped(own,lambda g:g["player_color"]),"openings":grouped(own,lambda g:g["opening"] or g["eco"]),"opponents":grouped(own,opponent),"opponent_bands":grouped(own,band),
        "hours_utc":grouped(own,lambda g:f"{date(g).hour:02d}:00" if date(g) else None),"months":grouped(own,lambda g:g["played_at"][:7] if date(g) else None),"terminations":grouped(own,lambda g:g["termination"]),
        "accuracy":{k:avg(acc[k]) for k in ("all","white","black")},"analysed_plies":len(acc["all"]),"best_game":best_game,"worst_move":worst_move,
        "best_win":{"game_id":best_win["id"],"opponent":opponent(best_win),"rating":rating(best_win,True)} if best_win else None,
        "mates":{"received":mates["received"],"delivered":mates["delivered"]},"piece_moves":dict(pieces),"piece_captures":dict(captures),
        "clock":{"mean_remaining_seconds":avg(clock_values),"observations":len(clock_values),"critical_errors_under_30s":timed_errors,"analysed_under_30s":timed_positions},
        "conversion":{"wins":converted,"opportunities":opportunities},"recovery":{"non_losses":recovered,"opportunities":losing},"metric_version":METRIC_VERSION}

def parse_day(value):
    if not value:return None
    try:return datetime.strptime(value,"%Y-%m-%d")
    except ValueError:raise HTTPException(422,"Data inválida")

@router.get("/dashboard")
def dashboard(source:str|None=None,speed:str|None=None,account_id:int|None=None,start:str|None=None,end:str|None=None):
    a,b=parse_day(start),parse_day(end)
    if a and b and a>b:raise HTTPException(422,"Período inválido")
    where,args=filters(source=source,speed=speed,account_id=account_id,start=start,end=end)
    with connect() as c:
        c.execute("BEGIN")
        rows=c.execute("SELECT g.* FROM games g"+where+" ORDER BY g.played_at IS NULL,g.played_at,g.id",args).fetchall()
        result=summarize_history(rows,c)
        if a and b:
            duration=(b-a).days+1
            prev_start=(a-timedelta(days=duration)).date().isoformat();prev_end=(a-timedelta(days=1)).date().isoformat()
            w,p=filters(source=source,speed=speed,account_id=account_id,start=prev_start,end=prev_end)
            previous=c.execute("SELECT g.* FROM games g"+w+" ORDER BY g.played_at IS NULL,g.played_at,g.id",p).fetchall()
            s=summarize_history(previous,c)
            result["previous"]={"start":prev_start,"end":prev_end,"total":s["total"],"accuracy":s["accuracy"],"outcomes":s["outcomes"],"conversion":s["conversion"]}
    return result
