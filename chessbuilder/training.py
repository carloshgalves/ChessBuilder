"""Local exercise generation, curated puzzle ingestion and attempt state."""
import csv
import hashlib
import io
import json
from datetime import datetime,timezone
import chess
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.review import select_job,get_analyses,metrics,win_percent,filters
from chessbuilder.tactics import diagnose,phase,legal_line

router=APIRouter(prefix="/api/training")
VERSION="exercise-v1"
def now():return datetime.now(timezone.utc).isoformat()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def reconstruct(initial,history):
    b=chess.Board(initial)
    for uci in history:b.push_uci(uci)
    return b

def equivalent(best,candidate):
    # A winning mate must remain a winning mate; a forced loss must not
    # become shorter by more than one move.
    bm,cm=best.get("mate"),candidate.get("mate")
    if bm is not None:
        if bm>0:return cm is not None and 0<cm<=bm+1
        if bm<0:return cm is None or cm>0 or cm<=bm+1
    if cm is not None:return cm>0
    if best.get("cp") is None or candidate.get("cp") is None:return False
    return best["cp"]-candidate["cp"]<=30 and win_percent(best)-win_percent(candidate)<=2

def verified_solutions(board,payload,exclude=None):
    best=payload["best"]
    settings=payload.get("settings",{})
    if settings.get("level") not in ("deep","detailed"):return []
    if (best.get("depth") or 0)<10:return []
    result={}
    for score in [best,*payload.get("alternatives",[])]:
        pv=score.get("pv") or []
        if not pv or pv[0]==exclude or not legal_line(board,pv):continue
        if (score.get("depth") or 0)<10 or not equivalent(best,score):continue
        if pv[0] not in result:result[pv[0]]=pv
    return list(result.values())

def insert_item(c,source,game_id,ply,job_id,board,kind,themes,solutions,evidence,difficulty,key=None):
    history=[m.uci() for m in board.move_stack]
    initial=board.root().fen(en_passant="fen")
    key=key or digest([VERSION,source,initial,history,board.fen(),kind,solutions])
    c.execute("""INSERT OR IGNORE INTO training_items(item_key,source,game_id,ply,job_id,initial_fen,history,fen,kind,themes,phase,solution,evidence,difficulty,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(key,source,game_id,ply,job_id,initial,json.dumps(history),board.fen(),kind,json.dumps(sorted(set(themes))),phase(board),json.dumps(solutions),json.dumps(evidence),difficulty,now()))
    return c.execute("SELECT changes()").fetchone()[0]

class Generate(BaseModel):
    speed:str|None=None
    start:str|None=None
    end:str|None=None
    limit:int=Field(default=100,ge=1,le=1000)

@router.post("/generate")
def generate(body:Generate):
    added=eligible=unstable=0
    where,args=filters(speed=body.speed,start=body.start,end=body.end)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        games=c.execute("SELECT g.* FROM games g"+where+(" AND " if where else " WHERE ")+"g.player_color IS NOT NULL ORDER BY g.played_at DESC,g.id DESC LIMIT ?",(*args,body.limit)).fetchall()
        for g in games:
            job=select_job(c,g["id"])
            if not job:continue
            analyses=get_analyses(c,job)
            b=chess.Board(g["initial_fen"])
            for row in c.execute("SELECT ply,uci FROM moves WHERE game_id=? ORDER BY ply",(g["id"],)).fetchall():
                m=chess.Move.from_uci(row["uci"])
                a=analyses.get(row["ply"])
                if a and a["perspective"]==g["player_color"]:
                    met=metrics(a)
                    if met and met["win_loss"]>=5:
                        eligible+=1
                        solutions=verified_solutions(b,a,exclude=m.uci())
                        if not solutions:unstable+=1
                        else:
                            d=diagnose(b,m,a)
                            themes=[f["theme"] for f in d["findings"] if f["confidence"]=="line_witness" and f["kind"] in ("missed","allowed")]
                            kind="mate" if (a["best"].get("mate") or 0)>0 else "defense" if met["before"]<55 else "conversion" if met["before"]>=75 else "best_move"
                            evidence={"diagnosis":d,"loss_pp":met["win_loss"],"played":m.uci(),"analysis_digest":digest(a),"game_id":g["id"],"ply":row["ply"]}
                            # Only one active personal item per original ply, updated atomically.
                            key=digest([VERSION,g["id"],row["ply"],digest(a),kind])
                            c.execute("UPDATE training_items SET active=0 WHERE source='personal' AND game_id=? AND ply=? AND item_key<>?",(g["id"],row["ply"],key))
                            added+=insert_item(c,"personal",g["id"],row["ply"],job["id"],b,kind,themes or ["unclassified"],solutions,evidence,None,key)
                            c.execute("UPDATE training_items SET active=1 WHERE item_key=?",(key,))
                b.push(m)
    return {"added":added,"eligible":eligible,"unstable":unstable}

class PuzzleImport(BaseModel):
    csv_text:str=Field(min_length=1,max_length=10_000_000)
    theme:str|None=None
    limit:int=Field(default=500,ge=1,le=5000)

@router.post("/puzzles/import")
def import_puzzles(body:PuzzleImport):
    reader=csv.DictReader(io.StringIO(body.csv_text))
    required={"PuzzleId","FEN","Moves","Rating","Themes"}
    if not required.issubset(reader.fieldnames or []):raise HTTPException(422,"CSV Lichess sem cabeçalho obrigatório")
    added=skipped=invalid=selected=0
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for row in reader:
            if selected>=body.limit:break
            themes=(row.get("Themes") or "").split()
            if body.theme and body.theme not in themes:continue
            selected+=1
            try:
                b=chess.Board(row["FEN"]);pv=row["Moves"].split()
                if not b.is_valid() or len(pv)<2 or not legal_line(b,pv):raise ValueError("PV inválida")
                b.push_uci(pv[0]);solution=pv[1:]
                rating=int(row["Rating"])
                if not row["PuzzleId"]:raise ValueError("ID vazio")
                aliases={"discoveredAttack":"discovered_attack","defensiveMove":"defense","hangingPiece":"material","mateIn1":"mate","mateIn2":"mate","mateIn3":"mate","mateIn4":"mate","mateIn5":"mate","intermezzo":"zwischenzug","removalOfTheGuard":"remove_defender"}
                normalized=[aliases.get(t,t) for t in themes]
                n=insert_item(c,"lichess",None,None,None,b,"calculation",normalized,[solution],{"puzzle_id":row["PuzzleId"],"license":"CC0","url":"https://lichess.org/training/"+row["PuzzleId"]},rating,"lichess:"+row["PuzzleId"])
                added+=n;skipped+=1-n
            except (ValueError,TypeError,KeyError,AttributeError):invalid+=1
    return {"added":added,"skipped":skipped,"invalid":invalid}

def public_item(row):
    return {k:row[k] for k in ("id","source","game_id","ply","fen","kind","phase","difficulty","created_at")}|{"themes":json.loads(row["themes"]),"turn":"white" if chess.Board(row["fen"]).turn else "black"}

@router.get("/items")
def items(theme:str|None=None,source:str|None=None,start:str|None=None,end:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0)):
    clauses=["t.active=1"];args=[]
    if theme:clauses.append("EXISTS(SELECT 1 FROM json_each(t.themes) WHERE value=?)");args.append(theme)
    if source:clauses.append("t.source=?");args.append(source)
    if start:clauses.append("substr(COALESCE(g.played_at,t.created_at),1,10)>=?");args.append(start)
    if end:clauses.append("substr(COALESCE(g.played_at,t.created_at),1,10)<=?");args.append(end)
    with connect() as c:
        rows=c.execute("SELECT t.* FROM training_items t LEFT JOIN games g ON g.id=t.game_id WHERE "+" AND ".join(clauses)+" ORDER BY t.created_at DESC,t.id DESC LIMIT ? OFFSET ?",(*args,limit,offset)).fetchall()
        return [public_item(r) for r in rows]

def load_attempt(c,aid):
    a=c.execute("SELECT * FROM training_attempts WHERE id=?",(aid,)).fetchone()
    if not a:raise HTTPException(404,"Tentativa não encontrada")
    t=c.execute("SELECT * FROM training_items WHERE id=?",(a["item_id"],)).fetchone()
    return a,t

def state(a,t,reveal=False):
    answers=json.loads(a["answers"]);solutions=json.loads(t["solution"])
    b=reconstruct(t["initial_fen"],json.loads(t["history"]))
    for u in answers:b.push_uci(u)
    data={"id":a["id"],"item":public_item(t),"fen":b.fen(),"answers":answers,"status":a["status"],"hints":a["hints"],"legal_moves":[m.uci() for m in b.legal_moves] if a["status"]=="active" else [],"correct":a["correct"],"elapsed_ms":a["elapsed_ms"]}
    if reveal or a["status"]!="active":
        data["solutions"]=solutions;data["evidence"]=json.loads(t["evidence"])
    return data

@router.post("/items/{item_id}/start")
def start_attempt(item_id:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        t=c.execute("SELECT * FROM training_items WHERE id=? AND active=1",(item_id,)).fetchone()
        if not t:raise HTTPException(404,"Exercício não encontrado")
        a=c.execute("SELECT * FROM training_attempts WHERE item_id=? AND status='active' ORDER BY id DESC LIMIT 1",(item_id,)).fetchone()
        if not a:
            aid=c.execute("INSERT INTO training_attempts(item_id,started_at) VALUES(?,?)",(item_id,now())).lastrowid
            a=c.execute("SELECT * FROM training_attempts WHERE id=?",(aid,)).fetchone()
        return state(a,t)

class Answer(BaseModel):
    uci:str=Field(min_length=4,max_length=5)
    expected_ply:int=Field(ge=0)

def finish(c,a,status,answers):
    elapsed=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(a["started_at"])).total_seconds()*1000))
    c.execute("UPDATE training_attempts SET answers=?,status=?,correct=?,elapsed_ms=?,finished_at=? WHERE id=?",(json.dumps(answers),status,int(status=="passed"),elapsed,now(),a["id"]))

@router.post("/attempts/{aid}/answer")
def answer(aid:int,body:Answer):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        a,t=load_attempt(c,aid)
        if a["status"]!="active":raise HTTPException(409,"Tentativa encerrada")
        answers=json.loads(a["answers"])
        if len(answers)!=body.expected_ply:raise HTTPException(409,"Posição mudou")
        b=reconstruct(t["initial_fen"],json.loads(t["history"]))
        for u in answers:b.push_uci(u)
        try:m=chess.Move.from_uci(body.uci)
        except ValueError:raise HTTPException(422,"Lance inválido")
        if m not in b.legal_moves:raise HTTPException(422,"Lance ilegal")
        candidates=[pv for pv in json.loads(t["solution"]) if pv[:len(answers)]==answers and len(pv)>len(answers) and pv[len(answers)]==body.uci]
        answers.append(body.uci)
        if not candidates:finish(c,a,"failed",answers)
        elif t["source"]=="personal" or any(len(pv)==len(answers) for pv in candidates):
            finish(c,a,"passed",answers)
        else:
            # Imported Lichess puzzles contain the opponent response and next user move.
            chosen=candidates[0]
            if len(chosen)>len(answers):answers.append(chosen[len(answers)])
            if len(chosen)==len(answers):finish(c,a,"passed",answers)
            else:c.execute("UPDATE training_attempts SET answers=? WHERE id=?",(json.dumps(answers),aid))
        a,t=load_attempt(c,aid)
        return state(a,t)

@router.post("/attempts/{aid}/hint")
def hint(aid:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        a,t=load_attempt(c,aid)
        if a["status"]!="active":raise HTTPException(409,"Tentativa encerrada")
        answers=json.loads(a["answers"])
        pv=next(p for p in json.loads(t["solution"]) if p[:len(answers)]==answers and len(p)>len(answers))
        m=pv[len(answers)]
        hints=a["hints"]+1
        c.execute("UPDATE training_attempts SET hints=? WHERE id=?",(hints,aid))
        return {"hint":"Peça na casa "+m[:2] if hints==1 else "Destino "+m[2:4] if hints==2 else "Lance "+m,"hints":hints}

@router.post("/attempts/{aid}/reveal")
def reveal(aid:int):
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        a,t=load_attempt(c,aid)
        if a["status"]=="active":finish(c,a,"abandoned",json.loads(a["answers"]))
        a,t=load_attempt(c,aid)
        return state(a,t)
