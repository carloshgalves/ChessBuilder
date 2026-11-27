"""Competence metrics and deterministic local training plans."""
import json
import math
from collections import defaultdict
from datetime import date,datetime,timedelta,timezone
import chess
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.review import select_job,get_analyses,filters,metrics
from chessbuilder.tactics import diagnose,phase
from chessbuilder.training import now,public_item

router=APIRouter(prefix="/api/coach")
VERSION="coach-v1"
MIN_SAMPLE=8

def wilson(fail,n):
    if not n:return None
    p=fail/n;z=1.96;den=1+z*z/n
    mid=(p+z*z/(2*n))/den
    radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [round(max(0,mid-radius),4),round(min(1,mid+radius),4)]

def game_observations(c,games):
    rows=[];coverage={"personal_games":len(games),"analysed_games":0,"own_plies":0,"analysed_plies":0,"witnessed_plies":0}
    for g in games:
        job=select_job(c,g["id"]);analyses=get_analyses(c,job)
        if analyses:coverage["analysed_games"]+=1
        b=chess.Board(g["initial_fen"])
        for r in c.execute("SELECT ply,uci FROM moves WHERE game_id=? ORDER BY ply",(g["id"],)):
            move=chess.Move.from_uci(r["uci"])
            color="white" if b.turn else "black"
            if color==g["player_color"]:
                coverage["own_plies"]+=1
                a=analyses.get(r["ply"])
                if a and a.get("perspective")==color:
                    m=metrics(a)
                    if m:
                        coverage["analysed_plies"]+=1
                        d=diagnose(b,move,a)
                        findings=[f for f in d["findings"] if f["confidence"]=="line_witness" and f["kind"] in ("missed","allowed","exploited")]
                        if findings:coverage["witnessed_plies"]+=1
                        rows.append({"game_id":g["id"],"ply":r["ply"],"date":g["played_at"],"speed":g["speed"],"phase":phase(b),"loss":m["win_loss"],"expected_loss":m["expected_score_loss"],"findings":findings})
            b.push(move)
    return rows,coverage

def aggregate(rows,reference_day=None):
    groups=defaultdict(list)
    if reference_day is None:
        dated=[date.fromisoformat(r["date"][:10]) for r in rows if r.get("date")]
        reference_day=max(dated) if dated else None
    for r in rows:
        by_theme=defaultdict(list)
        for f in r["findings"]:by_theme[f["theme"]].append(f)
        for theme,fs in by_theme.items():
            failed=any(f["kind"] in ("missed","allowed") for f in fs)
            # One opportunity per move/theme, regardless of overlapping witnesses.
            groups[(theme,r["phase"],r["speed"] or "unknown")].append((r,failed))
    result=[]
    for (theme,ph,speed),samples in groups.items():
        failures=[r for r,f in samples if f]
        n=len(samples);k=len(failures);interval=wilson(k,n)
        severity=sum(r["loss"] for r in failures)/k if k else 0
        expected=[r["expected_loss"] for r in failures if r["expected_loss"] is not None]
        games=len({r["game_id"] for r in failures})
        # Shrink small samples; opportunity-weighted loss is a priority index, not predicted rating gain.
        recent_weight=sum(2**(-max(0,(reference_day-date.fromisoformat(r["date"][:10])).days)/30) if reference_day and r.get("date") else 0.5 for r in failures)/k if k else 0
        priority=(k+1)/(n+4)*severity*math.log1p(games)*recent_weight
        result.append({"theme":theme,"phase":ph,"speed":speed,"opportunities":n,"failures":k,"successes":n-k,"failure_rate":round(k/n,4),"interval":interval,"confidence":"limited" if n<MIN_SAMPLE else "observed","mean_loss_pp":round(severity,2),"expected_score_loss":round(sum(expected)/len(expected),4) if expected else None,"expected_samples":len(expected),"distinct_error_games":games,"priority":round(priority,3),"recency_weight":round(recent_weight,3),"examples":[{"game_id":r["game_id"],"ply":r["ply"]} for r in sorted(failures,key=lambda r:-r["loss"])[:5]]})
    return sorted(result,key=lambda r:(-r["priority"],r["theme"],r["phase"],r["speed"]))

def phase_summary(rows):
    groups=defaultdict(list)
    for r in rows:groups[(r["phase"],r["speed"] or "unknown")].append(r)
    return [{"phase":ph,"speed":sp,"analysed":len(v),"mean_loss_pp":round(sum(r["loss"] for r in v)/len(v),2),"critical":sum(r["loss"]>=10 for r in v)} for (ph,sp),v in sorted(groups.items())]

def attempt_stats(c):
    rows=c.execute("""SELECT a.*,t.source,t.themes,t.fen FROM training_attempts a JOIN training_items t ON t.id=a.item_id WHERE a.finished_at IS NOT NULL ORDER BY a.finished_at,a.id""").fetchall()
    seen=set();first=[];repeat=[]
    for r in rows:
        position=" ".join(r["fen"].split()[:4])
        target=repeat if position in seen else first
        seen.add(position);target.append(r)
    def stats(xs):
        n=len(xs);k=sum(r["correct"]==1 and r["hints"]==0 for r in xs)
        return {"attempts":n,"unassisted_correct":k,"rate":round(k/n,4) if n else None}
    by_source={}
    for source in ("personal","lichess","endgame","opening"):
        by_source[source]={"novel":stats([r for r in first if r["source"]==source]),"repeat":stats([r for r in repeat if r["source"]==source])}
    last_by_position={};delayed=[]
    for r in rows:
        key=" ".join(r["fen"].split()[:4])
        when=datetime.fromisoformat(r["finished_at"])
        if key in last_by_position and (when-last_by_position[key]).total_seconds()>=3*86400:
            delayed.append(r)
        last_by_position[key]=when
    return {"novel":stats(first),"repeat":stats(repeat),"retention_3d":stats(delayed),"by_source":by_source,"unseen_external":by_source["lichess"]["novel"],"elapsed_minutes":round(sum(r["elapsed_ms"] or 0 for r in rows)/60000,1)}


def competitive_transfer(c,rows):
    """Descriptive before/after exposure cohorts, never a causal estimate."""
    practice=c.execute("""SELECT a.finished_at,t.themes,t.game_id FROM training_attempts a
        JOIN training_items t ON t.id=a.item_id
        WHERE a.correct=1 AND a.hints=0 AND a.finished_at IS NOT NULL
        ORDER BY a.finished_at,a.id""").fetchall()
    first={};source_games=defaultdict(set)
    for p in practice:
        when=date.fromisoformat(p["finished_at"][:10])
        for theme in json.loads(p["themes"]):
            if theme not in first:first[theme]=when
            if p["game_id"] is not None:source_games[theme].add(p["game_id"])
    cohorts=defaultdict(lambda:{"before":[],"after":[]})
    for row in rows:
        if not row.get("date"):continue
        played=date.fromisoformat(row["date"][:10])
        by_theme=defaultdict(list)
        for f in row["findings"]:by_theme[f["theme"]].append(f)
        for theme,findings in by_theme.items():
            if theme not in first or row["game_id"] in source_games[theme]:continue
            delta=(played-first[theme]).days
            if not delta or abs(delta)>30:continue
            bucket="before" if delta<0 else "after"
            cohorts[(theme,row["phase"],row["speed"] or "unknown")][bucket].append(any(f["kind"] in ("allowed","missed") for f in findings))
    result=[]
    for (theme,ph,speed),cohort in sorted(cohorts.items()):
        before,after=cohort["before"],cohort["after"]
        if not before or not after:continue
        result.append({"theme":theme,"phase":ph,"speed":speed,"before_n":len(before),"after_n":len(after),"before_rate":sum(before)/len(before),"after_rate":sum(after)/len(after),"confidence":"observed" if min(len(before),len(after))>=MIN_SAMPLE else "limited","first_practice":first[theme].isoformat()})
    return result

def read_games(c,speed,start,end,limit,offset=0):
    where,args=filters(speed=speed,start=start,end=end)
    return c.execute("SELECT g.* FROM games g"+where+(" AND " if where else " WHERE ")+"g.player_color IS NOT NULL ORDER BY g.played_at DESC,g.id DESC LIMIT ? OFFSET ?",(*args,limit,offset)).fetchall()

@router.get("/report")
def report(speed:str|None=None,days:int=Query(30,ge=1,le=3650),limit:int=Query(100,ge=1,le=1000),end:str|None=None,mode:str="period"):
    try: last=date.fromisoformat(end) if end else datetime.now(timezone.utc).date()
    except ValueError:raise HTTPException(422,"Data inválida")
    if mode not in ("period","latest"):raise HTTPException(422,"Modo inválido")
    start=last-timedelta(days=days-1);previous_end=start-timedelta(days=1);previous_start=previous_end-timedelta(days=days-1)
    with connect() as c:
        c.execute("BEGIN")
        if mode=="latest":
            current,coverage=game_observations(c,read_games(c,speed,None,last.isoformat(),limit))
            previous,previous_coverage=game_observations(c,read_games(c,speed,None,last.isoformat(),limit,offset=limit))
        else:
            current,coverage=game_observations(c,read_games(c,speed,start.isoformat(),last.isoformat(),limit))
            previous,previous_coverage=game_observations(c,read_games(c,speed,previous_start.isoformat(),previous_end.isoformat(),limit))
        competencies=aggregate(current,last);old=aggregate(previous,previous_end if mode=="period" else None)
        old_by={(r["theme"],r["phase"],r["speed"]):r for r in old}
        changes=[]
        for r in competencies:
            p=old_by.get((r["theme"],r["phase"],r["speed"]))
            if p and min(p["opportunities"],r["opportunities"])>=MIN_SAMPLE:
                changes.append({"theme":r["theme"],"phase":r["phase"],"speed":r["speed"],"current_n":r["opportunities"],"previous_n":p["opportunities"],"failure_rate_change":round(r["failure_rate"]-p["failure_rate"],4)})
        return {"version":VERSION,"period":{"mode":mode,"start":start.isoformat() if mode=="period" else None,"end":last.isoformat(),"limit":limit,"speed":speed},"coverage":coverage,"previous_coverage":previous_coverage,"competencies":competencies,"phases":phase_summary(current),"previous_phases":phase_summary(previous),"changes":changes,"strengths":[r for r in competencies if r["confidence"]=="observed" and r["interval"][1]<=0.35],"weaknesses":[r for r in competencies if r["confidence"]=="observed" and r["interval"][0]>=0.25],"practice":attempt_stats(c),"transfer":competitive_transfer(c,current+previous)}

class PlanRequest(BaseModel):
    minutes:int=Field(default=20,ge=5,le=180)
    speed:str|None=None
    goal:str=Field(default="general",pattern="^(general|tactics|defense|conversion|calculation)$")
    day:str|None=None

def select_plan(c,body,day):
    current,_=game_observations(c,read_games(c,body.speed,(day-timedelta(days=29)).isoformat(),day.isoformat(),100))
    priorities=aggregate(current,day)
    priority=defaultdict(float)
    for r in priorities:priority[r["theme"]]=max(priority[r["theme"]],r["priority"])
    from chessbuilder.repetition import sync_reviews
    sync_reviews(c)
    candidates=c.execute("""SELECT t.*,g.speed AS game_speed,s.due_at AS scheduled_due,
        (SELECT COUNT(*) FROM training_attempts a WHERE a.item_id=t.id AND a.finished_at IS NOT NULL) AS attempted,
        (SELECT MAX(a.finished_at) FROM training_attempts a WHERE a.item_id=t.id AND a.finished_at IS NOT NULL) AS last_attempt,
        (SELECT a.correct FROM training_attempts a WHERE a.item_id=t.id AND a.finished_at IS NOT NULL ORDER BY a.finished_at DESC,a.id DESC LIMIT 1) AS last_correct
        FROM training_items t LEFT JOIN games g ON g.id=t.game_id LEFT JOIN review_schedule s ON s.item_id=t.id WHERE t.active=1 AND (? IS NULL OR t.source IN ('lichess','endgame','opening') OR g.speed=?) ORDER BY t.id""",(body.speed,body.speed)).fetchall()
    ranking=[]
    for r in candidates:
        themes=json.loads(r["themes"]);minutes=8 if r["source"]=="endgame" else 4 if r["kind"] in ("calculation","conversion") else 3
        last=date.fromisoformat(r["last_attempt"][:10]) if r["last_attempt"] else None
        due=bool(r["scheduled_due"] and r["scheduled_due"][:10]<=day.isoformat()) if r["scheduled_due"] else bool(last and (day-last).days>=(3 if r["last_correct"] else 1))
        if last and not due:continue
        goal_match=body.goal=="general" or body.goal=="tactics" and any(t!="unclassified" for t in themes) or body.goal==r["kind"]
        score=(max([priority[t] for t in themes] or [0])+ (8 if due else 3)+(4 if goal_match else 0))
        ranking.append((r,minutes,score,due,themes))
    selected=[];remaining=body.minutes;used=defaultdict(int);seen_positions=set()
    while ranking:
        affordable=[x for x in ranking if x[1]<=remaining and " ".join(x[0]["fen"].split()[:4]) not in seen_positions]
        if not affordable:break
        best=max(affordable,key=lambda x:(x[2]-2*min([used[t] for t in x[4]] or [0]),-x[0]["id"]))
        ranking.remove(best);r,m,score,due,themes=best
        seen_positions.add(" ".join(r["fen"].split()[:4]))
        selected.append({"item_id":r["id"],"minutes":m,"reason":"review_due" if due else "new_practice","themes":themes,"priority":round(score,2)})
        for t in themes:used[t]+=1
        remaining-=m
    return {"version":VERSION,"goal":body.goal,"minutes":body.minutes,"allocated":body.minutes-remaining,"unallocated":remaining,"items":selected,"roadmap":[{"theme":r["theme"],"phase":r["phase"],"speed":r["speed"],"priority":r["priority"],"opportunities":r["opportunities"],"examples":r["examples"]} for r in priorities[:5]]}

def session_state(c,sid):
    s=c.execute("SELECT * FROM coach_sessions WHERE id=?",(sid,)).fetchone()
    if not s:raise HTTPException(404,"Sessão não encontrada")
    rows=c.execute("""SELECT si.*,a.status,a.correct,a.hints,t.active FROM coach_session_items si JOIN training_items t ON t.id=si.item_id LEFT JOIN training_attempts a ON a.id=si.attempt_id WHERE si.session_id=? ORDER BY si.ordinal""",(sid,)).fetchall()
    retired=sum(not r["active"] and r["attempt_id"] is None for r in rows)
    return {"id":sid,"day":s["day"],"completed_at":s["completed_at"],"plan":json.loads(s["plan"]),"items":[dict(r) for r in rows],"completed_items":sum(r["status"] in ("passed","failed","abandoned") for r in rows),"retired_items":retired}

@router.post("/sessions")
def create_session(body:PlanRequest):
    try:day=date.fromisoformat(body.day) if body.day else datetime.now(timezone.utc).date()
    except ValueError:raise HTTPException(422,"Data inválida")
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT id FROM coach_sessions WHERE day=? AND minutes=? AND speed IS ? AND goal=?",(day.isoformat(),body.minutes,body.speed,body.goal)).fetchone()
        if row:
            existing=session_state(c,row["id"])
            # An empty or wholly unstarted obsolete plan can be rebuilt.
            if existing["completed_at"] or any(x["attempt_id"] for x in existing["items"]) or (existing["items"] and not existing["retired_items"]):return existing
            sid=row["id"]
            c.execute("DELETE FROM coach_session_items WHERE session_id=?",(sid,))
            plan=select_plan(c,body,day)
            c.execute("UPDATE coach_sessions SET plan=? WHERE id=?",(json.dumps(plan),sid))
        else:
            plan=select_plan(c,body,day)
            sid=c.execute("INSERT INTO coach_sessions(day,minutes,speed,goal,plan,created_at) VALUES(?,?,?,?,?,?)",(day.isoformat(),body.minutes,body.speed,body.goal,json.dumps(plan),now())).lastrowid
        for i,x in enumerate(plan["items"]):c.execute("INSERT INTO coach_session_items(session_id,item_id,ordinal,minutes) VALUES(?,?,?,?)",(sid,x["item_id"],i,x["minutes"]))
        return session_state(c,sid)

@router.get("/sessions/{sid}")
def get_session(sid:int):
    with connect() as c:return session_state(c,sid)

@router.post("/sessions/{sid}/items/{item_id}/start")
def start_session_item(sid:int,item_id:int):
    from chessbuilder.training import state
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT si.*,s.completed_at FROM coach_session_items si JOIN coach_sessions s ON s.id=si.session_id WHERE si.session_id=? AND si.item_id=?",(sid,item_id)).fetchone()
        if not r:raise HTTPException(404,"Item de sessão não encontrado")
        t=c.execute("SELECT * FROM training_items WHERE id=?",(item_id,)).fetchone()
        if r["attempt_id"]:
            a=c.execute("SELECT * FROM training_attempts WHERE id=?",(r["attempt_id"],)).fetchone()
            return state(a,t)
        if r["completed_at"] or not t["active"]:raise HTTPException(409,"Exercício indisponível")
        aid=c.execute("INSERT INTO training_attempts(item_id,started_at) VALUES(?,?)",(item_id,now())).lastrowid
        c.execute("UPDATE coach_session_items SET attempt_id=? WHERE session_id=? AND item_id=?",(aid,sid,item_id))
        a=c.execute("SELECT * FROM training_attempts WHERE id=?",(aid,)).fetchone()
        return state(a,t)

@router.post("/sessions/{sid}/complete")
def complete_session(sid:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        result=session_state(c,sid)
        if not result["completed_items"] or result["completed_items"]+result["retired_items"]!=len(result["items"]):raise HTTPException(409,"Conclua os exercícios da sessão")
        c.execute("UPDATE coach_sessions SET completed_at=COALESCE(completed_at,?) WHERE id=?",(now(),sid))
        return session_state(c,sid)

@router.get("/adherence")
def adherence():
    with connect() as c:
        total,finished=c.execute("SELECT COUNT(*),COUNT(completed_at) FROM coach_sessions").fetchone()
        rows=c.execute("SELECT day,COUNT(*) AS planned,COUNT(completed_at) AS finished FROM coach_sessions GROUP BY day ORDER BY day DESC LIMIT 30").fetchall()
        planned,attempted,retired=c.execute("""SELECT COUNT(*),SUM(CASE WHEN a.finished_at IS NOT NULL THEN 1 ELSE 0 END),SUM(CASE WHEN t.active=0 AND si.attempt_id IS NULL THEN 1 ELSE 0 END) FROM coach_session_items si JOIN training_items t ON t.id=si.item_id LEFT JOIN training_attempts a ON a.id=si.attempt_id""").fetchone()
        return {"sessions":total,"completed":finished,"days":[dict(r) for r in rows],"planned_items":planned,"attempted_items":attempted or 0,"retired_items":retired or 0}
