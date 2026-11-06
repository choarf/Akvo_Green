# Caja impresa para Akvo: Raspberry Pi 4 + HAT Waveshare RS-485 de 2 canales (+ OLED 1.54"), en riel DIN.
# Reemplaza la caja GeeekPi del ensamble akvo_rpi4_rs485_din_assembly .step. Genera base, tapa y clip DIN en STEP y STL.
# Dos variantes: CAJA=oled freecad.cmd caja_akvo.py       con la OLED bajo una ventana de la tapa (por omisión)
#                CAJA=sin_oled freecad.cmd caja_akvo.py   sin OLED: tapa justo sobre las borneras, caja baja
import FreeCAD as App, Part, Mesh, Import, os
from FreeCAD import Vector as V
HERE=os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
ASM=os.path.join(HERE,'akvo_rpi4_rs485_din_assembly .step')
VAR=os.environ.get('CAJA','oled')
OLED=(VAR=='oled')
# --- Parámetros (mm), en las coordenadas del ensamble: x a lo ancho (lado GPIO en x=10, USB-C/HDMI en x=66),
#     y a lo largo (tarjeta SD en y=2.5, Ethernet/USB en y=87.5), z hacia arriba (cara inferior del Pi en z=6.2)
PX0,PX1,PY0,PY1,PZ0=10.0,66.0,2.5,87.5,6.2
WALL,FLOOR,LID=2.0,2.0,2.0
HOLES=[(13.5,6.0),(62.5,6.0),(13.5,64.0),(62.5,64.0)]   # barrenos M2.5 del Pi 4 (y del HAT)
GAP_UNDER=3.5                            # bajo el Pi (pines THT)
SIDE_ROOM=6.4                            # espacio del lado GPIO (x-) para cables de la OLED y postes de la tapa
END_ROOM=6.0                             # espacio del lado de la tarjeta SD (y-) para postes de la tapa
TOP_IN=59.8 if OLED else 36.2            # cara interior de la tapa: cristal de la OLED en 59.5 / bornes en 35.2
POST_R,PILOT_R,SCREW_R,HEAD_R=2.6,1.05,1.45,2.6
R_OUT,R_IN,R_EDGE=3.0,1.0,1.5
LOGO_DEPTH=0.6
TERM_X=62.86                             # tornillos de las borneras (cabeza en z=35.2); los cables entran por x+ a z~28.4
TERM_Y=[16.26,19.76,23.26,46.74,50.24,53.74]
OLED_HOLES=[(16.0,29.67),(54.0,29.67),(16.0,62.17),(54.0,62.17)]
OLED_PCB_TOP=57.8
OLED_GLASS=(14.0,56.0,30.5,59.5)         # cristal de la OLED (x0,x1,y0,y1)
OLED_WIN=(16.5,53.5,35.0,55.0)           # ventana en la tapa: confirmar con el área activa del módulo
LOGOS=([('GHOTRIX',35.0,10.0,6.0),('AKVO',35.0,21.5,4.5)] if OLED else
       [('GHOTRIX',35.0,75.0,6.0),('AKVO',35.0,64.0,4.5)])
FONTS=['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf']
xi0,xi1=PX0-SIDE_ROOM,PX1+1.8           # micro-HDMI y audio sobresalen hasta x=67.5
yi0,yi1=PY0-END_ROOM,PY1+0.5
xo0,xo1=xi0-WALL,xi1+WALL; yo0,yo1=yi0-WALL,yi1+WALL
zf1=PZ0-GAP_UNDER; zf0=zf1-FLOOR
zl0,zl1=TOP_IN,TOP_IN+LID
# postes de la tapa, pegados a las paredes y fuera del Pi (el lado de Ethernet/USB no tiene lugar)
CORNERS=[(xi0+2.4,yi0+2.4),(xi1-2.4,yi0+2.4),(xi0+2.4,yi1-2.4),(xi0+2.4,(yi0+yi1)/2)]
CLIP_C=((xi0+xi1)/2,(PY0+PY1)/2)         # el riel corre a lo largo de x, centrado con el Pi como en la GeeekPi
CLIP_SCREWS=[(CLIP_C[0]-10,CLIP_C[1]-10),(CLIP_C[0]+10,CLIP_C[1]-10)]
BOSS_H=GAP_UNDER-1.0
def box(x0,x1,y0,y1,z0,z1): return Part.makeBox(x1-x0,y1-y0,z1-z0,V(x0,y0,z0))
def cylz(x,y,r,z0,z1): return Part.makeCylinder(r,z1-z0,V(x,y,z0))
def rbox(x0,x1,y0,y1,z0,z1,r):
    b=box(x0,x1,y0,y1,z0,z1)
    return b.makeFillet(r,[e for e in b.Edges if abs(e.Vertexes[0].Z-e.Vertexes[1].Z)>1e-6]) if r>0 else b
def round_face(s,z,r):
    return s.makeFillet(r,[e for e in s.Edges if abs(e.BoundBox.ZMin-z)<1e-6 and abs(e.BoundBox.ZMax-z)<1e-6])
def logo(text,lx,ly,lh):
    font=[f for f in FONTS if os.path.exists(f)][0]
    sh=Part.makeCompound([Part.makeFace(ch,'Part::FaceMakerBullseye') for ch in Part.makeWireString(text,font,lh,0.4) if ch])
    bb=sh.BoundBox; sh.translate(V(lx-bb.Center.x,ly-bb.Center.y,0)); return sh
# ---------------- BASE ----------------
base=round_face(rbox(xo0,xo1,yo0,yo1,zf0,zl0,R_OUT),zf0,R_EDGE).cut(rbox(xi0,xi1,yi0,yi1,zf1,zl0+1,R_IN))
for (x,y) in HOLES:                                       # postes del Pi: tornillo M2.5 desde abajo a un separador
    base=base.fuse(cylz(x,y,POST_R,zf1,PZ0))
for (x,y) in CORNERS:                                     # postes para atornillar la tapa
    base=base.fuse(cylz(x,y,POST_R,zf1,zl0-1.6)).cut(cylz(x,y,PILOT_R,zl0-1.6-12,zl0))
for (x,y) in CLIP_SCREWS:                                 # torretas para el clip DIN
    base=base.fuse(cylz(x,y,3.5,zf1,zf1+BOSS_H))
for (x,y) in HOLES:
    base=base.cut(cylz(x,y,SCREW_R,zf0-0.1,PZ0+0.1)).cut(cylz(x,y,HEAD_R+0.2,zf0-0.1,zf0+1.6))   # paso y cajera de la cabeza
for (x,y) in CLIP_SCREWS: base=base.cut(cylz(x,y,1.25,zf0-0.1,zf1+BOSS_H-0.5))
# ventanas: (nombre, pared, a0, a1, z0, z1); a es y en las paredes x y x en las paredes y
W_OPEN=[('Ethernet','y+',11.7,28.7,7.1,21.6),('USB 3','y+',31.9,46.1,7.1,24.1),('USB 2','y+',49.9,64.1,7.1,24.1),
        ('USB-C','x+',7.7,19.7,5.7,12.7),
        ('RS-485 canal 1','x+',14.0,25.5,25.4,31.4),('RS-485 canal 2','x+',44.5,56.0,25.4,31.4),
        ('microSD','y-',24.0,52.0,zf1+0.3,PZ0-0.1)]
for (_,w,a0,a1,z0,z1) in W_OPEN:
    if w=='x+': base=base.cut(box(xi1-1,xo1+1,a0,a1,z0,z1))
    elif w=='y+': base=base.cut(box(a0,a1,yi1-1,yo1+1,z0,z1))
    else: base=base.cut(box(a0,a1,yo0-1,yi0+1,z0,z1))
VENT_Y=[y for y in range(10,82,5) if abs(y-CORNERS[3][1])>5]
for y in VENT_Y:                                          # ventilación en la pared del lado GPIO
    base=base.cut(box(xo0-1,xi0+1,y-0.8,y+0.8,PZ0+4,min(zl0-6,PZ0+4+(36 if OLED else 22))))
# ---------------- TAPA ----------------
lid=round_face(rbox(xo0,xo1,yo0,yo1,zl0,zl1,R_OUT),zl1,R_EDGE)
lip=rbox(xi0+0.2,xi1-0.2,yi0+0.2,yi1-0.2,zl0-1.5,zl0,R_IN-0.2).cut(box(xi0+1.4,xi1-1.4,yi0+1.4,yi1-1.4,zl0-2,zl0+0.1))
if not OLED: lip=lip.cut(box(xi1-2,xi1+1,10.0,60.0,zl0-2,zl0+0.1))   # sin labio sobre las borneras
lid=lid.fuse(lip)
for (x,y) in CORNERS:
    lid=lid.fuse(cylz(x,y,POST_R,zl0-1.6,zl0).common(box(xi0+0.2,xi1-0.2,yi0+0.2,yi1-0.2,zl0-2,zl0)))
    lid=lid.cut(cylz(x,y,SCREW_R,zl0-1.7,zl1+0.1)).cut(cylz(x,y,HEAD_R,zl1-1.2,zl1+0.1))
for k in (0,3):                                           # ranura sobre los 3 tornillos de cada bornera
    lid=lid.cut(rbox(TERM_X-1.9,TERM_X+1.9,TERM_Y[k]-1.9,TERM_Y[k+2]+1.9,zl0-0.1,zl1+0.1,1.8))
if OLED:
    gx0,gx1,gy0,gy1=OLED_GLASS
    for (x,y) in OLED_HOLES:                              # columnas para la OLED (tornillo M2.5 desde abajo)
        lid=lid.fuse(cylz(x,y,2.3,OLED_PCB_TOP,zl0).cut(box(gx0-0.3,gx1+0.3,gy0-0.3,gy1+0.3,OLED_PCB_TOP-1,zl0)))
        lid=lid.cut(cylz(x,y,PILOT_R,OLED_PCB_TOP-0.1,zl1-0.8))
    wx0,wx1,wy0,wy1=OLED_WIN
    lid=lid.cut(rbox(wx0,wx1,wy0,wy1,zl0-0.1,zl1+0.1,1.0))
    vents=[(10.0,58.0,y) for y in (68,72,76,80,84)]       # sobre Ethernet/USB
else:
    vents=[(22.0,52.0,y) for y in range(8,52,4)]          # sobre el SoC
for (x0,x1,y) in vents: lid=lid.cut(rbox(x0,x1,y-0.8,y+0.8,zl0-0.1,zl1+0.1,0.75))
for L in LOGOS:
    lg=logo(*L); lg.translate(V(0,0,zl1-LOGO_DEPTH)); lid=lid.cut(lg.extrude(V(0,0,LOGO_DEPTH+0.1)))
# ---------------- CLIP DIN (TS35), riel a lo largo de x ----------------
cx,c=CLIP_C
zc1=zf0; zc0=zc1-2.5
clip=box(cx-15,cx+15,c-23,c+23,zc0,zc1)
clip=clip.fuse(box(cx-15,cx+15,c-19.8,c-17.8,zc0-2.6,zc0))                    # gancho fijo
clip=clip.fuse(box(cx-15,cx+15,c-19.8,c-15.8,zc0-2.6,zc0-1.2))
clip=clip.cut(box(cx-7,cx-6,c+4,c+23.1,zc0-0.1,zc1+0.1)).cut(box(cx+6,cx+7,c+4,c+23.1,zc0-0.1,zc1+0.1))  # brazo flexible
clip=clip.cut(box(cx-6,cx+6,c+4,c+23.1,zc0+1.2,zc1+0.1))
clip=clip.fuse(box(cx-6,cx+6,c+17.8,c+19.8,zc0-2.6,zc0)).fuse(box(cx-6,cx+6,c+15.8,c+19.8,zc0-2.6,zc0-1.2))  # pestillo
clip=clip.cut(box(cx-1.5,cx+1.5,c+20.8,c+22.3,zc0-0.1,zc1))                   # ranura para desarmador
for (x,y) in CLIP_SCREWS:
    clip=clip.cut(cylz(x,y,1.7,zc0-3,zc1+0.1)).cut(cylz(x,y,3.0,zc0-3,zc0+1.0))
# ---------------- salida ----------------
pre='akvo_caja_%s_'%VAR
parts={'base':base,'tapa':lid,'clip_din':clip}
for n,s in parts.items():
    s=s.removeSplitter(); parts[n]=s
    s.exportStep(os.path.join(HERE,pre+n+'.step'))
    m=Mesh.Mesh(); m.addFacets(s.tessellate(0.05)); m.write(os.path.join(HERE,pre+n+'.stl'))
# componentes del ensamble original, sin la caja GeeekPi, su soporte de la OLED, pasacables, cables y separadores de la OLED
SKIP=('geeekpi','oled_carrier','rs485_grommet','rs485_cable')+(() if OLED else ('waveshare_1in54_oled',))
OLED_STANDOFFS=['waveshare_1in54_oled%03d'%i for i in range(54,62)]
doc=App.newDocument('asm'); Import.insert(ASM,'asm')
comp=[]
for o in doc.Objects:
    if o.TypeId!='Part::Feature' or o.Shape.isNull(): continue
    if o.Label.startswith(SKIP) or o.Label in OLED_STANDOFFS: continue
    s=o.Shape
    sol=list(s.Solids) or [Part.Solid(sh) for sh in s.Shells if sh.isClosed()]
    comp+=[(o.Label,x) for x in sol]
Part.makeCompound(list(parts.values())+[x for _,x in comp]).exportStep(os.path.join(HERE,pre+'ensamble.step'))
# revisión de choques
rep=open(os.path.join(HERE,'revision_%s.txt'%VAR),'w')
bb=Part.makeCompound(list(parts.values())).BoundBox
rep.write('Variante: %s\nMedidas exteriores (mm): %.1f x %.1f x %.1f (con clip DIN)\n'%(VAR,bb.XLength,bb.YLength,bb.ZLength))
bb2=Part.makeCompound([parts['base'],parts['tapa']]).BoundBox
rep.write('Sin clip: %.1f x %.1f x %.1f\n'%(bb2.XLength,bb2.YLength,bb2.ZLength))
for n in parts: rep.write('%s volumen %.1f cm3\n'%(n,parts[n].Volume/1000))
tot=0
for lbl,so in comp:
    for n in ('base','tapa'):
        if so.BoundBox.intersect(parts[n].BoundBox):
            v=so.common(parts[n]).Volume
            if v>0.01: rep.write('CHOQUE %s con %s: %.2f mm3\n'%(n,lbl,v)); tot+=1
v=parts['base'].common(parts['tapa']).Volume
if v>0.01: rep.write('CHOQUE base con tapa: %.2f\n'%v); tot+=1
rep.write('componentes revisados: %d\nchoques: %d\n'%(len(comp),tot)); rep.close()
