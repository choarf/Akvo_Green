# Caja impresa de Akvo (Raspberry Pi 4 + HAT RS-485, riel DIN)

**Fecha:** 1 de noviembre de 2026
**Estado:** primer diseño para impresión 3D, sin imprimir ni probar. Reemplaza la caja comercial GeeekPi DIN del ensamble `akvo_rpi4_rs485_din_assembly .step`.

Dos variantes, del mismo script (`caja_akvo.py`):

| Variante | Medidas (ancho × alto × fondo) | Con clip DIN | Para |
|---|---|---|---|
| `oled` | **68.2 × 95.5 × 61.1 mm** | 66.2 de fondo | Con la OLED Waveshare 1.54" bajo una ventana de la tapa |
| `sin_oled` | **68.2 × 95.5 × 37.5 mm** | 42.6 de fondo | Sin pantalla: la tapa queda justo sobre las borneras RS-485 |

Las dos tienen aristas redondeadas (3 mm verticales, 1.5 mm en la cara de la tapa y el fondo), paredes de 2 mm y "GHOTRIX" (6 mm) y "AKVO" (4.5 mm) grabados en la tapa.

## Aberturas

| Abertura | Cara | Posición (mm, coordenadas del ensamble) | Para |
|---|---|---|---|
| Ethernet | y+ | x 11.7–28.7, z 7.1–21.6 | RJ45 del Pi |
| USB 3 / USB 2 | y+ | x 31.9–46.1 / 49.9–64.1, z 7.1–24.1 | Módem USB, memoria |
| USB-C | x+ | y 7.7–19.7, z 5.7–12.7 (12 × 7) | Alimentación 5 V, si no va por GPIO |
| RS-485 canal 1 / 2 | x+ | y 14.0–25.5 / 44.5–56.0, z 25.4–31.4 | Entrada de cables a las borneras del HAT |
| microSD | y− | x 24–52, bajo el Pi | Sacar la tarjeta con pinzas |
| Ranuras de tornillos | tapa | x 61–64.8 sobre cada bornera | Apretar las borneras sin abrir la caja |
| Ventilación | x− y tapa | ranuras de 1.6 mm | Ver pendiente 1 |
| Ventana OLED (`oled`) | tapa | x 16.5–53.5, y 35–55 | Pantalla |

Los micro-HDMI y el audio quedan cerrados (la pared interior pasa a 1.8 mm del borde del Pi para que quepan). Si hace falta HDMI para depurar, agregar su ventana a `W_OPEN`.

## Archivos

| Archivo | Contenido |
|---|---|
| `akvo_caja_<variante>_base.step` / `.stl` | Base: paredes, postes del Pi, postes de la tapa, torretas del clip y aberturas |
| `akvo_caja_<variante>_tapa.step` / `.stl` | Tapa con labio, ranuras, ventilación, logos y, en `oled`, ventana y columnas de la OLED |
| `akvo_caja_<variante>_clip_din.step` / `.stl` | Clip para riel TS35 |
| `akvo_caja_<variante>_ensamble.step` | La caja con el Pi, el HAT (y la OLED), para revisar |
| `revision_<variante>.txt` | Medidas, volúmenes y revisión de choques |
| `vista_<variante>_*.png` | Cerrada, interior, conectores (lado y red) y clip |
| `caja_akvo.py` | `freecad.cmd caja_akvo.py` hace `oled` (por defecto); `CAJA=sin_oled` antes del comando hace la otra. Las medidas están al inicio |
| `render_vistas.py` | `CAJA=... freecad render_vistas.py` (con interfaz): vistas `vista_<var>_*.png` y `akvo_caja_<var>.FCStd` con colores |

Las piezas están en las mismas coordenadas que `akvo_rpi4_rs485_din_assembly .step`. El script lee ese STEP para el ensamble y la revisión de choques, y deja fuera la caja GeeekPi, su soporte de la OLED, los pasacables y los cables.

## Cómo va armada

- **Pi:** sobre cuatro postes de la base (barrenos M2.5 en (13.5, 6), (62.5, 6), (13.5, 64), (62.5, 64)). **M2.5 × 12** desde abajo (cabeza en una cajera del fondo), a través del poste y del Pi, a un **separador hembra-hembra M2.5 de 16 mm** (la altura del modelo entre el Pi y el HAT). El HAT va encima con **M2.5 × 6**.
- **Tapa:** cuatro tornillos **M2.5 × 12 autorroscantes** a postes pegados a las paredes del lado GPIO (x−) y del lado de la microSD (y−). El lado de Ethernet/USB no tiene lugar para un poste. El labio de la tapa centra la tapa; en `sin_oled` no hay labio sobre las borneras.
- **OLED (`oled`):** atornillada por debajo a cuatro columnas de la tapa con **M2.5 × 5 autorroscantes**; el cristal queda contra la ventana. Ya no lleva los separadores ni el soporte de la GeeekPi. Del lado GPIO quedan 6.4 mm libres para sus cables.
- **Clip DIN:** el riel corre a lo largo del ancho (x), centrado con el Pi como en la GeeekPi. Se atornilla con dos **M3 × 6** a las torretas del fondo, entre los tornillos del Pi.

## Gabinete box_E (versión 2)

`akvo_box_E_yuhoho_AB_lte_v2.step` / `.FCStd` es `akvo_box_E_yuhoho_AB_lte.step` con la caja impresa (`oled`) en lugar de la GeeekPi, en el mismo riel DIN junto a las dos HDR-60. Lo arma `CAJA=oled freecad caja_en_box_E.py` (con interfaz; `CAJA=sin_oled` hace `..._v2_sin_oled`).

- Se quitaron la GeeekPi, el soporte y los separadores de la OLED, los tornillos del soporte, los pasacables y el Pi/HAT/OLED anteriores; entran la caja con el Pi, el HAT y la OLED de `akvo_rpi4_rs485_din_assembly .step`.
- La caja va en el mismo x, y que la GeeekPi (desplazamiento 156.4, 50.2, 24.8) con el clip centrado en el riel. Queda **1.8 mm más alta**, porque el clip va atornillado bajo el fondo; la extensión USB enchufada al Pi (codo, cable y lazo) subió lo mismo.
- **Sólo la antena LTE:** se quitaron el puck GNSS, su cable, su pasamuros y su pigtail. La antena LTE sigue en su pasamuros SMA de la pared, con el pigtail RG174 al conector principal del SIM7600. El barreno del pasamuros GNSS (6.6 mm) lleva un tapón (`tapon_barreno_gnss`); el conector GNSS del módem queda libre.
- `revision_box_E_v2_oled.txt`: **0 choques** con el gabinete, las fuentes, el interruptor, el módem SIM7600 y la antena. Vistas: `vista_box_E_v2_oled_{completo,interior,planta}.png`.

## Revisión

`revision_oled.txt` y `revision_sin_oled.txt`: **0 choques** entre la base o la tapa y los componentes del ensamble (Pi, HAT, OLED).

## Impresión

PETG o ASA (el Pi se calienta); base con el fondo sobre la cama, tapa boca abajo, clip acostado. Paredes de 2 mm: al menos 5 perímetros con boquilla de 0.4.

## Pendientes

1. **Temperatura:** un Pi 4 en caja cerrada de plástico puede bajar su frecuencia. Poner disipador en el SoC y medir `vcgencmd measure_temp` con la caja cerrada dentro del gabinete. Si hace falta, más ventilación o un ventilador de 30 mm en la tapa (`sin_oled`).
2. **No es hermética:** las aberturas están abiertas; en campo va dentro de un gabinete IP.
3. **Ventana de la OLED:** está centrada en el cristal; ajustar `OLED_WIN` al área activa real del módulo.
4. **microSD:** confirmar la posición de la ranura en el Pi real (el modelo no trae la tarjeta).
5. **USB-C:** la ventana es de 12 × 7 mm; revisar que entre el conector del cable usado.
6. **Clip DIN:** probar en riel real; las torretas son de 2.5 mm, revisar que el tornillo agarre.
7. **Postes de la tapa:** 12 mm de rosca en el plástico; si se abre seguido, usar insertos de latón M2.5.
8. **Logos:** son texto; no hay archivos SVG de logos.
