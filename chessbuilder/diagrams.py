"""Local, review-first recognition of cropped book diagrams."""
import hashlib
import io
import json
import re
from typing import Literal
import chess
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,Field,model_validator
from chessbuilder.books import folder,material,candidate
from chessbuilder.db import connect

router=APIRouter(prefix="/api/diagrams")
VERSION="template-v1"
SIZE=32
def dependencies():
    try:
        import pypdfium2 as pdfium
        from PIL import Image
        import numpy as np
    except ImportError as exc:raise HTTPException(503,"Instale o extra diagrams: pip install -e '.[diagrams]'") from exc
    return pdfium,Image,np

class Region(BaseModel):
    page:int=Field(ge=1,le=500)
    x:float=Field(ge=0,lt=1)
    y:float=Field(ge=0,lt=1)
    width:float=Field(gt=.02,le=1)
    height:float=Field(gt=.02,le=1)
    orientation:Literal["white","black"]="white"
    @model_validator(mode="after")
    def inside(self):
        if self.x+self.width>1.000001 or self.y+self.height>1.000001:raise ValueError("Recorte fora da página")
        return self

class Calibrate(BaseModel):
    region:Region
    fen:str=Field(max_length=120)

class Recognize(BaseModel):
    region:Region
    candidate_id:int=Field(ge=1)
    turn:Literal["w","b"]
    calibration:str=Field(pattern="^[a-f0-9]{64}$")

def render(mid,page):
    pdfium,Image,np=dependencies()
    with connect() as c:m=material(c,mid)
    if m["format"]!="pdf" or not 1<=page<=m["pages"]:raise HTTPException(422,"Página PDF inválida")
    path=folder()/(m["digest"]+".pdf")
    if not path.is_file():raise HTTPException(404,"PDF indisponível")
    doc=pdfium.PdfDocument(str(path))
    try:
        pg=doc[page-1]
        try:
            w,h=pg.get_size()
            if min(w,h)<=0:raise ValueError("Página inválida")
            bitmap=pg.render(scale=min(2.5,2000/max(w,h)))
            try:return bitmap.to_pil().convert("L").copy()
            finally:bitmap.close()
        finally:pg.close()
    finally:doc.close()

def extract_cells(image,region):
    _,Image,np=dependencies()
    w,h=image.size
    crop=(round(region.x*w),round(region.y*h),round((region.x+region.width)*w),round((region.y+region.height)*h))
    cropped=image.crop(crop)
    cw,ch=cropped.size
    if min(cw,ch)<160 or not .85<=cw/ch<=1.15:raise HTTPException(422,"Selecione o quadrado interno de 8×8 casas (mínimo 160 pixels)")
    cells=[]
    for row in range(8):
        for col in range(8):
            tile=cropped.crop((round(col*cw/8),round(row*ch/8),round((col+1)*cw/8),round((row+1)*ch/8)))
            # Discard a narrow border to reduce printed grid interference.
            tw,th=tile.size;pad=max(1,round(min(tw,th)*.04))
            tile=tile.crop((pad,pad,tw-pad,th-pad)).resize((SIZE,SIZE),Image.Resampling.BILINEAR)
            cells.append(np.asarray(tile,dtype=np.float32).reshape(-1)/255)
    return cells

def square(i,orientation):
    return chess.square(7-i%8,i//8) if orientation=="black" else chess.square(i%8,7-i//8)
def parity(sq):return (chess.square_file(sq)+chess.square_rank(sq))%2
def make_templates(cells,fen,orientation):
    b=chess.Board(fen)
    if not b.is_valid():raise HTTPException(422,"FEN de calibração inválida")
    templates=[]
    for i,cell in enumerate(cells):
        sq=square(i,orientation);p=b.piece_at(sq)
        templates.append({"piece":p.symbol() if p else ".", "parity":parity(sq),"pixels":cell.tolist()})
    if len({t["piece"] for t in templates})<5:raise HTTPException(422,"Calibração precisa de maior variedade de peças")
    return templates

def classify(cells,templates,orientation,turn):
    _,_,np=dependencies()
    matches=[];board=chess.Board(None);uncertain=[]
    for i,cell in enumerate(cells):
        sq=square(i,orientation);distances={}
        for t in templates:
            if t["parity"]!=parity(sq):continue
            d=float(np.mean((cell-np.asarray(t["pixels"],dtype=np.float32))**2))
            distances[t["piece"]]=min(d,distances.get(t["piece"],float("inf")))
        ranked=sorted(distances.items(),key=lambda p:(p[1],p[0]))
        best,error=ranked[0] if ranked else (".",1)
        margin=ranked[1][1]-error if len(ranked)>1 else 0
        # Scores are residuals, not calibrated probabilities.
        accepted=len(ranked)>1 and error<=.018 and margin>=.002
        name=chess.square_name(sq)
        if accepted and best!=".":board.set_piece_at(sq,chess.Piece.from_symbol(best))
        if not accepted:uncertain.append(name)
        matches.append({"square":name,"piece":best if accepted else None,"error":round(error,5),"margin":round(margin,5)})
    board.turn=turn=="w";board.castling_rights=0;board.ep_square=None
    return {"fen":board.fen(),"uncertain":uncertain,"matches":matches,"legal_shape":board.is_valid(),"approved":False}

@router.get("/{mid}/page/{page}")
def page_image(mid:int,page:int):
    im=render(mid,page);out=io.BytesIO();im.save(out,format="PNG")
    return Response(out.getvalue(),media_type="image/png",headers={"Cache-Control":"no-store"})

def calibration_path(mid,digest):
    return folder()/f"diagram-{mid}-{digest}.json"

@router.post("/{mid}/calibrate")
def calibrate(mid:int,body:Calibrate):
    try:templates=make_templates(extract_cells(render(mid,body.region.page),body.region),body.fen,body.region.orientation)
    except ValueError as exc:raise HTTPException(422,"FEN inválida") from exc
    payload={"version":VERSION,"region":body.region.model_dump(),"fen":body.fen,"templates":templates}
    raw=json.dumps(payload,separators=(",",":")).encode();sha=hashlib.sha256(raw).hexdigest()
    path=calibration_path(mid,sha);path.write_bytes(raw)
    return {"calibration":sha,"classes":sorted({t["piece"] for t in templates})}

@router.post("/{mid}/recognize")
def recognize(mid:int,body:Recognize):
    with connect() as c:
        r=candidate(c,body.candidate_id)
        if r["material_id"]!=mid or r["status"]!="draft":raise HTTPException(409,"Selecione um rascunho do material")
        if r["page"]!=body.region.page:raise HTTPException(422,"Página do candidato não coincide")
    path=calibration_path(mid,body.calibration)
    if not path.is_file():raise HTTPException(404,"Calibração inexistente")
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=body.calibration:raise HTTPException(409,"Calibração alterada")
    payload=json.loads(raw)
    result=classify(extract_cells(render(mid,body.region.page),body.region),payload["templates"],body.region.orientation,body.turn)
    # No database changes here. The existing editor requires manual approval.
    return {**result,"provenance":{"material_id":mid,"candidate_id":body.candidate_id,"region":body.region.model_dump(),"calibration":body.calibration,"version":VERSION}}
