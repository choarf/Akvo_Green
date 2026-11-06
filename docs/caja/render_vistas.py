# Vistas de la caja de Akvo + akvo_caja_<var>.FCStd: CAJA=oled|sin_oled freecad render_vistas.py   (con interfaz)
import os, FreeCAD as App, FreeCADGui as Gui, Part, ImportGui
from PySide import QtGui
D=os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
VAR=os.environ.get('CAJA','oled'); P=D+'/akvo_caja_%s_'%VAR
doc=App.newDocument("caja")
ImportGui.insert(D+"/akvo_rpi4_rs485_din_assembly .step","caja")
SKIP=('geeekpi','oled_carrier','rs485_grommet','rs485_cable')+(() if VAR=='oled' else ('waveshare_1in54_oled',))
for o in doc.Objects:
    if o.TypeId=='Part::Feature' and (o.Label.startswith(SKIP) or o.Label in ['waveshare_1in54_oled%03d'%i for i in range(54,62)]):
        o.Visibility=False
def add(name,f,color,tr=0):
    s=Part.Shape(); s.read(f); o=doc.addObject("Part::Feature",name); o.Shape=s
    o.ViewObject.ShapeColor=color; o.ViewObject.Transparency=tr; return o
base=add("base",P+"base.step",(0.80,0.82,0.85))
lid=add("tapa",P+"tapa.step",(0.80,0.82,0.85))
clip=add("clip",P+"clip_din.step",(0.25,0.45,0.75))
doc.recompute()
v=Gui.activeDocument().activeView(); v.setCameraType("Orthographic")
o=D+'/vista_%s_'%VAR
v.viewIsometric(); v.fitAll(); v.saveImage(o+"cerrada.png",1800,1350,"White")
lid.ViewObject.Transparency=75; base.ViewObject.Transparency=60
v.viewIsometric(); v.fitAll(); v.saveImage(o+"interior.png",1800,1350,"White")
lid.ViewObject.Transparency=0; base.ViewObject.Transparency=0
v.viewRight(); v.fitAll(); v.saveImage(o+"conectores_lado.png",1800,1100,"White")
v.viewRear(); v.fitAll(); v.saveImage(o+"conectores_red.png",1800,1100,"White")
v.setCameraOrientation(App.Rotation(App.Vector(0,0,1),180).multiply(App.Rotation(App.Vector(1,0,0),180+40)).Q); v.fitAll(); v.saveImage(o+"clip.png",1800,1350,"White")
doc.saveAs(D+"/akvo_caja_%s.FCStd"%VAR)
QtGui.QApplication.instance().quit()
