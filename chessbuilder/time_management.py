"""Bounded, read-only time-management report from persisted game evidence."""
from collections import defaultdict
from statistics import median
from fastapi import APIRouter,Query
from chessbuilder.db import connect
from chessbuilder.review import filters,select_job,get_analyses,metrics
from chessbuilder.replay_timing import annotate_timing

router=APIRouter(prefix="/api")
BUCKETS=("under30","30to60","60to180","180plus")

def clock_bucket(value):
    return None if value is None else "under30" if value<30 else "30to60" if value<60 else "60to180" if value<180 else "180plus"

def summarize_time(rows):
    groups=defaultdict(list)
    for r in rows:groups[r["speed"] or "unknown"].append(r)
    report=[]
    for speed,rs in sorted(groups.items()):
        timed=[r for r in rs if r["elapsed_seconds"] is not None]
        assessed=[r for r in rs if r["loss_pp"] is not None]
        buckets=[]
        for b in BUCKETS:
            all_b=[r for r in rs if clock_bucket(r["clock_before"])==b]
            observed=[r for r in all_b if r["loss_pp"] is not None]
            critical=sum(r["loss_pp"]>=10 for r in observed)
            buckets.append({"bucket":b,"moves":len(all_b),"analysed":len(observed),"critical":critical,
                "critical_rate":round(critical/len(observed),4) if observed else None})
        report.append({"speed":speed,"moves":len(rs),"timed":len(timed),"explicit":sum(r["elapsed_source"]=="pgn_emt" for r in timed),
            "estimated":sum(r["elapsed_source"]=="clock_delta" for r in timed),"clock_before_known":sum(r["clock_before"] is not None for r in rs),
            "analysed":len(assessed),"median_seconds":round(median(r["elapsed_seconds"] for r in timed),2) if timed else None,
            "buckets":buckets,"slowest":sorted(timed,key=lambda r:(-r["elapsed_seconds"],r["game_id"],r["ply"]))[:5]})
    return report

@router.get("/time-management")
def time_management(source:str|None=None,speed:str|None=None,account_id:int|None=None,start:str|None=None,end:str|None=None,limit:int=Query(100,ge=1,le=200)):
    where,args=filters(source=source,speed=speed,account_id=account_id,start=start,end=end)
    personal="g.player_color IS NOT NULL" if account_id is None else "EXISTS(SELECT 1 FROM game_accounts ga WHERE ga.game_id=g.id AND ga.account_id=? AND ga.player_color IS NOT NULL)"
    clauses=where+(" AND " if where else " WHERE ")+personal+" AND g.source!='historical'"
    params=[*args]+([account_id] if account_id is not None else [])
    rows=[];games_with_time=0
    with connect() as c:
        c.execute("BEGIN")
        matched=c.execute("SELECT COUNT(*) FROM games g"+clauses,params).fetchone()[0]
        games=c.execute("SELECT g.* FROM games g"+clauses+" ORDER BY g.played_at IS NULL,g.played_at DESC,g.id DESC LIMIT ?",(*params,limit)).fetchall()
        for g in games:
            color=g["player_color"] if account_id is None else c.execute("SELECT player_color FROM game_accounts WHERE game_id=? AND account_id=?",(g["id"],account_id)).fetchone()[0]
            moves=[dict(m) for m in c.execute("SELECT ply,uci,san,clock FROM moves WHERE game_id=? ORDER BY ply",(g["id"],))]
            if not moves:continue
            annotate_timing(g,moves)
            a=get_analyses(c,select_job(c,g["id"]))
            known=False
            for m in moves:
                if m["color"]!=color:continue
                payload=a.get(m["ply"])
                metric=metrics(payload) if payload and payload["perspective"]==color else None
                rows.append({"game_id":g["id"],"ply":m["ply"],"san":m["san"],"speed":g["speed"],"clock_before":m["clock_before"],
                    "elapsed_seconds":m["elapsed_seconds"],"elapsed_source":m["elapsed_source"],"loss_pp":metric["win_loss"] if metric else None})
                known|=m["elapsed_seconds"] is not None
            games_with_time+=int(known)
    return {"matched_games":matched,"sample_games":len(games),"games_with_time":games_with_time,"groups":summarize_time(rows),
            "note":"Amostra das partidas mais recentes. Associação observacional, não causa demonstrada."}
