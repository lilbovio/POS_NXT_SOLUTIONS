# Contexto del proyecto POS_NXT_SOLUTIONS

Este archivo resume el estado actual del proyecto para pasarlo a otra IA o desarrollador. Contiene información relevante sobre arquitectura, archivos clave, esquema de datos, endpoints, flujo de ejecución y comandos para ejecutar/compilar.

---

## Propósito

Aplicación Point of Sale (POS) de escritorio ligera empaquetada con PyWebView y PyInstaller. Backend en Flask con almacenamiento local en SQLite y datos iniciales importados desde un fichero Excel.

## Entorno
- OS objetivo: Windows (build-bat y rutas Windows).
- Lenguaje: Python 3.x
- Librerías principales: Flask, pandas, pywebview, werkzeug (hashing), pyinstaller (packaging).

## Estructura de archivos (raíz)

- `app.py` — arranca el servidor Flask en un hilo y abre una ventana nativa con PyWebView.
- `pos_backend.py` — implementación principal del backend (API REST, DB, importación Excel).
- `build.bat` — script de empaquetado usando PyInstaller (incluye plantillas, static y el Excel).
- `NXT_POS.spec` — spec de PyInstaller con `datas` configuradas.
- `requirements.txt` — dependencias del proyecto (no inspeccionado aquí, presente en repo).
- `templates/` — archivos HTML (ej. `index.html`).
- `static/` — `script.js`, `style.css`.
- `Base de datos Excel.xlsx` / `Basededatos_Actualizada.xlsx` — ficheros de datos; la importación actual usa `Basededatos_Actualizada.xlsx`.

## Comportamiento principal (resumen técnico)

- `app.py`:
  - Llama a `init_db()` y `import_excel_data()` antes de iniciar el servidor.
  - Arranca Flask en un `threading.Thread` (daemon) en `127.0.0.1:5000`.
  - Crea ventana PyWebView apuntando a `http://127.0.0.1:5000` y arranca el loop de UI.

- `pos_backend.py` (puntos clave):
  - Archivo de BD: `pos_database.db` (SQLite).
  - Excel esperado: `Basededatos_Actualizada.xlsx`.
  - Funciones principales:
    - `get_db()` — conexión SQLite con `row_factory`.
    - `init_db()` — crea tablas: `users`, `products`, `sales`, `sale_items`. Inserta usuarios por defecto:
      - `admin` / contraseña hasheada (valor inicial en código: `admin123`)
      - `caja` / `caja123` (rol `cashier`)
    - `import_excel_data()` — ahora puede recargar productos desde Excel aun si la tabla ya existe; busca nombres de columna de precio comunes (`PM`, `Precio venta`, `Precio`, `precio`, `precio venta`, `price`).
      - Actualiza producto existente con `descripcion` y `precio` nuevos.
      - Inserta nuevos productos si no existen.
      - Cuando se fuerza recarga (`force=True`), elimina productos que no aparecen en el Excel.
    - `reload_products` — nuevo endpoint `POST /api/admin/reload_products` para recargar inventario directamente desde el Excel.

## Esquema de la base de datos (resumen)

- `users` (id, username UNIQUE, password_hash, role)
- `products` (codigo PK, descripcion, precio, stock)
- `sales` (id, user_id FK users(id), total, timestamp, is_synced)
- `sale_items` (id, sale_id FK sales(id), product_codigo FK products(codigo), quantity, subtotal)

## Endpoints REST relevantes (entrada rápida)

- `GET /` — renderiza `index.html`.
- `POST /api/login` — autenticación (JSON: username, password).
- `GET /api/products` — listado o búsqueda (`q` param).
- `PUT /api/products/<codigo>/stock` — actualizar stock (admin).
- `PUT /api/products/<codigo>` — actualizar `precio`, `stock`, `descripcion`.
- `POST /api/sales` — registrar venta (items, total, user_id). Actualiza stock y crea `sales` + `sale_items`.
- `GET /api/sales/<int:sale_id>/items` — obtener items de una venta.
- `GET /api/receipt/<int:sale_id>` — obtener recibo digital.
- `GET /api/admin/income` — estadísticas e ingresos.
- `GET /api/admin/inventory` — inventario con filtros y orden.
- `POST /api/sync` — mark-as-synced mock (actualiza `is_synced` a 1).

## Packaging / Build

- `build.bat` ejecuta PyInstaller con opciones: `--noconfirm --onedir --windowed`, añade `templates`, `static` y `Base de datos Excel.xlsx` como `--add-data`, nombre `NXT_POS` y script `app.py`.
- `NXT_POS.spec` contiene configuración similar para PyInstaller (datas definidos).

## Credenciales por defecto (importante)

- Usuario admin: `admin` / contraseña original en código `admin123` (hasheada).
- Usuario caja: `caja` / contraseña original `caja123`.

## Requisitos del Excel

- Columnas esperadas (nombres usados en el código): `Codigo`, `Descripcion`, y una columna de precio válida.
- Columnas de precio aceptadas: `PM`, `Precio venta`, `Precio`, `precio`, `precio venta`, `price`.
- El precio se convierte a numérico; valores no válidos pasan a 0.

## Notas importantes y recomendaciones

- El servidor Flask se inicia en un hilo; por lo tanto para debugging local puede ser útil ejecutar `pos_backend.py` directamente o ejecutar `app.py` y revisar logs impresos en consola.
- Validaciones y manejo de errores en `import_excel_data()` son básicos; validar columnas en Excel antes de producción.
- El endpoint de sincronización (`/api/sync`) es un stub que solo marca ventas como sincronizadas; integrar con un backend remoto requiere implementación adicional.
- La UI admin ahora muestra la barra de navegación superior al entrar a Caja desde admin, sin duplicar botones de regreso.
- La vista de login se ha centrado en pantalla con `display:flex`, `align-items:center` y `justify-content:center`, pensado para uso en computadoras de escritorio, no móvil.

## Comandos útiles

Para ejecutar localmente durante desarrollo:

```
python app.py
```

Para generar el ejecutable (Windows) usando el script incluido:

```
build.bat
```

---

Si necesitas, puedo adaptar este `context.md` a un formato específico (prompt para IA, JSON, o plantilla más corta). Indica el formato deseado.
