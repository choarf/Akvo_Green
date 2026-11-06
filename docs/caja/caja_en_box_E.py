# Nueva versión del gabinete akvo_box_E_yuhoho_AB_lte con la caja impresa (caja_akvo.py) en lugar de la GeeekPi.
#   CAJA=oled freecad caja_en_box_E.py       (con interfaz; por omisión oled)  -> akvo_box_E_yuhoho_AB_lte_v2.*
#   CAJA=sin_oled freecad caja_en_box_E.py                                      -> akvo_box_E_yuhoho_AB_lte_v2_sin_oled.*
# Quita del gabinete la GeeekPi, el soporte y separadores de la OLED, los pasacables y el Pi/HAT/OLED anteriores, y pone
# la caja con el Pi, el HAT (y la OLED) de akvo_rpi4_rs485_din_assembly .step, con el clip DIN centrado en el riel.
import os, FreeCAD as App, FreeCADGui as Gui, Part, ImportGui
from FreeCAD import Vector as V
from PySide import QtGui
D=os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
VAR=os.environ.get('CAJA','oled'); OLED=(VAR=='oled')
SRC=D+'/akvo_box_E_yuhoho_AB_lte.step'
ASM=D+'/akvo_rpi4_rs485_din_assembly .step'
OUT=D+'/akvo_box_E_yuhoho_AB_lte_v2'+('' if OLED else '_sin_oled')
# La GeeekPi está en (156.4, 50.2, 23.0) respecto al ensamble DIN, con su fondo sobre el riel (z=23.0). La caja impresa
# va en el mismo x, y; su clip (fondo en z=-1.8 en coordenadas del ensamble) apoya en el riel: 1.8 mm más alta.
ZC0=6.2-3.5-2.0-2.5                       # PZ0-GAP_UNDER-FLOOR-2.5 de caja_akvo.py
SHIFT=V(156.4,50.2,23.0-ZC0)
DZ=SHIFT.z-23.0
OLD=('geeekpi','oled_carrier','hat_standoffs','carrier_screws','waveshare_','rs485_grommet','rpi4_')
SKIP=('geeekpi','oled_carrier','rs485_grommet','rs485_cable')+(() if OLED else ('waveshare_1in54_oled',))
OLED_STANDOFFS=['waveshare_1in54_oled%03d'%i for i in range(54,62)]
MOVE_UP=('usb_ext_right_angle_plug','usb_ext_cable_run','usb_ext_cable_loop')   # la extensión USB enchufada al Pi
GNSS=('gnss_',)                           # sólo la antena LTE: se quita el puck GNSS, su cable, pasamuros y pigtail
doc=App.newDocument('box_E_v2')
def remove(objs):
    names=set()
    for o in objs:
        names.add(o.Name)
        if o.TypeId=='App::Part': names.update(x.Name for x in o.OutListRecursive)
    for n in names:
        if doc.getObject(n): doc.removeObject(n)
ImportGui.insert(SRC,doc.Name)
remove([o for o in doc.Objects if o.Label.startswith(OLD)])
gnss=[o for o in doc.Objects if o.Label.startswith(GNSS)]
gb=[o for o in gnss if o.Label=='gnss_sma_bulkhead']
gb=gb[0].Shape.BoundBox if gb else None
remove(gnss)
# tapón en el barreno del pasamuros GNSS, si la pared del gabinete lo tiene
plug_note='sin barreno GNSS en el gabinete'
if gb:
    hole=None
    for o in doc.Objects:
        if o.TypeId!='Part::Feature' or o.Shape.isNull() or not o.Shape.BoundBox.intersect(gb): continue
        for e in o.Shape.Edges:
            c=e.Curve
            if c.__class__.__name__=='Circle' and 2.5<c.Radius<6 and gb.isInside(c.Center):
                if hole is None or c.Radius<hole[1]: hole=(c.Center,c.Radius,c.Axis,o.Label)
    if hole:
        cen,r,ax,lbl=hole; ax=V(ax).normalize()
        out=ax if (cen+ax*5-V(144,95,70)).Length>(cen-ax*5-V(144,95,70)).Length else ax*-1   # hacia afuera del gabinete
        plug=Part.makeCylinder(r-0.1,8,cen-out*6,out).fuse(Part.makeCylinder(r+2.5,2,cen+out*2,out))
        po=doc.addObject('Part::Feature','tapon_gnss'); po.Label='tapon_barreno_gnss'; po.Shape=plug
        po.ViewObject.ShapeColor=(0.1,0.1,0.1)
        plug_note='tapón en el barreno GNSS de %s (diámetro %.1f mm)'%(lbl,2*r)
for o in doc.Objects:
    if o.Label in MOVE_UP:
        pl=o.Placement; pl.Base=pl.Base+V(0,0,DZ); o.Placement=pl
before=set(o.Name for o in doc.Objects)
ImportGui.insert(ASM,doc.Name)
new=[o for o in doc.Objects if o.Name not in before]
remove([o for o in new if o.TypeId=='Part::Feature' and (o.Label.startswith(SKIP) or o.Label in OLED_STANDOFFS)])
new=[o for o in doc.Objects if o.Name not in before]
root=[o for o in new if o.TypeId=='App::Part' and not [p for p in o.InList if p.TypeId=='App::Part']][0]
root.Label='AKVO_caja_impresa_'+VAR
for n,c in (('base',(0.80,0.82,0.85)),('tapa',(0.80,0.82,0.85)),('clip_din',(0.25,0.45,0.75))):
    s=Part.Shape(); s.read(D+'/akvo_caja_%s_%s.step'%(VAR,n))
    o=doc.addObject('Part::Feature','caja_'+n); o.Label='akvo_caja_'+n; o.Shape=s; o.ViewObject.ShapeColor=c
    root.addObject(o)
root.Placement=App.Placement(SHIFT,App.Rotation())
doc.recompute()
# revisión de choques: la caja (y el Pi, HAT, OLED movidos) contra todo lo demás del gabinete
def gshape(o):
    s=o.Shape.copy(); s.Placement=o.getGlobalPlacement(); return s
mine=[o for o in doc.Objects if o.TypeId=='Part::Feature' and o.Name not in before]
rest=[o for o in doc.Objects if o.TypeId=='Part::Feature' and o.Name in before]
rep=open(D+'/revision_box_E_v2_%s.txt'%VAR,'w')
rep.write('Antena: sólo LTE; %s\n'%plug_note)
rep.write('Gabinete box_E con la caja impresa, variante %s\nDesplazamiento de la caja: (%.1f, %.1f, %.1f) mm; %.1f mm más alta que la GeeekPi\n'%(VAR,SHIFT.x,SHIFT.y,SHIFT.z,DZ))
rs=[(o.Label,gshape(o)) for o in rest]
tot=0
for o in mine:
    a=gshape(o)
    for lbl,b in rs:
        if not a.BoundBox.intersect(b.BoundBox): continue
        try: v=a.common(b).Volume
        except Exception: continue
        if v>0.01: rep.write('CHOQUE %s con %s: %.2f mm3\n'%(o.Label,lbl,v)); tot+=1
rep.write('piezas revisadas: %d contra %d\nchoques: %d\n'%(len(mine),len(rest),tot)); rep.close()
tops=[o for o in doc.RootObjects if o.TypeId=='App::Part']
ImportGui.export(tops,OUT+'.step')
doc.saveAs(OUT+'.FCStd')
v=Gui.activeDocument().activeView(); v.setCameraType('Orthographic')
p=D+'/vista_box_E_v2_%s_'%VAR
v.viewIsometric(); v.fitAll(); v.saveImage(p+'completo.png',1800,1350,'White')
box=[o for o in doc.Objects if o.Label=='box_E']
for b in box: b.Visibility=False
v.viewTop(); v.fitAll(); v.saveImage(p+'planta.png',1800,1350,'White')
for o in doc.Objects:
    if o.TypeId=='App::Part' and o.Label.startswith(('fieldA','fieldB')): o.Visibility=False
v.viewIsometric(); v.fitAll(); v.saveImage(p+'interior.png',1800,1350,'White')
QtGui.QApplication.instance().quit()
