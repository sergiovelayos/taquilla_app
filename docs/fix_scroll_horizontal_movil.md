# Fix: Scroll horizontal en tablas (móvil)

> **Fecha:** 2026-05-10  
> **Archivo afectado:** `webapp/templates/index.html`  
> **Tablas corregidas:** Ranking Cine Español / Toda la Cartelera · Ranking Anual Oficial ICAA — Cine Español

---

## Síntoma

En móvil no era posible hacer scroll horizontal en las tablas de la página principal (`/`). La tabla "Películas del Criterio Seleccionado" de la Calculadora de Subvenciones (`/calculadora`) sí funcionaba correctamente como referencia.

---

## Causa raíz (3 problemas combinados)

### 1. `overflow-y: auto` + `overflow-x: auto` en el contenedor — captura del gesto táctil

La clase `.table-scroll` tenía ambos ejes de overflow activados:

```css
.table-scroll { max-height: 520px; overflow-y: auto; overflow-x: auto; -webkit-overflow-scrolling: touch; }
```

En iOS y Android, cuando un scroll container tiene desplazamiento habilitado en los dos ejes, el navegador captura el gesto táctil y lo asigna al eje con más inercia inicial. Como las tablas tienen muchas filas (scroll-y disponible), el gesto horizontal era interpretado como scroll vertical. La tabla de la Calculadora usaba `table-responsive` de Bootstrap, que solo tiene `overflow-x: auto` sin `overflow-y`, evitando el conflicto.

### 2. Intento de fix con `overflow-y: visible` — inefectivo por el spec CSS

Se intentó corregir añadiendo en el media query de móvil:

```css
/* ❌ NO funciona */
.table-scroll { max-height: none !important; overflow-y: visible; }
```

Pero el spec CSS establece que si un eje de overflow es no-`visible`, el otro tampoco puede ser `visible` (se computa como `auto`). Al tener `overflow-x: auto`, el `overflow-y: visible` quedaba forzado a `auto`, sin efecto real.

### 3. `width: 100%` en la tabla + columnas ocultas — sin contenido horizontal que scrollear

Aunque se hubiera arreglado el contenedor, la tabla tenía `width: 100%` y las columnas extra estaban ocultas con `hide-mobile`. Sin overflow horizontal real, no habría nada que deslizar. La clase `hide-mobile` ocultaba en móvil: `%`, `Espectadores`, `Rec. Acum.`, `Esp. Acum.` (ranking semanal) y `Distribuidora`, `Estreno`, `Espectadores` (tabla ICAA).

---

## Solución aplicada

### CSS (media query `max-width: 767.98px`)

```css
/* ✅ overflow-y: hidden sí es compatible con overflow-x: auto (no aplica la regla del spec) */
.table-scroll { max-height: none !important; overflow-y: hidden; }
/* min-width fuerza el desbordamiento horizontal para que el scroll tenga contenido */
.ranking-table { min-width: 650px; }
```

- `overflow-y: hidden` con `max-height: none` → la tabla se muestra completa sin recortes; el navegador no puede capturar gestos como scroll vertical en el contenedor.
- `min-width: 650px` → la tabla desborda el ancho del móvil y genera overflow horizontal real.
- En escritorio, el comportamiento original (`max-height: 520px`, `overflow-y: auto`, sin `min-width`) se mantiene intacto.

### HTML — columnas visibles vía scroll

Se eliminó `hide-mobile` de todas las columnas que estaban ocultas en ambas tablas:

**Ranking semanal** (`<thead>` y `<tbody>`):
- `%`, `Espectadores`, `Rec. Acum.`, `Esp. Acum.`

**Tabla ICAA** (`<thead>` y filas generadas por JS en `anualTbody`):
- `Distribuidora`, `Estreno`, `Espectadores`

La clase `.hide-mobile { display: none !important; }` permanece definida en el CSS por si se usa en otros elementos de la página.

---

## Por qué funciona la Calculadora y no la página principal

| | `calculadora.html` | `index.html` (antes del fix) |
|---|---|---|
| Contenedor scroll | `<div class="table-responsive">` (Bootstrap) | `<div class="table-scroll">` (custom) |
| `overflow-x` | `auto` | `auto` |
| `overflow-y` | no definido (`visible` por defecto) | `auto` |
| `max-height` | ninguno | `520px` |
| Columnas en móvil | todas visibles | varias ocultas con `hide-mobile` |
| Scroll horizontal móvil | ✅ funciona | ❌ no funcionaba |
