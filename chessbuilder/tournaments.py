"""Offline tournament planning, simulations and post-event records."""
import json
from collections import Counter
from datetime import date,datetime,timezone
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,model_validator
from chessbuilder.db import connect
from chessbuilder.coach import report,PlanRequest,create_session
from chessbuilder.importer import parse,store
from chessbuilder.bots import Settings,create as create_bot

router=APIRouter(prefix="/api/tournaments")
class Tournament(BaseModel):
    name:str=Field(min_length=1,max_length=160)
    start_date:date
    end_date:date
    minutes:int=Field(default=15,ge=1,le=180)
    increment:int=Field(default=10,ge=0,le=60)
    rounds:int=Field(default=7,ge=1,le=30)
    goal:str=Field(default="",max_length=1000)
    player:str=Field(min_length=1,max_length=160)
    initial_rating:int|None=Field(default=None,ge=0,le=4000)
    final_rating:int|None=Field(default=None,ge=0,le=4000)
    notes:str=Field(default="",max_length=4000)
    @model_validator(mode="after")
    def dates(self):
        if self.end_date<self.start_date:raise ValueError("Data final anterior à inicial")
        return self

def today():return datetime.now().astimezone().date()
def get(c,tid):
    t=c.execute("SELECT * FROM tournaments WHERE id=?",(tid,)).fetchone()
    if not t:raise HTTPException(404,"Torneio inexistente")
    return dict(t)

def values(body):
    return (body.name,body.start_date.isoformat(),body.end_date.isoformat(),body.minutes,body.increment,body.rounds,body.goal,body.player,body.initial_rating,body.final_rating,body.notes)

@router.get("")
def listing():
    with connect() as c:return [dict(r) for r in c.execute("SELECT * FROM tournaments ORDER BY start_date DESC,id DESC")]
@router.post("")
def create(body:Tournament):
    with connect() as c:
        tid=c.execute("INSERT INTO tournaments(name,start_date,end_date,minutes,increment,rounds,goal,player,initial_rating,final_rating,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",values(body)).lastrowid
        return get(c,tid)
@router.put("/{tid}")
def update(tid:int,body:Tournament):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");get(c,tid)
        max_round=c.execute("SELECT MAX(round) FROM tournament_rounds WHERE tournament_id=?",(tid,)).fetchone()[0] or 0
        if max_round>body.rounds:raise HTTPException(409,"Já há rodadas registradas além do novo limite")
        c.execute("UPDATE tournaments SET name=?,start_date=?,end_date=?,minutes=?,increment=?,rounds=?,goal=?,player=?,initial_rating=?,final_rating=?,notes=? WHERE id=?",(*values(body),tid))
        return get(c,tid)

@router.get("/{tid}")
def detail(tid:int):
    with connect() as c:
        t=get(c,tid);rows=[dict(r) for r in c.execute("SELECT * FROM tournament_rounds WHERE tournament_id=? ORDER BY round",(tid,))]
        sims=[dict(r) for r in c.execute("SELECT b.id,b.status,b.result,b.settings FROM tournament_simulations s JOIN bot_games b ON b.id=s.bot_game_id WHERE s.tournament_id=? ORDER BY b.id DESC",(tid,))]
    points=sum({"win":1,"draw":.5,"loss":0,"bye":.5}.get(r["result"],0) for r in rows)
    rated=[r for r in rows if r["result"] in ("win","draw","loss") and r["opponent_rating"] is not None]
    return {**t,"results":rows,"simulations":sims,"summary":{"rounds_recorded":len(rows),"points":points,"wins":sum(r["result"]=="win" for r in rows),"draws":sum(r["result"]=="draw" for r in rows),"losses":sum(r["result"]=="loss" for r in rows),"rated_opponents":len(rated),"average_opponent_rating":round(sum(r["opponent_rating"] for r in rated)/len(rated),1) if rated else None,"rating_change":t["final_rating"]-t["initial_rating"] if t["final_rating"] is not None and t["initial_rating"] is not None else None}}

class Round(BaseModel):
    opponent:str=Field(default="",max_length=160)
    color:str|None=Field(default=None,pattern="^(white|black)$")
    result:str=Field(pattern="^(win|draw|loss|bye|forfeit)$")
    opponent_rating:int|None=Field(default=None,ge=0,le=4000)
    notes:str=Field(default="",max_length=2000)
    pgn:str|None=Field(default=None,max_length=200000)
@router.put("/{tid}/rounds/{number}")
def record(tid:int,number:int,body:Round):
    with connect() as c:t=get(c,tid)
    if not 1<=number<=t["rounds"]:raise HTTPException(422,"Rodada fora do torneio")
    gid=None
    if body.pgn:
        games=list(parse(body.pgn))
        if len(games)!=1:raise HTTPException(422,"Informe o PGN de uma partida")
        g,raw=games[0]
        if g.errors:raise HTTPException(422,"PGN inválido")
        side=next((s for s in ("white","black") if g.headers.get(s.title(),"").casefold()==t["player"].casefold()),None)
        if not side or body.color!=side:raise HTTPException(422,"Cor/nome do jogador não coincide com PGN")
        opponent=g.headers.get("Black" if side=="white" else "White","")
        if body.opponent and opponent.casefold()!=body.opponent.casefold():raise HTTPException(422,"Adversário não coincide com PGN")
        actual="draw" if g.headers.get("Result")=="1/2-1/2" else "win" if g.headers.get("Result")==("1-0" if side=="white" else "0-1") else "loss" if g.headers.get("Result")==("0-1" if side=="white" else "1-0") else None
        if actual!=body.result:raise HTTPException(422,"Resultado não coincide com PGN")
        from chessbuilder.training import digest
        ext=digest(["otb",tid,number,raw])
        try:store(g,raw,"otb",ext,username=t["player"])
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        with connect() as c:gid=c.execute("SELECT id FROM games WHERE source='otb' AND external_id=?",(ext,)).fetchone()[0]
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");current=get(c,tid)
        if number>current["rounds"]:raise HTTPException(409,"Torneio alterado")
        previous=c.execute("SELECT game_id,result,color FROM tournament_rounds WHERE tournament_id=? AND round=?",(tid,number)).fetchone()
        if previous and previous["game_id"] and not gid and (previous["result"]!=body.result or previous["color"]!=body.color):raise HTTPException(422,"Forneça PGN corrigido para alterar resultado/cor de rodada vinculada")
        c.execute("""INSERT INTO tournament_rounds(tournament_id,round,opponent,color,result,opponent_rating,notes,game_id) VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(tournament_id,round) DO UPDATE SET opponent=excluded.opponent,color=excluded.color,result=excluded.result,opponent_rating=excluded.opponent_rating,notes=excluded.notes,game_id=COALESCE(excluded.game_id,tournament_rounds.game_id)""",(tid,number,body.opponent,body.color,body.result,body.opponent_rating,body.notes,gid))
    return detail(tid)

def speed_for(minutes,increment):
    sec=minutes*60+40*increment
    return "bullet" if sec<180 else "blitz" if sec<600 else "rapid" if sec<3600 else "classical"

@router.get("/{tid}/plan")
def plan(tid:int):
    with connect() as c:t=get(c,tid)
    days=(date.fromisoformat(t["start_date"])-today()).days
    speed=speed_for(t["minutes"],t["increment"])
    r=report(speed=speed,days=90,limit=200)
    top=r["competencies"][:5]
    if days<=0:calendar=[]
    else:
        calendar=[{"phase":"Diagnóstico e repertório","days":max(0,days-7),"focus":"Revisar temas recorrentes e preparar variantes"},
                  {"phase":"Simulação e conversão","days":min(5,max(0,days-2)),"focus":"Partidas no ritmo do torneio e finais críticos"},
                  {"phase":"Revisão leve","days":min(2,days),"focus":"Recordação, descanso e logística"}]
    return {"days_remaining":max(0,days),"preparation_open":days>0,"speed":speed,"calendar":calendar,"priorities":top,"coverage":r["coverage"],"minutes_recommended":30 if days>2 else 15,"note":"Preparação prévia e revisão posterior, sem assistência durante competição."}

class Preparation(BaseModel):
    minutes:int=Field(default=30,ge=5,le=180)
@router.post("/{tid}/session")
def session(tid:int,body:Preparation):
    with connect() as c:t=get(c,tid)
    if date.fromisoformat(t["start_date"])<=today():raise HTTPException(409,"Preparação específica disponível antes do torneio")
    return create_session(PlanRequest(minutes=body.minutes,speed=speed_for(t["minutes"],t["increment"]),goal="general",day=today().isoformat()))

class Simulation(BaseModel):
    profile:str="universal"
    strength:int=Field(default=1600,ge=800,le=2400)
    color:str="white"
@router.post("/{tid}/simulate")
def simulate(tid:int,body:Simulation):
    with connect() as c:t=get(c,tid)
    if date.fromisoformat(t["start_date"])<=today():raise HTTPException(409,"Simule antes do torneio")
    if t["minutes"]>120:raise HTTPException(422,"Motor suporta até 120 minutos")
    if body.profile not in ("universal","aggressive","solid","prophylactic","mirror","nemesis","explorer") or body.color not in ("white","black"):raise HTTPException(422,"Perfil ou cor inválida")
    bot=create_bot(Settings(profile=body.profile,strength=body.strength,human_color=body.color,minutes=t["minutes"],increment=t["increment"]))
    with connect() as c:c.execute("INSERT INTO tournament_simulations VALUES(?,?)",(tid,bot["id"]))
    return bot

@router.get("/{tid}/opponent")
def opponent(tid:int,name:str):
    with connect() as c:
        t=get(c,tid)
        if date.fromisoformat(t["start_date"])<=today():raise HTTPException(409,"Consulta específica restrita à preparação prévia")
        games=c.execute("SELECT id,white,black,result,opening,eco FROM games WHERE source='historical' AND (white=? COLLATE NOCASE OR black=? COLLATE NOCASE) ORDER BY played_at DESC LIMIT 500",(name,name)).fetchall()
    openings=Counter((g["opening"] or g["eco"] or "Desconhecida") for g in games)
    return {"sample":len(games),"openings":[{"name":k,"count":v} for k,v in openings.most_common(15)],"games":[dict(g) for g in games[:30]],"note":"Amostra local de partidas públicas previamente importadas; sem inferência quando indisponível."}
