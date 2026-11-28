"""Legal PGN repertoire graph and shared recall attempts."""
import io
import json
import chess
import chess.pgn
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel,Field
from chessbuilder.db import connect
from chessbuilder.training import insert_item,digest
from chessbuilder.review import select_job,get_analyses

router=APIRouter(prefix="/api/repertoire")
CARO='''[Event "Caro-Kann: introdução"]
[Result "*"]

1. e4 c6 {Prepare d5.} 2. d4 d5 3. Nc3
(3. e5 Bf5 {Desenvolva o bispo antes de e6.} 4. Nf3 e6 5. Be2 c5)
(3. exd5 cxd5 4. Bd3 Nc6)
(3. Nd2 dxe4 4. Nxe4 Bf5)
3... dxe4 4. Nxe4 Bf5 5. Ng3 Bg6 *
'''
def key(b):return " ".join(b.fen(en_passant="legal").split()[:4])

def graph(pgn):
    nodes={};roots=[];stream=io.StringIO(pgn);n=0
    while (g:=chess.pgn.read_game(stream)) is not None:
        b=g.board()
        if g.errors or not b.is_valid() or b.chess960 or b.uci_variant!="chess":raise HTTPException(422,"PGN inválido ou variante não suportada")
        roots.append(key(b));stack=[(g,b)]
        while stack:
            node,b=stack.pop();k=key(b)
            record=nodes.setdefault(k,{"fen":b.fen(),"turn":"white" if b.turn else "black","comment":node.comment,"moves":{}})
            if node.comment and not record["comment"]:record["comment"]=node.comment
            for child in node.variations:
                m=child.move
                if m not in b.legal_moves:raise HTTPException(422,"Lance ilegal")
                after=b.copy();after.push(m);n+=1
                if n>20000:raise HTTPException(422,"Limite de 20.000 arestas")
                record["moves"][m.uci()]={"uci":m.uci(),"san":b.san(m),"to":key(after),"comment":child.comment}
                stack.append((child,after))
    if not n:raise HTTPException(422,"PGN sem linhas")
    return {"roots":list(dict.fromkeys(roots)),"nodes":nodes,"edges":sum(len(x["moves"]) for x in nodes.values())}

class Repertoire(BaseModel):
    name:str=Field(min_length=1,max_length=120)
    color:str=Field(pattern="^(white|black)$")
    pgn:str=Field(min_length=1,max_length=1000000)

def row(c,rid):
    r=c.execute("SELECT * FROM repertoires WHERE id=?",(rid,)).fetchone()
    if not r:raise HTTPException(404,"Repertório inexistente")
    return r
@router.get("")
def listing():
    with connect() as c:return [dict(r) for r in c.execute("SELECT id,name,color FROM repertoires ORDER BY id")]
@router.post("")
def create(body:Repertoire):
    graph(body.pgn)
    with connect() as c:return {"id":c.execute("INSERT INTO repertoires(name,color,pgn) VALUES(?,?,?)",(body.name,body.color,body.pgn)).lastrowid}
@router.post("/caro-kann")
def seed():
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r=c.execute("SELECT id FROM repertoires WHERE color='black' AND pgn=?",(CARO,)).fetchone()
        return {"id":r[0] if r else c.execute("INSERT INTO repertoires(name,color,pgn) VALUES(?,'black',?)",("Caro-Kann introdutória",CARO)).lastrowid}
@router.get("/{rid}")
def detail(rid:int):
    with connect() as c:r=dict(row(c,rid))
    return {**r,**graph(r["pgn"]),"provenance":"PGN do usuário ou exemplo introdutório original"}
@router.put("/{rid}")
def update(rid:int,body:Repertoire):
    graph(body.pgn)
    with connect() as c:
        c.execute("BEGIN IMMEDIATE");r=row(c,rid)
        c.execute("UPDATE repertoires SET name=?,color=?,pgn=? WHERE id=?",(body.name,body.color,body.pgn,rid))
        if r["color"]!=body.color or r["pgn"]!=body.pgn:c.execute("UPDATE training_items SET active=0 WHERE source='opening' AND json_extract(evidence,'$.repertoire_id')=?",(rid,))
    return {"id":rid}
@router.get("/{rid}/pgn",response_class=PlainTextResponse)
def export(rid:int):
    with connect() as c:return row(c,rid)["pgn"]

def lines(g,b,limit):
    result=[];color=b.turn
    def walk(b,path,seen):
        if len(result)>=256:return
        k=key(b);node=g["nodes"].get(k)
        if k in seen or len(path)>=limit or not node or not node["moves"]:
            if b.turn==color:path=path[:-1]
            if path:result.append(path)
            return
        for u in node["moves"]:
            child=b.copy();child.push_uci(u);walk(child,path+[u],seen|{k})
    walk(b,[],set())
    return list(dict.fromkeys(tuple(p) for p in result))
class Prepare(BaseModel):
    mode:str=Field(default="recall",pattern="^(recall|line)$")
    max_plies:int=Field(default=9,ge=3,le=19)
@router.post("/{rid}/prepare")
def prepare(rid:int,body:Prepare):
    with connect() as c:r=dict(row(c,rid))
    g=graph(r["pgn"]);version=digest([r["color"],r["pgn"]]);ids=[]
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        cur=row(c,rid)
        if digest([cur["color"],cur["pgn"]])!=version:raise HTTPException(409,"Repertório mudou")
        for k,n in g["nodes"].items():
            if n["turn"]!=r["color"] or not n["moves"]:continue
            b=chess.Board(n["fen"]);solutions=[[u] for u in n["moves"]] if body.mode=="recall" else [list(p) for p in lines(g,b,body.max_plies)]
            if not solutions:continue
            item_key=digest(["opening-v1",rid,version,k,body.mode,body.max_plies if body.mode=="line" else 1])
            insert_item(c,"opening",None,None,None,b,"opening_"+body.mode,["opening:"+str(rid)],solutions,{"repertoire_id":rid,"version":version,"comment":n["comment"],"moves":list(n["moves"].values())},None,item_key)
            c.execute("UPDATE training_items SET active=1 WHERE item_key=?",(item_key,))
            ids.append(c.execute("SELECT id FROM training_items WHERE item_key=?",(item_key,)).fetchone()[0])
    return {"item_ids":ids,"count":len(ids)}
@router.get("/{rid}/deviations")
def deviations(rid:int,limit:int=Query(100,ge=1,le=1000)):
    with connect() as c:
        r=dict(row(c,rid));g=graph(r["pgn"]);result=[]
        for game in c.execute("SELECT * FROM games WHERE player_color=? ORDER BY played_at DESC,id DESC LIMIT ?",(r["color"],limit)).fetchall():
            b=chess.Board(game["initial_fen"])
            if key(b) not in g["roots"]:continue
            analyses=get_analyses(c,select_job(c,game["id"]))
            for m in c.execute("SELECT ply,uci,san FROM moves WHERE game_id=? ORDER BY ply",(game["id"],)):
                n=g["nodes"].get(key(b))
                if not n or not n["moves"]:break
                side="white" if b.turn else "black";b.push_uci(m["uci"])
                if m["uci"] not in n["moves"]:
                    a=analyses.get(m["ply"])
                    result.append({"game_id":game["id"],"ply":m["ply"],"san":m["san"],"own":side==r["color"],"expected":[x["san"] for x in n["moves"].values()],"rejoins":key(b) in g["nodes"],"exit_score":a["played"] if a else None,"perspective":a["perspective"] if a else None})
                    if key(b) not in g["nodes"]:break
    return result
